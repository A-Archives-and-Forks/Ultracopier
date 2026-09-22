#!/usr/bin/env python3
"""In-process PluginInterface_CopyEngine API test on a RUNNING transfer (#14/#18).

Builds + runs test/unit/engine_api_test.cpp, which instantiates the REAL CopyEngine, configures it
via the same setXxx() calls the factory uses (no OptionInterface needed), and drives a real copy
through a live Qt event loop -- exercising the engine's public API against an in-flight transfer:

  * plain copy   -> the engine reaches actionInProgess(Idle) after going active, and the copied
                    tree is byte-identical (the engine actually copies, driven purely in-process).
  * pause/resume -> pause() makes the engine emit isInPause(true) and dest progress PLATEAUS
                    (writes stop after the in-flight pipeline drains); resume() completes the copy
                    and the tree is byte-identical.
  * cancel       -> cancel() mid-transfer reaches canBeDeleted() within 20 s (the engine's own
                    teardown signal: every transfer thread went Idle) and the partial destination
                    is REMOVED. 3.1 regression: WriteThread::stop() of an open file never emitted
                    closed() (isOpen semaphore released once too often), so the cancel never
                    completed -- engine + 33 threads leaked per cancel, partial file left behind.

I/O is slowed by the LD_PRELOAD shim (UC_FS_SCENARIO=slow:<ms>) so the copy is long enough to be
caught mid-flight and paused. ASYNC backend only (the driver links the async TransferThread; the
shim faults only libc I/O). Runs in a few seconds + the one-off engine build.
"""
import sys, os, re, pathlib, subprocess, tempfile, shutil
_HERE = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _HERE)]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K

_UNIT_DIR = pathlib.Path(__file__).resolve().parents[1] / "unit"
_PRO = _UNIT_DIR / "engine_api_test.pro"


def _qmake() -> str:
    return shutil.which("qmake6") or shutil.which("qmake") or "qmake6"


def _build() -> str:
    bdir = pathlib.Path(tempfile.mkdtemp(prefix="uc-engine-api-"))
    q = subprocess.run([_qmake(), "-o", str(bdir / "Makefile"), str(_PRO),
                        "CONFIG+=release", "-spec", "linux-g++"], capture_output=True, text=True)
    if q.returncode != 0:
        raise RuntimeError(f"qmake failed:\n{q.stderr[-1500:]}")
    m = subprocess.run(["make", "-C", str(bdir), f"-j{os.cpu_count()}"], capture_output=True, text=True)
    binp = bdir / "engine_api_test"
    if m.returncode != 0 or not binp.exists():
        errs = "\n".join(l for l in m.stdout.splitlines() + m.stderr.splitlines()
                         if "error:" in l or "undefined reference" in l)
        raise RuntimeError(f"build failed:\n{errs[-2000:]}")
    return str(binp)


def _mk_src(name: str, nfiles: int, fsize: int) -> str:
    src = pathlib.Path(K.fresh_src_root(name))
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    for i in range(nfiles):
        # deterministic content (re-runs reproduce); varied per file
        (src / f"f{i}.dat").write_bytes(bytes(((i * 131 + j * 7) & 0xFF) for j in range(fsize)))
    return str(src)


def _run(binp, src, dest, env, want_pause, mode=None):
    argv = [binp, src, dest] + ([mode] if mode else (["pause"] if want_pause else []))
    r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=140)
    out = r.stdout + r.stderr
    subprocess.run(["pkill", "-9", "-x", "engine_api_test"], capture_output=True)
    return r.returncode, out


def _is_prefix(src_file, dst_file) -> bool:
    """A kept partial must be a faithful PREFIX of its source (never wrong bytes)."""
    with open(dst_file, "rb") as d, open(src_file, "rb") as s:
        got = d.read()
        return got == s.read(len(got))


def _diff_ok(src, dest) -> bool:
    copied = os.path.join(dest, os.path.basename(src))
    return subprocess.run(["diff", "-rq", "--no-dereference", src, copied],
                          capture_output=True).returncode == 0


