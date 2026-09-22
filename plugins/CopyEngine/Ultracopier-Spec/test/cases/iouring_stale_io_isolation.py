#!/usr/bin/env python3
"""io_uring: I/O still owned by the kernel when a file FAILS must never reach the NEXT file.

A dying disk / stalled network mount answers one chunk with EIO at once while the other in-flight
reads of the same file take seconds. The error policy (skip) freed the thread and the next file
reused the SAME buffers: the late read then landed OLD bytes into the new file's buffer, and its
completion (same tag: buffer index) was credited to the new file -> silent content corruption of
a GOOD sibling, or a write from a recycled buffer. io_uring's close does not cancel pending ops.
FIX: cancel + bounded drain at the failed file's end, QUARANTINE what is still pending, and a
per-file generation in every read/write tag so a late completion of an older file is dropped.

Rig: a FUSE source (the ring reads through the daemon) where the victim faults at offset 0 for
every attempt and every other read of it is DELAYED; the good siblings are copied right after.
Two rounds: delay < drain deadline (the late reads are reaped) and delay > deadline (quarantine +
generation drop). inode_threads=1 so the same worker (same buffers) takes the next file.
ASSERTS: the job completes, EVERY good file is byte-identical, no crash, no mem errors."""
import sys, os, pathlib, subprocess, shutil, time, filecmp
_TEST_DIR = pathlib.Path(__file__).resolve().parents[1]
_CASES_DIR = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _CASES_DIR)]
sys.path.insert(0, str(_TEST_DIR))
from lib import harness as H
from lib import synthtree, casekit as K
_FSO = _TEST_DIR / "fsoverride"
VICTIM = "victim_STALEIO.dat"
NGOOD = 12


def _fuse_bin():
    if not (pathlib.Path("/dev/fuse").exists() and shutil.which("fusermount3")):
        return None
    if subprocess.run(["pkg-config", "--exists", "fuse3"]).returncode != 0:
        return None
    out = _FSO / "build" / "fuse_fault"
    subprocess.run(["make", "-C", str(_FSO), "fuse_fault"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return out if out.exists() else None


def _round(fuse, delay_ms: int) -> bool:
    back = pathlib.Path(f"/tmp/uc-staleio-back-{delay_ms}")
    mnt = pathlib.Path(f"/tmp/uc-staleio-mnt-{delay_ms}")
    subprocess.run(["fusermount3", "-u", str(mnt)], stderr=subprocess.DEVNULL)
    for d in (back, mnt):
        shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    # victim first in the queue (name sorts first); 8 chunks of the default 64 KiB block are in flight
    (back / ("a_" + VICTIM)).write_bytes(synthtree._seeded_bytes_fast(9, 8 * 64 * 1024 + 123))
    for i in range(NGOOD):
        (back / f"good_{i:02d}.bin").write_bytes(synthtree._seeded_bytes_fast(100 + i, 64 * 1024 * (1 + i % 4) + i))
    fenv = dict(os.environ, UC_FUSE_BACKING=str(back), UC_FUSE_FAULT=f"STALEIO:0:1000000",
                UC_FUSE_DELAY=f"STALEIO:{delay_ms}")
    subprocess.run([str(fuse), str(mnt), "-s"], env=fenv, check=True)
    time.sleep(1)
    if subprocess.run(["mountpoint", "-q", str(mnt)]).returncode != 0:
        print("    [skip] FUSE mount failed (sandbox restriction)")
        return True
    try:
        dest = K.fresh_dest(f"staleio_dest_{delay_ms}")
        r = H.run(H.IO_URING, "cp", [str(mnt)], dest, file_collision=H.FileCollision.OVERWRITE,
                  folder_collision=H.FolderCollision.MERGE, file_error=H.FileError.SKIP,
                  expect_dir=None, inode_threads=1)
        copied = os.path.join(dest, mnt.name)
        bad = [f"good_{i:02d}.bin" for i in range(NGOOD)
               if not (os.path.exists(os.path.join(copied, f"good_{i:02d}.bin"))
                       and filecmp.cmp(str(back / f"good_{i:02d}.bin"), os.path.join(copied, f"good_{i:02d}.bin"), shallow=False))]
        ok = r.completed and r.stayed_alive and not bad and not r.oom_killed and r.mem_errors == 0
        print(f"      [delay {delay_ms} ms] completed={r.completed} alive={r.stayed_alive} corrupt_or_missing={bad[:4]} mem_errors={r.mem_errors} -> {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        subprocess.run(["fusermount3", "-u", str(mnt)], stderr=subprocess.DEVNULL)


def run(backends=None, memcheck=H.NONE) -> bool:
    if backends is not None and H.IO_URING not in backends:
        print("    [skip] iouring_stale_io_isolation is io_uring-only")
        return True
    fuse = _fuse_bin()
    if fuse is None:
        print("    [skip] libfuse3/fusermount3 unavailable")
        return True
    ok = _round(fuse, 1200) and _round(fuse, 3500)
    print(f"    [io_uring stale I/O isolation] {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
