#!/usr/bin/env python3
"""An EMPTY (0-byte) source overwriting a NON-EMPTY pre-existing destination (fileCollision=OVERWRITE)
must leave a 0-byte destination. The async open no longer truncates a pre-existing destination
(data-loss guard #9) and relied on the close-time truncate, which was gated on "bytes written !=
start size" -- 0 != 0 is false, so the OLD content survived and was reported as copied (a MOVE then
deleted the source). Real case: a log/config emptied at the source and synced over. Both backends."""
import sys, pathlib, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K
STALE = b"STALE CONTENT THAT MUST VANISH\n"


def run(backends=None, memcheck=H.NONE) -> bool:
    def one(backend):
        src = K.fresh_src_root(f"zero_ovr_src_{backend}")
        K.write_file(os.path.join(src, "empty.txt"), b"")
        K.write_file(os.path.join(src, "full.txt"), b"hello\n")
        dest = K.fresh_dest(f"zero_ovr_dest_{backend}")
        collide = os.path.join(dest, os.path.basename(src), "empty.txt")
        K.write_file(collide, STALE)
        r = H.run(backend, "cp", [src], dest, file_collision=H.FileCollision.OVERWRITE,
                  folder_collision=H.FolderCollision.MERGE, expect_dir=src, memcheck=memcheck)
        size = os.path.getsize(collide) if os.path.exists(collide) else -1
        ok = r.ok and size == 0
        if not ok:
            print(f"      not ok: completed={r.completed} alive={r.stayed_alive} content={r.content_ok} "
                  f"dest_size_of_empty={size} (expected 0) mem_errors={r.mem_errors}\n{r.diff_text}")
        return ok
    return K.for_backends(backends, one)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
