#!/usr/bin/env python3
"""Same-drive whole-folder MOVE whose rename() FAILS (a non-EXDEV error: a locked/stuck destination),
then the user CANCELS from the error dialog. The transfer thread sat in its error-wait with neither
readError nor writeError set, so stop() (whose realMove branch goes Idle only on an error flag) left it
in Transfer forever: the engine never reached Idle/canBeDeleted -- a "stuck" window, no message.
Now the failed rename is flagged as a destination error and the cancel completes.
Shim: renamefail on the destination path; fileError=Ask; the dialog hook answers CANCEL.
ASSERTS (both backends): the job reaches its end state (completed), the process stays alive, and the
SOURCE tree is untouched (a failed rename is atomic: nothing was moved)."""
import sys, pathlib, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import synthtree, casekit as K


def run(backends=None, memcheck=H.NONE) -> bool:
    def one(backend):
        src = synthtree.make_tree(K.fresh_src_root(f"mvrfc_src_{backend}"), "default")
        before = sorted(os.path.relpath(os.path.join(dp, f), src) for dp, _d, fs in os.walk(src) for f in fs)
        dest = K.fresh_dest(f"mvrfc_RENAMEFAIL_dest_{backend}")   # same fs as the source: whole-folder rename()
        K.with_scenario("renamefail:RENAMEFAIL")
        os.environ["ULTRACOPIER_TEST_FILE_ERROR_ACTION"] = "cancel"
        try:
            r = H.run(backend, "mv", [src], dest, file_collision=H.FileCollision.OVERWRITE,
                      folder_collision=H.FolderCollision.MERGE, file_error=H.FileError.ASK,
                      folder_error=H.FolderError.ASK, expect_dir=None, fs_preload=K.fs_so(), memcheck=memcheck)
        finally:
            K.with_scenario("")
            os.environ.pop("ULTRACOPIER_TEST_FILE_ERROR_ACTION", None)
        after = sorted(os.path.relpath(os.path.join(dp, f), src) for dp, _d, fs in os.walk(src) for f in fs) if os.path.isdir(src) else []
        source_intact = (after == before)
        ok = r.completed and r.stayed_alive and source_intact and r.mem_errors == 0
        if not ok:
            print(f"      not ok: completed={r.completed} alive={r.stayed_alive} source_intact={source_intact} mem_errors={r.mem_errors}")
        return ok
    return K.for_backends(backends, one)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
