#!/usr/bin/env python3
"""An unopenable Write_log file must never block or crash a copy.

LogThread opened its file synchronously inside its constructor (blocking the GUI thread with a
modal QMessageBox before the copy was even requested) and, on a runtime option change, called
QMessageBox::critical from its OWN worker thread (widgets off the GUI thread). Since 2026-09-21 the
file is opened on the log thread and the failure travels through LogThread::errorMessage() to
Core::logError() on the GUI thread, where the dialog is shown while the copy keeps running.

Headless: [Write_log] enabled with a file under /proc (open() always fails). REQUIRED: the copy
completes byte-identical and the process stays alive (the error dialog is modal but the engine
runs in its own threads). Pre-fix the modal at construction time starved the copy start -> timeout.
"""
import os
import sys
import pathlib

_HERE = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _HERE)]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import casekit as K


def run(backends=None, memcheck=H.NONE) -> bool:
    src = K.fresh_src_root("logunopen_src")
    for i in range(4):
        K.write_file(os.path.join(src, f"f{i}.bin"), os.urandom(64 * 1024 + i))
    K.write_file(os.path.join(src, "sub", "deep.txt"), b"nested\n")

    def one(backend):
        dest = K.fresh_dest("logunopen_dest_" + backend)
        r = H.run(backend, "cp", [src], dest,
                  file_collision=H.FileCollision.OVERWRITE,
                  folder_collision=H.FolderCollision.MERGE,
                  expect_dir=src, memcheck=memcheck,
                  extra_sections={"Write_log": {"enabled": "true", "transfer": "true", "error": "true",
                                                "folder": "true", "sync": "false",
                                                "file": "/proc/no-such-dir/ultracopier.log"}})
        ok = r.ok
        if not ok:
            print(f"      [{backend}] completed={r.completed} alive={r.stayed_alive} "
                  f"content={r.content_ok} exit={r.exit_code} notes={r.notes[:200]}")
        return ok
    return K.for_backends(backends, one)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
