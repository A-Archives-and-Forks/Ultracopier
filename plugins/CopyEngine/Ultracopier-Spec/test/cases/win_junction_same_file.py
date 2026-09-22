#!/usr/bin/env python3
"""IOCP/Windows: a file moved or copied onto ITSELF through a junction alias must survive.

TransferThread::isSame compared path strings only on Windows, so `mv S\\f.bin J` with J a junction
to S (dest J\\f.bin IS S\\f.bin) was treated as a normal transfer onto an existing destination:
with fileCollision=overwrite the destination -- the source itself -- was truncated/replaced and a
move then deleted "the source". The POSIX branch already resolves (st_dev,st_ino); the Windows
branch now compares volume serial + file index (GetFileInformationByHandle) and skips the no-op.

Runs only on the laptop lane (needs NTFS + mklink /J). Both mv and cp: REQUIRED S\\f.bin present
with its original SHA-256 afterwards, the engine idle, no crash.
"""
import sys
import uuid
import pathlib

_HERE = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if p not in ("", _HERE)]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from lib import harness as H
from lib import winlane


def _sha(box, path):
    r = box.ps(f"(Get-FileHash -Algorithm SHA256 -LiteralPath '{path}').Hash")
    return r.stdout.strip().upper() if r.returncode == 0 else ""


def run(backends=None, memcheck=H.NONE) -> bool:
    if backends is not None and H.IOCP not in backends:
        print("    [skip] win_junction_same_file is IOCP/Windows-only")
        return True
    cfg = H.load_config()
    host = cfg.get("windows", "host", fallback="").strip()
    exe = cfg.get("windows", "exe", fallback="").strip()
    if not host or not exe:
        print("    [skip] no [windows] host/exe configured")
        return True
    box = winlane._Box(host)
    dest_root = cfg.get("paths", "DESTINATIONWINDOWS", fallback=r"C:\cc-test\uc-auto").strip()
    base = winlane.win_join(dest_root, f"run-junction-{uuid.uuid4().hex[:8]}")
    real = winlane.win_join(base, "S")
    alias = winlane.win_join(base, "J")
    home = winlane.win_join(base, "home")
    conf_dir = winlane.win_join(home, ".config")
    exe_dir = exe.rsplit("\\", 1)[0]
    conf_path = winlane.win_join(exe_dir, "Ultracopier.conf")
    victim = winlane.win_join(real, "f.bin")
    ok_all = True
    try:
        box.ps("Stop-Process -Name ultracopier -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800")
        box.ps(f"@('{base}','{real}','{home}','{conf_dir}') | "
               "ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }")
        box.ps("$b = New-Object byte[] 1048576; (New-Object System.Random 7).NextBytes($b); "
               f"[System.IO.File]::WriteAllBytes('{victim}', $b)")
        r = box.ps(f'cmd /c mklink /J "{alias}" "{real}"')
        if r.returncode != 0:
            print(f"    [win_junction_same_file] cannot create the junction: {(r.stderr or r.stdout)[:200]}")
            return False
        h0 = _sha(box, victim)
        if not h0:
            print("    [win_junction_same_file] cannot hash the victim")
            return False
        for mode in ("mv", "cp"):
            winlane._put_text(box, conf_path, winlane._conf_text(
                file_collision=H.FileCollision.OVERWRITE, folder_collision=H.FolderCollision.MERGE,
                file_error=H.FileError.SKIP, folder_error=H.FolderError.SKIP, keep_date=True, do_right=True))
            r = box.ps(winlane._launch_script(exe, mode, [victim], alias, home, conf_dir, 512), timeout=90)
            pid = winlane._parse_kv(r.stdout).get("PID") if r.returncode == 0 else None
            if not pid:
                print(f"    [win_junction_same_file] launch failed: {(r.stderr or r.stdout)[:300]}")
                ok_all = False
                break
            completed, _peak, _oom, exit_code = winlane._wait_idle(box, pid, 180, 512)
            box.ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue; "
                   "Stop-Process -Name ultracopier -Force -ErrorAction SilentlyContinue")
            h1 = _sha(box, victim)
            ok = completed and exit_code is None and h1 == h0
            print(f"      [{mode} onto itself via junction] completed={completed} exit={exit_code} "
                  f"intact={h1 == h0} -> {'PASS' if ok else 'FAIL'}")
            ok_all = ok_all and ok
    finally:
        box.ps(f'cmd /c rmdir "{alias}"; Remove-Item -Recurse -Force -ErrorAction SilentlyContinue \'{base}\'')
    return ok_all


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
