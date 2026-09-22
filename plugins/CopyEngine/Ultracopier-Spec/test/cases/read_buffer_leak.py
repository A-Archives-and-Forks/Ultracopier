#!/usr/bin/env python3
"""Per-file heap leak guard (async). ReadThread::internalRead() malloc'd one blockSize buffer per
read() and never freed the one that hit EOF (nor the one of a failed read, nor a block the writer
refused before queuing it): ONE blockSize block (default 64 KiB..16 MiB) leaked per file copied --
a 50M-file job would exhaust the address space. Copy a small tree under valgrind --leak-check=full
and assert NO "definitely lost" block whose stack is in the copy engine's I/O classes.
(The harness valgrind lane counts invalid accesses only, not leaks, hence this dedicated case.)"""
import sys, pathlib, os, re, subprocess, time, shutil, signal
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K
NFILES = 30
OURS = re.compile(r"\b(ReadThread|WriteThread|TransferThread|TransferThreadAsync|ListThread|ScanFileOrFolder|MkPath)::")


def run(backends=None, memcheck=H.NONE) -> bool:
    if not shutil.which("valgrind"):
        print("    [read_buffer_leak] FAIL: valgrind not installed")
        return False
    cfg = H.load_config()
    binp = H.binary_for(H.ASYNC, cfg)
    src = pathlib.Path(K.fresh_src_root("rbl_src"))
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    for i in range(NFILES):
        (src / f"f{i}.dat").write_bytes(bytes(((i * 31 + j) & 0xFF) for j in range(3000 + i)))
    dest = pathlib.Path(K.fresh_dest("rbl_dest"))
    home = pathlib.Path(K.fresh_dest("rbl_home"))
    H.write_config(home, file_collision=H.FileCollision.OVERWRITE, folder_collision=H.FolderCollision.MERGE,
                   file_error=H.FileError.SKIP, folder_error=H.FolderError.SKIP)
    vg_log = home / "valgrind.log"
    env = dict(os.environ, HOME=str(home), QT_QPA_PLATFORM="offscreen", DISPLAY="",
               XDG_CONFIG_HOME=str(home / ".config"), ULTRACOPIER_SOCKET_SUFFIX=H.TEST_SOCKET_SUFFIX)
    proc = subprocess.Popen(["valgrind", "--tool=memcheck", "--leak-check=full", "--show-leak-kinds=definite",
                             f"--log-file={vg_log}", binp, "cp", str(src), str(dest)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    copied = dest / src.name
    t0 = time.time()
    while time.time() - t0 < 300:
        if copied.is_dir() and len(list(copied.iterdir())) >= NFILES:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    time.sleep(3)   # let the last close/post-op settle before the graceful stop
    n = len(list(copied.iterdir())) if copied.is_dir() else 0
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)   # graceful: valgrind writes its leak report at exit
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
    txt = vg_log.read_text(errors="ignore") if vg_log.exists() else ""
    txt = re.sub(r"(?m)^==\d+==\s?", "", txt)
    leaked = []
    for blk in txt.split("\n\n"):
        if "definitely lost" in blk and OURS.search(blk):
            m = re.search(r"([\d,]+) bytes in ([\d,]+) blocks are definitely lost", blk)
            # a PER-FILE leak shows as many blocks; a single block is a one-shot object the SIGTERM
            # stop never freed (e.g. a `new` in ListThread::run()) -- not what this case guards
            if m and int(m.group(2).replace(",", "")) >= 2:
                leaked.append((m.group(0), OURS.search(blk).group(0)))
    ok = (n >= NFILES) and not leaked and bool(txt)
    print(f"    [async valgrind per-file leak] {'PASS' if ok else 'FAIL'} copied={n}/{NFILES} "
          f"engine_leaks={leaked[:3]} report={'yes' if txt else 'MISSING'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
