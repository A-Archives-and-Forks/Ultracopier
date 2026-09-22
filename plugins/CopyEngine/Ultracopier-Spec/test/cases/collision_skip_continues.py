#!/usr/bin/env python3
"""A collision dialog answered "Skip" must skip THAT file only: the job goes on.

3.1 regression (async backend, hidden until 2026-09-21): the interactive Skip reached
TransferThreadAsync::skip() in PreOperation, which stopped the never-opened read/write threads and
waited for their closed() events; the write side does not send one for a file it never opened, so
the transfer thread never completed. With one inode thread every following file was silently
dropped (the job looked finished); with more threads the job never reached Idle. The same-file
dialog (source == destination) took the identical path. Since 3.0 the skip went straight to Idle.

Deterministic: inodeThreads=1 + followTheStrictOrder, sources [T/b.txt (collides with S/b.txt,
answered skip), T/c.txt (fresh)] -> S. REQUIRED: dialog shown once, S/b.txt untouched (bytes AND
mtime: a skip must not stamp the source's date onto the user's file), S/c.txt copied. Both
backends; a same-file variant (S/a.txt copied onto itself, answered skip) guards the other entry.
"""
import os
import sys
import pathlib

_HERE = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _HERE)]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K

NEW = b"NEW bytes from T\n"
STALE = b"stale bytes already at the destination\n"
FRESH = b"fresh file, must be copied after the skip\n"
SELF = b"same file\n"


def _marker(tag):
    p = os.path.join(K.fresh_src_root(tag + "_marker"), "marker.txt")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        os.remove(p)
    return p


def _kinds(path):
    try:
        with open(path) as f:
            return [l.strip() for l in f if l.strip()]
    except OSError:
        return []


def _one(backend, same_file, memcheck):
    tag = f"skipcont_{'same' if same_file else 'coll'}_{backend}"
    s_dir = K.fresh_src_root(tag + "_S")
    t_dir = K.fresh_src_root(tag + "_T")
    for d in (s_dir, t_dir):
        if os.path.exists(d):
            K._force_rmtree(pathlib.Path(d))
    if same_file:
        first = os.path.join(s_dir, "a.txt")
        K.write_file(first, SELF)
    else:
        first = os.path.join(t_dir, "b.txt")
        K.write_file(first, NEW)
        K.write_file(os.path.join(s_dir, "b.txt"), STALE)
        os.utime(os.path.join(s_dir, "b.txt"), (1000000000, 1000000000))   # 2001: a stamp would show
    K.write_file(os.path.join(t_dir, "c.txt"), FRESH)
    marker = _marker(tag)
    os.environ["ULTRACOPIER_TEST_COLLISION_MARKER"] = marker
    os.environ["ULTRACOPIER_TEST_FILE_COLLISION_ACTION"] = "skip"
    os.environ["ULTRACOPIER_TEST_SAME_FILE_ACTION"] = "skip"
    os.environ.pop("ULTRACOPIER_TEST_COLLISION_ALWAYS", None)
    try:
        r = H.run(backend, "cp", [first, os.path.join(t_dir, "c.txt")], s_dir,
                  file_collision=H.FileCollision.ASK, folder_collision=H.FolderCollision.MERGE,
                  inode_threads=1, extra_options={"followTheStrictOrder": "true"},
                  expect_dir=None, memcheck=memcheck)
    finally:
        for k in ("ULTRACOPIER_TEST_COLLISION_MARKER", "ULTRACOPIER_TEST_FILE_COLLISION_ACTION",
                  "ULTRACOPIER_TEST_SAME_FILE_ACTION"):
            os.environ.pop(k, None)
    kinds = _kinds(marker)
    if same_file:
        untouched = K.read_file(os.path.join(s_dir, "a.txt")) == SELF
        want = ["same_file"]
    else:
        bp = os.path.join(s_dir, "b.txt")
        untouched = K.read_file(bp) == STALE and int(os.stat(bp).st_mtime) == 1000000000
        want = ["file_collision"]
    c_ok = os.path.exists(os.path.join(s_dir, "c.txt")) and K.read_file(os.path.join(s_dir, "c.txt")) == FRESH
    ok = r.completed and r.stayed_alive and r.mem_errors == 0 and kinds == want and untouched and c_ok
    if not ok:
        print(f"      [{backend} {'same-file' if same_file else 'collision'}] completed={r.completed} "
              f"alive={r.stayed_alive} dialogs={kinds} skipped_untouched={untouched} next_file_copied={c_ok}")
    return ok


def run(backends=None, memcheck=H.NONE) -> bool:
    return K.for_backends(backends, lambda b: _one(b, False, memcheck) and _one(b, True, memcheck))


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
