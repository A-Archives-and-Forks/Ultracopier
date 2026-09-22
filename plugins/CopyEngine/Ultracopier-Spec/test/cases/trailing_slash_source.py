#!/usr/bin/env python3
"""A source given WITH a trailing slash (shell completion: "ultracopier cp /data/a/ /backup") must land
under its own name. CopyListener::stripSeparator() discarded the result of std::regex_replace (a
no-op), and TransferThread::resolvedName() returned "root" for a ONE-character last component with a
trailing slash -> the folder was copied/moved to "<dest>/root". Both backends, copy and move."""
import sys, pathlib, os, filecmp
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K


def run(backends=None, memcheck=H.NONE) -> bool:
    def one(backend):
        ok = True
        for mode in ("cp", "mv"):
            root = pathlib.Path(K.fresh_src_root(f"tss_{mode}_{backend}"))
            src = root / "a"
            K.write_file(str(src / "one.txt"), b"one\n")
            K.write_file(str(src / "sub" / "two.txt"), b"two\n")
            dest = K.fresh_dest(f"tss_{mode}_{backend}_dest")
            r = H.run(backend, mode, [str(src) + "/"], dest, file_collision=H.FileCollision.OVERWRITE,
                      folder_collision=H.FolderCollision.MERGE, expect_dir=None, memcheck=memcheck)
            landed = os.path.join(dest, "a")
            named_ok = (os.path.isdir(landed) and not os.path.exists(os.path.join(dest, "root"))
                        and K.read_file(os.path.join(landed, "one.txt")) == b"one\n"
                        and K.read_file(os.path.join(landed, "sub", "two.txt")) == b"two\n")
            source_ok = (not os.path.exists(src)) if mode == "mv" else os.path.isdir(src)
            good = r.completed and r.stayed_alive and named_ok and source_ok and r.mem_errors == 0
            print(f"      [{mode}] landed_as_a={named_ok} source_ok={source_ok} -> {'PASS' if good else 'FAIL'}"
                  + ("" if good else f" completed={r.completed} alive={r.stayed_alive} dest={os.listdir(dest)}"))
            ok = ok and good
        return ok
    return K.for_backends(backends, one)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
