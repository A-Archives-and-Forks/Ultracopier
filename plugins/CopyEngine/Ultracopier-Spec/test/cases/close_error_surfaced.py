#!/usr/bin/env python3
"""A write error that only surfaces at close() (NFS/CIFS/USB writeback: EDQUOT/ENOSPC/EIO) must be
reported as a FAILED file, never as success. WriteThread::internalClose() only logged a failed close(),
then emitted closed() -> the file counted as copied and a MOVE deleted its source.
Setup: MOVE a small tree; the shim fails close() on every path under the destination directory whose
name contains CFDEST (dest-side only; the sources are untouched). fileError=SKIP.
ASSERTS (async only: io_uring closes through the ring, not libc):
  * the job completes, the process stays alive;
  * EVERY source file SURVIVES (no source deleted after a failed close);
  * no memory errors."""
import sys, pathlib, os, tempfile, shutil
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import synthtree, casekit as K


def run(backends=None, memcheck=H.NONE) -> bool:
    # source on /tmp (tmpfs), dest on the scratch fs: a SAME-fs move takes the per-file rename() path
    # and never writes/closes anything, so the close fault would never be reached
    base = tempfile.mkdtemp(prefix="uc-closefail-", dir="/tmp")
    src = synthtree.make_tree(os.path.join(base, "closefail_src"), "default")
    before = sorted(os.path.relpath(os.path.join(dp, f), src)
                    for dp, _d, fs in os.walk(src) for f in fs)
    dest = K.fresh_dest("closefail_CFDEST_dest")
    K.with_scenario("closefail:CFDEST")
    try:
        r = H.run(H.ASYNC, "mv", [src], dest, file_collision=H.FileCollision.OVERWRITE,
                  folder_collision=H.FolderCollision.MERGE, file_error=H.FileError.SKIP,
                  expect_dir=None, fs_preload=K.fs_so(), memcheck=memcheck,
                  extra_options={"moveTheWholeFolder": "false"})   # per-file move: no whole-folder rename
    finally:
        K.with_scenario("")
    after = sorted(os.path.relpath(os.path.join(dp, f), src)
                   for dp, _d, fs in os.walk(src) for f in fs) if os.path.isdir(src) else []
    lost = sorted(set(before) - set(after))
    ok = r.completed and r.stayed_alive and not lost and not r.oom_killed and r.mem_errors == 0
    shutil.rmtree(base, ignore_errors=True)
    print(f"    [async closefail on MOVE] {'PASS' if ok else 'FAIL'}"
          + ("" if ok else f" completed={r.completed} alive={r.stayed_alive} sources_lost={lost[:6]} mem_errors={r.mem_errors}"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
