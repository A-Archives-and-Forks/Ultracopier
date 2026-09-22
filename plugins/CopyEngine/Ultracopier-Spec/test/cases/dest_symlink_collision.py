#!/usr/bin/env python3
"""A SYMLINK at the destination path is a collision too. The collision check used is_file() (lstat,
S_ISREG), so a dest that is a symlink to an existing file was "not existing": no policy consulted,
open(O_CREAT) followed the link and the symlink TARGET was overwritten even under fileCollision=SKIP.
Setup: dest/<tree>/latest.txt -> <outside>/real.txt (a user's file outside the dest tree).
ASSERTS (both backends): with SKIP the target keeps its content and the copy completes; every other
file is byte-identical."""
import sys, pathlib, os, filecmp
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K
REAL = b"the user's real file, must survive a SKIP\n"


def run(backends=None, memcheck=H.NONE) -> bool:
    def one(backend):
        src = K.fresh_src_root(f"dsl_src_{backend}")
        K.write_file(os.path.join(src, "latest.txt"), b"new content from the source\n")
        K.write_file(os.path.join(src, "other.txt"), b"other\n")
        outside = K.fresh_dest(f"dsl_outside_{backend}")
        real = os.path.join(outside, "real.txt")
        K.write_file(real, REAL)
        dest = K.fresh_dest(f"dsl_dest_{backend}")
        tree = os.path.join(dest, os.path.basename(src))
        os.makedirs(tree, exist_ok=True)
        os.symlink(real, os.path.join(tree, "latest.txt"))
        r = H.run(backend, "cp", [src], dest, file_collision=H.FileCollision.SKIP,
                  folder_collision=H.FolderCollision.MERGE, expect_dir=None, memcheck=memcheck)
        target_intact = K.read_file(real) == REAL
        still_link = os.path.islink(os.path.join(tree, "latest.txt"))
        other_ok = filecmp.cmp(os.path.join(src, "other.txt"), os.path.join(tree, "other.txt"), shallow=False)
        ok = r.completed and r.stayed_alive and target_intact and still_link and other_ok and r.mem_errors == 0
        if not ok:
            print(f"      not ok: completed={r.completed} alive={r.stayed_alive} target_intact={target_intact} "
                  f"still_link={still_link} other_ok={other_ok} mem_errors={r.mem_errors}")
        return ok
    return K.for_backends(backends, one)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
