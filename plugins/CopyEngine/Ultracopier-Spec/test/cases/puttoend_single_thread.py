#!/usr/bin/env python3
"""Put-to-end after a MID-FILE read error must not wedge the transfer thread (async).

fileError=2 (put-to-end, the SHIPPING default) defers a failing file to the bottom and retries it
once. The retry-teardown went through WriteThread::stop(false), which -- after the isOpen semaphore
had been released once too often (a 3.1 regression) -- pre-closed the destination fd from the caller
thread and never emitted closed(): the thread stayed in Transfer forever. With inodeThreads=1 the
FIRST deferral wedged the only thread and every later file was silently never copied; the job even
looked finished. With 16 threads, 16 bad files wedge the whole job the same way.

Setup: a default tree + 3 files whose reads fail EIO after 64 KiB (eio_after: permanent, so the one
deferred retry fails too and escalates to the Ask dialog, answered SKIP by the hook), inodeThreads=1.
ASSERTS: the job completes, EVERY good file is byte-identical at the destination, no mem errors."""
import sys, pathlib, os, filecmp
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import synthtree, casekit as K
NBAD = 3


def run(backends=None, memcheck=H.NONE) -> bool:
    src = K.fresh_src_root("pte1_src")
    synthtree.make_tree(src, "default")
    for i in range(NBAD):
        pathlib.Path(src, f"bad_RECOVER{i}.bin").write_bytes(synthtree._seeded_bytes_fast(100 + i, 1024 * 1024 + 7))
    dest = K.fresh_dest("pte1_dest")
    K.with_scenario("eio_after:RECOVER:65536")
    os.environ["ULTRACOPIER_TEST_FILE_ERROR_ACTION"] = "skip"
    try:
        r = H.run(H.ASYNC, "cp", [src], dest, file_collision=H.FileCollision.OVERWRITE,
                  folder_collision=H.FolderCollision.MERGE, file_error=H.FileError.PUT_TO_END,
                  expect_dir=None, fs_preload=K.fs_so(), inode_threads=1, memcheck=memcheck)
    finally:
        K.with_scenario("")
        os.environ.pop("ULTRACOPIER_TEST_FILE_ERROR_ACTION", None)
    copied = os.path.join(dest, os.path.basename(src))
    missing = []
    for dp, _dirs, files in os.walk(src):
        for fn in files:
            sp = os.path.join(dp, fn)
            if "RECOVER" in fn or os.path.islink(sp):
                continue
            dq = os.path.join(copied, os.path.relpath(sp, src))
            if not (os.path.exists(dq) and filecmp.cmp(sp, dq, shallow=False)):
                missing.append(os.path.relpath(sp, src))
    ok = r.completed and r.stayed_alive and not missing and not r.oom_killed and r.mem_errors == 0
    print(f"    [async put-to-end inodeThreads=1] {'PASS' if ok else 'FAIL'}"
          + ("" if ok else f" completed={r.completed} alive={r.stayed_alive} missing={missing[:6]} mem_errors={r.mem_errors}"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
