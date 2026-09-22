#!/usr/bin/env python3
"""The FileIsSameDialog's "always do this" must scope to SAME-FILE collisions only.

Before 2026-09-21 the same-file dialog stored its answer into the GLOBAL file-collision policy
(alwaysDoThisActionForFileExists): one "skip, always" on a file copied onto itself silently turned
every later REAL collision (a different file at the destination) into a skip -- no dialog, the
destination kept its stale bytes, the user never saw it.

Deterministic sequence (inodeThreads=1 + followTheStrictOrder): source 1 is S/a.txt copied INTO S
(dest S/a.txt == source -> FileIsSameDialog, answered "skip" + always), source 2 is T/b.txt whose
destination S/b.txt pre-exists with different bytes (a real collision). fileCollision=Ask (the
shipping default). REQUIRED: the FileExistsDialog is still shown for b.txt (marker file) and its
answer (overwrite) is applied, i.e. S/b.txt ends with T's bytes. Pre-fix: no second dialog, S/b.txt
stale. async + io_uring (the policy lives in CopyEngine, shared by every backend).
"""
import os
import sys
import pathlib

_HERE = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _HERE)]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K

SELF = b"same file, must stay untouched\n"
NEW = b"NEW bytes from T\n"
STALE = b"stale bytes already at the destination\n"


def _marker_kinds(path):
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip()]   # ordered, duplicates kept
    except OSError:
        return set()


def run(backends=None, memcheck=H.NONE) -> bool:
    def one(backend):
        tag = "samefile_scoped_" + backend
        s_dir = K.fresh_src_root(tag + "_S")
        t_dir = K.fresh_src_root(tag + "_T")
        K.write_file(os.path.join(s_dir, "a.txt"), SELF)
        K.write_file(os.path.join(s_dir, "b.txt"), STALE)     # the real collision target
        K.write_file(os.path.join(t_dir, "b.txt"), NEW)
        marker = os.path.join(K.fresh_src_root(tag + "_marker"), "marker.txt")
        os.makedirs(os.path.dirname(marker), exist_ok=True)   # the hook fopen()s it, the dir must exist
        if os.path.exists(marker):
            os.remove(marker)
        os.environ["ULTRACOPIER_TEST_COLLISION_MARKER"] = marker
        os.environ["ULTRACOPIER_TEST_SAME_FILE_ACTION"] = "skip"
        os.environ["ULTRACOPIER_TEST_COLLISION_ALWAYS"] = "1"
        os.environ["ULTRACOPIER_TEST_FILE_COLLISION_ACTION"] = "overwrite"
        try:
            r = H.run(backend, "cp", [os.path.join(s_dir, "a.txt"), os.path.join(t_dir, "b.txt")], s_dir,
                      file_collision=H.FileCollision.ASK,
                      folder_collision=H.FolderCollision.MERGE,
                      inode_threads=1,
                      extra_options={"followTheStrictOrder": "true"},
                      expect_dir=None, memcheck=memcheck)
        finally:
            for k in ("ULTRACOPIER_TEST_COLLISION_MARKER", "ULTRACOPIER_TEST_SAME_FILE_ACTION",
                      "ULTRACOPIER_TEST_COLLISION_ALWAYS", "ULTRACOPIER_TEST_FILE_COLLISION_ACTION"):
                os.environ.pop(k, None)
        kinds = _marker_kinds(marker)
        a_ok = K.read_file(os.path.join(s_dir, "a.txt")) == SELF
        b_final = K.read_file(os.path.join(s_dir, "b.txt"))
        ok = (r.completed and r.stayed_alive and a_ok and "same_file" in kinds
              and "file_collision" in kinds and b_final == NEW)
        if not ok:
            print(f"      [{backend}] completed={r.completed} alive={r.stayed_alive} a_intact={a_ok} "
                  f"dialogs={kinds} b_overwritten={b_final == NEW} (b={b_final!r})")
        return ok
    return K.for_backends(backends, one)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
