#!/usr/bin/env python3
"""ThreadSanitizer gate over the REAL engine: build the engine_api_test unit driver with
-fsanitize=thread and run a real multi-file copy through the real CopyEngine/ListThread/
TransferThread stack. ASSERTS: rc==0 (also proves no TSan thread-registry CHECK crash),
dest byte-identical, and ZERO TSan reports touching our code.

WHY THE UNIT DRIVER, NOT THE FULL APP: the full ultracopier binary under TSan dies with
`CHECK failed: sanitizer_thread_registry.cpp` -- QtGui/offscreen + QThreadPool expired
threads exit in a way TSan's registry can't track (pthread_t reuse), a Qt-vs-TSan
incompatibility OUTSIDE our code. The unit driver hosts the identical engine threads
(ListThread + TransferThreads + reader/writer) without QtGui/tray/threadpool, so engine
races are fully visible and the run is stable. Established 2026-07-02: a real copy through
this gate is CLEAN (0 reports) -- the ~60 reports the first full-app run showed were all the
glib-eventfd wakeup false-positive class, suppressed in lib/tsan.supp.
CAVEAT (measured 2026-09-21): the suppression file's race:libQt6Core rule matches ANY frame
of either stack and every QThread bottoms out in libQt6Core, so the suppressed runs prove
"no crash, no deadlock, content identical" -- not "no race". The third sub-run therefore
drops the suppressions and DENIES the exact race shapes fixed on 2026-09-21 (an access from
the ListThread constructor = thread started before its members were initialised; an access
from newScanThread() = newCopy/newMove touching the scan pool off the list thread); the
structural false positives (dtor after QThread::wait(), queued-signal handoffs) are ignored.

Backend-independent gate (the async engine threads are the subject); runs once, not per
requested backend. Slowish (TSan build + ~5-15x runtime) -- excluded from --quick by timing.
"""
import sys, os, re, pathlib, subprocess, tempfile, shutil, glob
_CASES_DIR = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _CASES_DIR)]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K

_UNIT_DIR = pathlib.Path(__file__).resolve().parents[1] / "unit"
_PRO = _UNIT_DIR / "engine_api_test.pro"
_SUPP = pathlib.Path(__file__).resolve().parents[1] / "lib" / "tsan.supp"


def _qmake() -> str:
    return shutil.which("qmake6") or shutil.which("qmake") or "qmake6"


def _build() -> str:
    """Build (or incrementally re-make) the TSan-instrumented unit driver in a stable dir so
    rebuilds are cheap; a missing Makefile regenerates with the TSan flags baked in."""
    bdir = pathlib.Path(tempfile.gettempdir()) / "uc-tsan-engine-api"
    bdir.mkdir(parents=True, exist_ok=True)
    if not (bdir / "Makefile").exists():
        q = subprocess.run([_qmake(), "-o", str(bdir / "Makefile"), str(_PRO),
                            "-spec", "linux-g++", "CONFIG+=release", "CONFIG+=nodebug",
                            "QMAKE_CXXFLAGS+=-fsanitize=thread -fno-omit-frame-pointer -g",
                            "QMAKE_LFLAGS+=-fsanitize=thread"],
                           capture_output=True, text=True)
        if q.returncode != 0:
            raise RuntimeError("qmake failed:\n" + q.stderr[-2000:])
    m = subprocess.run(["make", "-C", str(bdir), f"-j{os.cpu_count()}"],
                       capture_output=True, text=True)
    binp = bdir / "engine_api_test"
    if m.returncode != 0 or not binp.exists():
        raise RuntimeError("make failed:\n" + (m.stderr or m.stdout)[-2000:])
    return str(binp)


def _libtsan() -> str:
    """The TSan runtime must precede any non-instrumented .so in LD_PRELOAD (else TSan aborts at
    startup), so the LD_PRELOAD fault shim is only usable with libtsan listed first."""
    for cand in ("libtsan.so", "libtsan.so.2", "libtsan.so.0"):
        try:
            pth = subprocess.run(["gcc", "-print-file-name=" + cand], capture_output=True, text=True).stdout.strip()
        except OSError:
            return ""
        if pth and pth != cand and os.path.exists(pth):
            return pth
    return ""


