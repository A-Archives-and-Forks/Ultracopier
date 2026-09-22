#!/usr/bin/env python3
"""io_uring: an OVERWRITE whose source turns out unreadable must leave the user's pre-existing
destination untouched (async parity, data-loss guard #9). openDestFile() truncated the destination
to 0 at open; a read fault at byte 0 then skipped the file -- and the 0-byte wreck stayed, the
previous good copy gone. Now a pre-existing destination is only cut to the new size once the copy
succeeded. FUSE source: the victim faults at offset 0 on every attempt; fileError=Skip."""
import sys, os, pathlib, subprocess, shutil, time
_TEST_DIR = pathlib.Path(__file__).resolve().parents[1]
_CASES_DIR = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _CASES_DIR)]
sys.path.insert(0, str(_TEST_DIR))
from lib import harness as H
from lib import synthtree, casekit as K
_FSO = _TEST_DIR / "fsoverride"
OLD = b"the previous good copy: must survive an unreadable source under OVERWRITE\n" * 100


def _fuse_bin():
    if not (pathlib.Path("/dev/fuse").exists() and shutil.which("fusermount3")):
        return None
    if subprocess.run(["pkg-config", "--exists", "fuse3"]).returncode != 0:
        return None
    out = _FSO / "build" / "fuse_fault"
    subprocess.run(["make", "-C", str(_FSO), "fuse_fault"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return out if out.exists() else None


def run(backends=None, memcheck=H.NONE) -> bool:
    if backends is not None and H.IO_URING not in backends:
        print("    [skip] iouring_overwrite_keeps_old_on_fault is io_uring-only")
        return True
    fuse = _fuse_bin()
    if fuse is None:
        print("    [skip] libfuse3/fusermount3 unavailable")
        return True
    back = pathlib.Path("/tmp/uc-ovrkeep-back"); mnt = pathlib.Path("/tmp/uc-ovrkeep-mnt")
    subprocess.run(["fusermount3", "-u", str(mnt)], stderr=subprocess.DEVNULL)
    for d in (back, mnt):
        shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True)
    (back / "dead_UNREADABLE.bin").write_bytes(synthtree._seeded_bytes_fast(3, 300000))
    (back / "fine.bin").write_bytes(synthtree._seeded_bytes_fast(4, 200000))
    fenv = dict(os.environ, UC_FUSE_BACKING=str(back), UC_FUSE_FAULT="UNREADABLE:0:1000000")
    subprocess.run([str(fuse), str(mnt), "-s"], env=fenv, check=True)
    time.sleep(1)
    if subprocess.run(["mountpoint", "-q", str(mnt)]).returncode != 0:
        print("    [skip] FUSE mount failed (sandbox restriction)")
        return True
    try:
        dest = K.fresh_dest("ovrkeep_dest")
        victim_dst = os.path.join(dest, mnt.name, "dead_UNREADABLE.bin")
        K.write_file(victim_dst, OLD)
        r = H.run(H.IO_URING, "cp", [str(mnt)], dest, file_collision=H.FileCollision.OVERWRITE,
                  folder_collision=H.FolderCollision.MERGE, file_error=H.FileError.SKIP,
                  expect_dir=None, inode_threads=1)
        kept = os.path.exists(victim_dst) and K.read_file(victim_dst) == OLD
        fine = os.path.join(dest, mnt.name, "fine.bin")
        fine_ok = os.path.exists(fine) and K.read_file(fine) == (back / "fine.bin").read_bytes()
        ok = r.completed and r.stayed_alive and kept and fine_ok and r.mem_errors == 0
        print(f"    [io_uring overwrite keeps old on fault] completed={r.completed} alive={r.stayed_alive} old_kept={kept} sibling_ok={fine_ok} mem_errors={r.mem_errors} -> {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        subprocess.run(["fusermount3", "-u", str(mnt)], stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
