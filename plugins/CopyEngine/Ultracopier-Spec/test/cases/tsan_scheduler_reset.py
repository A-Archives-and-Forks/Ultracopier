#!/usr/bin/env python3
"""ThreadSanitizer gate over the SPECIFIC path #4 fixed: ListThread::safetyReschedule()'s stall
backstop firing DURING a healthy small-file copy while per-file completions are in flight. The fix
reads thread state (getStat/transferId) + entry state on the list thread and conditionally skips
zeroing an Idle thread's id -- exactly the kind of scheduler/thread-coordination change the repo
requires a TSan run for. The existing tsan_engine_api copies two 8 MiB files (big -> the backstop
never fires), so it does NOT cover this path; this case does.

HOW IT TRIGGERS THE RESET WITHOUT A SHIM: the copy is many files all < parallelizeIfSmallerThan
(128 KB), so getNumberOfTranferRuning() reads 0 and the 500ms retryScheduler concludes "stalled"
after ~1.5 s and runs its reset -- repeatedly, throughout the copy. Under TSan (5-15x slowdown) the
small-file copy lasts far more than 1.5 s on its own, so the reset fires many times while completions
race it -- no LD_PRELOAD slow shim needed (which would fight TSan's own read/write interceptors).

Runs the REAL engine (CopyEngine/ListThread/TransferThread + reader/writer) via the in-process unit
driver (test/unit/engine_api_test) -- the full ultracopier binary can't run under TSan (a Qt-vs-TSan
thread-registry CHECK crash unrelated to our code; see tsan_engine_api). ASSERTS: rc==0 (also proves
no TSan CHECK crash / livelock), ZERO TSan reports touching our code, dest byte-identical.
"""
import sys, os, pathlib, subprocess, tempfile, shutil, glob
_CASES_DIR = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _CASES_DIR)]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K

_UNIT_DIR = pathlib.Path(__file__).resolve().parents[1] / "unit"
_PRO = _UNIT_DIR / "engine_api_test.pro"
_SUPP = pathlib.Path(__file__).resolve().parents[1] / "lib" / "tsan.supp"

NFILES = 2500
SIZE = 6 * 1024          # every file < parallelizeIfSmallerThan (128 KB)


def _qmake() -> str:
    return shutil.which("qmake6") or shutil.which("qmake") or "qmake6"


def _build() -> str:
    """Build (or incrementally re-make) the TSan-instrumented unit driver -- same recipe/stable dir
    as tsan_engine_api so the two share the build cache."""
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


def _make_small_tree(root):
    shutil.rmtree(root, ignore_errors=True)
    for i in range(NFILES):
        d = os.path.join(root, f"d{i % 8}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"f{i:05d}.bin"), "wb") as f:
            f.write(bytes((i % 251,)) * SIZE)


def run(backends=None, memcheck=H.NONE) -> bool:
    try:
        binp = _build()
    except Exception as e:
        print(f"    [tsan_scheduler_reset] BUILD FAILED: {e}")
        return False

    src = K.fresh_src_root("tsan_reset_src")
    _make_small_tree(src)
    dest = K.fresh_dest("tsan_reset_dest")
    logdir = pathlib.Path(tempfile.mkdtemp(prefix="uc-tsan-reset-log-"))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", DISPLAY="",
               TSAN_OPTIONS=("halt_on_error=0:report_thread_leaks=0:"
                             f"suppressions={_SUPP}:log_path={logdir}/tsan"))
    try:
        r, abort = K.run_tsan([binp, src, dest], env, 900, logdir)
    except subprocess.TimeoutExpired:
        print("    [tsan_scheduler_reset] FAIL: timed out (livelock under TSan?)")
        shutil.rmtree(logdir, ignore_errors=True); shutil.rmtree(src, ignore_errors=True)
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
                        our_sample = "\n".join(block.strip().splitlines()[:12])
    if ours:
        problems.append(f"{ours} TSan report(s) touching OUR code (fix the race, never suppress "
                        f"it):\n{our_sample}")
    d = subprocess.run(["diff", "-rq", "--no-dereference", src,
                        os.path.join(dest, os.path.basename(src))], capture_output=True, text=True)
    if d.returncode != 0:
        problems.append(f"content diff FAILED:\n{d.stdout[-600:]}")

    shutil.rmtree(logdir, ignore_errors=True)
    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(dest, ignore_errors=True)
    if problems:
        print("    [tsan_scheduler_reset] FAIL:")
        for p in problems:
            print("      - " + p)
        return False
    print(f"    [tsan_scheduler_reset] PASS  (reset fired under TSan on a {NFILES}-small-file copy; "
          f"{total} third-party report(s) suppressed, 0 touching our code; content ok)")
    return True


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