def _one(binp, src, dest, mode, extra_env, tag) -> bool:
    logdir = pathlib.Path(tempfile.mkdtemp(prefix="uc-tsan-gate-log-"))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", DISPLAY="",
               TSAN_OPTIONS=("halt_on_error=0:report_thread_leaks=0:"
                             f"suppressions={_SUPP}:log_path={logdir}/tsan"))
    env.update(extra_env)
    try:
        r, abort = K.run_tsan([binp, src, dest] + ([mode] if mode else []), env, 600, logdir)
    except subprocess.TimeoutExpired:
        print(f"    [tsan_engine_api {tag}] FAIL: timed out (livelock under TSan?)")
        shutil.rmtree(logdir, ignore_errors=True)
        return False
    problems = []
    if r.returncode != 0:
        problems.append(f"rc={r.returncode}; libtsan abort: {abort or 'none'}; "
                        f"stderr tail: {r.stderr[-200:]}")
    total = ours = 0
    our_sample = ""
    for f in glob.glob(str(logdir / "tsan*")):
        t = open(f, errors="ignore").read()
        for block in t.split("=================="):
            if "WARNING: ThreadSanitizer:" in block:
                total += 1
                if any(k in block for k in ("Ultracopier-Spec", "TransferThread", "ListThread")):
                    ours += 1
                    if not our_sample:
                        our_sample = "\n".join(block.strip().splitlines()[:10])
    if ours:
        problems.append(f"{ours} TSan report(s) touching OUR code (fix the race, never "
                        f"suppress it):\n{our_sample}")
    d = subprocess.run(["diff", "-rq", "--no-dereference", src,
                        os.path.join(dest, os.path.basename(src))],
                       capture_output=True, text=True)
    if d.returncode != 0:
        problems.append("dest not byte-identical:\n" + d.stdout[:300])
    print(f"      [{tag}] rc={r.returncode} tsan_reports={total} our_code={ours} diff_ok={d.returncode == 0}")
    shutil.rmtree(logdir, ignore_errors=True)
    if problems:
        print(f"    [tsan_engine_api {tag}] FAIL: " + "; ".join(problems))
        return False
    return True