def run(backends=None, memcheck=H.NONE) -> bool:
    try:
        binp = _build()
    except RuntimeError as e:
        print(f"    {e}")
        return False
    base_env = dict(os.environ, HOME="/tmp/uc-engine-api-home",
                    QT_QPA_PLATFORM="offscreen", DISPLAY="")
    results = []

    # 1. plain in-process copy -> completes + byte-identical
    src = _mk_src("engapi_plain_src", 8, 60000)
    dest = K.fresh_dest("engapi_plain_dest"); pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
    rc, out = _run(binp, src, dest, base_env, want_pause=False)
    m = re.search(r"DONE done=(\d).*destBytes=(\d+)/(\d+)", out)
    completed = bool(m and m.group(1) == "1")
    content = _diff_ok(src, dest)
    ok1 = (rc == 0 and completed and content)
    print(f"      [plain]  done={completed} content_identical={content} -> {'PASS' if ok1 else 'FAIL'}")
    if not ok1:
        print("        " + out.strip().splitlines()[-1] if out.strip() else "        (no output)")
    results.append(ok1)

    # 2. pause/resume on a running (slowed) transfer -> isInPause + progress plateaus, then completes
    src = _mk_src("engapi_pause_src", 14, 400000)   # 5.6 MB, slow I/O -> catchable mid-copy
    dest = K.fresh_dest("engapi_pause_dest"); pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
    env = dict(base_env, LD_PRELOAD=f"{K.fs_so()}:" + base_env.get("LD_PRELOAD", ""),
               UC_FS_SCENARIO="slow:10")
    rc, out = _run(binp, src, dest, env, want_pause=True)
    mp = re.search(r"PAUSE isInPause=(\d) drained=(\d+) plateau=(\d+) halted=(\d)", out)
    md = re.search(r"DONE done=(\d).*destBytes=(\d+)/(\d+)", out)
    paused_sig = bool(mp and mp.group(1) == "1")
    halted = bool(mp and mp.group(4) == "1")
    completed = bool(md and md.group(1) == "1")
    content = _diff_ok(src, dest)
    ok2 = (rc == 0 and paused_sig and halted and completed and content)
    print(f"      [pause]  isInPause={paused_sig} progress_halted={halted} resumed_done={completed} "
          f"content_identical={content} -> {'PASS' if ok2 else 'FAIL'}")
    if not ok2:
        for l in out.strip().splitlines()[-3:]:
            print(f"        {l}")
    results.append(ok2)

    # 3. cancel on a running (slowed) transfer -> canBeDeleted within 20 s. Files finished BEFORE the
    #    cancel legitimately stay; the in-flight one is REMOVED under deletePartiallyTransferredFiles
    #    (shipping default: a partial is a wrong-size file a later run could mistake for a copy) and
    #    KEPT as a faithful prefix when the option is off (UC_TEST_KEEP_PARTIALS=1 -> the driver
    #    calls setDeletePartiallyTransferredFiles(false)); the option was dead until 2026-09-21.
    for keep in (False, True):
        tag = "engapi_cancel_keep" if keep else "engapi_cancel"
        src = _mk_src(tag + "_src", 2, 6 * 1024 * 1024) if keep else _mk_src(tag + "_src", 14, 400000)
        dest = K.fresh_dest(tag + "_dest"); pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
        env_c = dict(env, UC_TEST_KEEP_PARTIALS="1") if keep else env
        rc, out = _run(binp, src, dest, env_c, want_pause=False, mode="cancel")
        mc = re.search(r"CANCEL at destBytes=(\d+)/(\d+)", out)
        md = re.search(r"DONE done=(\d) canDelete=(\d) destBytes=(\d+)/(\d+)", out)
        cancelled = bool(mc)
        can_delete = bool(md and md.group(2) == "1")
        copied = pathlib.Path(dest) / os.path.basename(src)
        partials = [f.name for f in (copied.iterdir() if copied.is_dir() else [])
                    if f.is_file() and f.stat().st_size != (pathlib.Path(src) / f.name).stat().st_size]
        prefix_ok = all(_is_prefix(pathlib.Path(src) / n, copied / n) for n in partials)
        partial_state = (bool(partials) and prefix_ok) if keep else (not partials)
        ok3 = (rc == 0 and cancelled and can_delete and partial_state)
        print(f"      [cancel{' keep-partials' if keep else ''}] cancelled_midway={cancelled} "
              f"canBeDeleted={can_delete} partials={partials} prefix_ok={prefix_ok} -> {'PASS' if ok3 else 'FAIL'}")
        if not ok3:
            for l in out.strip().splitlines()[-3:]:
                print(f"        {l}")
        results.append(ok3)

    # 4. put-to-end on ONE inode thread: the deferred file's close handshake + reschedule on the
    #    thread that also copies everything else (flaky:RECOVER:1 -> deferred once, retry succeeds).
    src = pathlib.Path(K.fresh_src_root("engapi_pte_src"))
    shutil.rmtree(src, ignore_errors=True); (src / "sub").mkdir(parents=True)
    for i in range(6):
        (src / f"good_{i}.bin").write_bytes(bytes(((i * 17 + j) & 0xFF) for j in range(300000 + i)))
    (src / "sub" / "bad_RECOVER.bin").write_bytes(bytes(((j * 7) & 0xFF) for j in range(2 * 1024 * 1024)))
    src = str(src)
    dest = K.fresh_dest("engapi_pte_dest"); pathlib.Path(dest).mkdir(parents=True, exist_ok=True)
    env4 = dict(base_env, LD_PRELOAD=f"{K.fs_so()}:" + base_env.get("LD_PRELOAD", ""),
                UC_FS_SCENARIO="flaky:RECOVER:1")
    rc, out = _run(binp, src, dest, env4, want_pause=False, mode="puttoend")
    m4 = re.search(r"DONE done=(\d)", out)
    done4 = bool(m4 and m4.group(1) == "1")
    content4 = _diff_ok(src, dest)
    ok4 = (rc == 0 and done4 and content4)
    print(f"      [puttoend] done={done4} content_identical={content4} -> {'PASS' if ok4 else 'FAIL'}")
    if not ok4:
        for l in out.strip().splitlines()[-3:]:
            print(f"        {l}")
    results.append(ok4)

    ok = all(results)
    print(f"    [engine_api] {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