def run(backends=None, memcheck=H.NONE) -> bool:
    try:
        binp = _build()
    except Exception as e:
        print(f"    [tsan_engine_api] BUILD FAILED: {e}")
        return False
    # 2. put-to-end on ONE inode thread: the deferred file's close handshake + the reschedule, watched by
    #    TSan on the real engine threads (the full app cannot: Qt registry CHECK). flaky:RECOVER:1 fails
    #    the RECOVER file's first read once -> deferred once -> its retry succeeds. Needs the shim, so
    #    libtsan is preloaded first.
    libtsan = _libtsan()
    if libtsan:
        src2 = K.fresh_src_root("tsan_pte_src")
        shutil.rmtree(src2, ignore_errors=True)
        os.makedirs(os.path.join(src2, "sub"))
        for i in range(6):
            with open(os.path.join(src2, f"good_{i}.bin"), "wb") as f:
                f.write(os.urandom(300 * 1024 + i))
        with open(os.path.join(src2, "sub", "bad_RECOVER.bin"), "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024))
        dest2 = K.fresh_dest("tsan_pte_dest")
        ok2 = _one(binp, src2, dest2, "puttoend",
                   {"LD_PRELOAD": f"{libtsan}:{K.fs_so()}", "UC_FS_SCENARIO": "flaky:RECOVER:1"}, "put-to-end")
    else:
        print("      [put-to-end] SKIP: libtsan not found for LD_PRELOAD")
        ok2 = True

    src = K.fresh_src_root("tsan_gate_src")
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(os.path.join(src, "sub"))
    with open(os.path.join(src, "a.bin"), "wb") as f:
        f.write(os.urandom(8 * 1024 * 1024))
    with open(os.path.join(src, "sub", "b.bin"), "wb") as f:
        f.write(os.urandom(8 * 1024 * 1024))
    dest = K.fresh_dest("tsan_gate_dest")
    logdir = pathlib.Path(tempfile.mkdtemp(prefix="uc-tsan-gate-log-"))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", DISPLAY="",
               TSAN_OPTIONS=("halt_on_error=0:report_thread_leaks=0:"
                             f"suppressions={_SUPP}:log_path={logdir}/tsan"))
    try:
        r, abort = K.run_tsan([binp, src, dest], env, 600, logdir)
    except subprocess.TimeoutExpired:
        print("    [tsan_engine_api] FAIL: timed out (livelock under TSan?)")
        shutil.rmtree(logdir, ignore_errors=True)
        return False

    problems = []
    if r.returncode != 0:
        problems.append(f"rc={r.returncode}; libtsan abort: {abort or 'none'}; "
                        f"stderr tail: {r.stderr[-200:]}")
    total = ours = 0
    our_sample = ""
    for f in glob.glob(str(logdir / "tsan*")):
        t = open(f, errors="ignore").read()
        for block in t.split("=================="):
            if "WARNING: ThreadSanitizer:" in block:
                total += 1
                if any(k in block for k in ("Ultracopier-Spec", "TransferThread", "ListThread")):
                    ours += 1
                    if not our_sample:
                        our_sample = "\n".join(block.strip().splitlines()[:10])
    if ours:
        problems.append(f"{ours} TSan report(s) touching OUR code (fix the race, never "
                        f"suppress it):\n{our_sample}")
    d = subprocess.run(["diff", "-rq", "--no-dereference", src,
                        os.path.join(dest, os.path.basename(src))],
                       capture_output=True, text=True)
    if d.returncode != 0:
        problems.append("dest not byte-identical:\n" + d.stdout[:300])
    print(f"      [plain] rc={r.returncode} tsan_reports={total} our_code={ours} diff_ok={d.returncode == 0}")
    shutil.rmtree(logdir, ignore_errors=True)
    if problems:
        print("    [tsan_engine_api plain] FAIL: " + "; ".join(problems))
        return False
    if not ok2:
        return False
    if not _denylist_run(binp, src):
        return False
    print("    [tsan_engine_api] PASS  (real engine copy + put-to-end TSan-clean, denylist clean)")
    return True


_DENIED = ("ListThread::ListThread(", "ListThread::newScanThread(")


def _denylist_run(binp, src) -> bool:
    """Unsuppressed copy: only the ACCESS stacks of each report are inspected (thread-creation
    stacks legitimately name the constructor). rc is 66 whenever any report exists, so completion
    is judged by the driver's DONE line and the absence of a registry CHECK crash."""
    dest = K.fresh_dest("tsan_deny_dest")
    logdir = pathlib.Path(tempfile.mkdtemp(prefix="uc-tsan-deny-log-"))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", DISPLAY="",
               TSAN_OPTIONS=f"halt_on_error=0:report_thread_leaks=0:log_path={logdir}/tsan")
    try:
        r, abort = K.run_tsan([binp, src, dest], env, 600, logdir)
    except subprocess.TimeoutExpired:
        print("    [tsan_engine_api denylist] FAIL: timed out")
        shutil.rmtree(logdir, ignore_errors=True)
        return False
    problems, hits, total = [], [], 0
    if "DONE done=1" not in r.stdout:
        problems.append(f"driver did not complete: stdout tail {r.stdout[-120:]!r}; libtsan abort: {abort or 'none'}")
    for f in glob.glob(str(logdir / "tsan*")):
        text = open(f, errors="ignore").read()
        for block in text.split("=================="):
            if "WARNING: ThreadSanitizer:" in block:
                total += 1
                access = re.split(r"\n\s+(?:Location is|Thread T\d+|Mutex M\d+)", block)[0]
                for d in _DENIED:
                    if d in access:
                        hits.append(d + "\n" + "\n".join(block.strip().splitlines()[:8]))
    if hits:
        problems.append(f"{len(hits)} denied race shape(s):\n" + hits[0])
    print(f"      [denylist] tsan_reports={total} denied_hits={len(hits)} done={'DONE done=1' in r.stdout}")
    shutil.rmtree(logdir, ignore_errors=True)
    if problems:
        print("    [tsan_engine_api denylist] FAIL: " + "; ".join(problems))
        return False
    return True


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
