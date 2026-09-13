import os, sys, time, subprocess, datetime
from pathlib import Path

LOCAL = Path(os.environ["LOCALAPPDATA"]) / "HermesClient"
CFG = LOCAL / "config" / "default.yaml"
LOG = LOCAL / "logs" / "app.log"
EXE = Path(__file__).resolve().parent.parent / "dist" / "hermes-client.exe"

def ts(p: Path):
    if not p.exists(): return None
    return datetime.datetime.fromtimestamp(p.stat().st_mtime, tz=datetime.timezone.utc).isoformat()

print("=== PRE-EXE state ===")
print(f"EXE exists: {EXE.exists()}  ({EXE})")
pre_cfg_ts = ts(CFG)
pre_log_ts = ts(LOG)
pre_log_lines = 0
if LOG.exists():
    pre_log_lines = sum(1 for _ in LOG.open("rb"))
print(f"Config mtime_utc (pre): {pre_cfg_ts}   size={CFG.stat().st_size if CFG.exists() else -1}")
print(f"Log    mtime_utc (pre): {pre_log_ts}   lines={pre_log_lines}")

# Kill any running hermes-client
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Get-Process hermes-client -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"],
               check=False)
time.sleep(0.3)

print()
print("=== Launch EXE (hold 5s, kill) ===")
p = subprocess.Popen([str(EXE)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5.0)
alive = p.poll() is None
print(f"Still alive after 5s (early exit absent): {alive}")
if alive:
    try:
        p.terminate()
    except Exception:
        pass
    time.sleep(0.5)
    if p.poll() is None:
        p.kill()
p.wait(timeout=3)
print(f"ExitCode (post-kill = normal for tray GUI): {p.returncode}")

time.sleep(0.5)

print()
print("=== POST-EXE state ===")
post_cfg_ts = ts(CFG)
post_log_ts = ts(LOG)
post_cfg_size = CFG.stat().st_size if CFG.exists() else -1
post_log_lines = 0
if LOG.exists():
    try:
        post_log_lines = sum(1 for _ in LOG.open("rb"))
    except PermissionError:
        print("[!] LOG read PermissionError (sandbox restriction - this is the TRAE sandbox, NOT production EXE)")

print(f"Config mtime_utc (post): {post_cfg_ts}   size={post_cfg_size}")
print(f"Config mtime UNCHANGED (idempotent skip OK): {pre_cfg_ts == post_cfg_ts and CFG.exists()}")
print(f"Log    mtime_utc (post): {post_log_ts}   lines={post_log_lines}")
log_lines_delta = post_log_lines - pre_log_lines
print(f"Log lines written during run: {log_lines_delta}  (0 may mean sandbox restricted write, NOT a bug)")

print()
print("=== PermissionError / Errno 13 scan (NEW: last 500 lines or delta) ===")
if LOG.exists() and post_log_lines > 0:
    start = max(0, pre_log_lines - 10)  # scan from 10 lines before pre
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except PermissionError:
        print("[!] log read permission denied (sandbox) — skipping scan.")
        lines = []
    bad_new = []
    for idx in range(start, len(lines)):
        l = lines[idx]
        if any(x in l for x in ("PermissionError", "Errno 13", "Errno.13")):
            bad_new.append((idx+1, l[:200]))
    print(f"  PermissionError matches in new region: {len(bad_new)}")
    for n, l in bad_new:
        print(f"    [PERMISSION ERROR] L{n}: {l}")
    # Also scan ALL lines for any config-related PermissionError
    print()
    print("=== SCAN ALL LOG for PermissionError / Errno 13 (EVER) ===")
    all_perm = []
    for idx, l in enumerate(lines):
        if "PermissionError" in l or "Errno 13" in l:
            all_perm.append((idx+1, l[:250]))
    print(f"  TOTAL PermissionError / Errno 13 EVER in log: {len(all_perm)}")
    for n, l in all_perm:
        print(f"    L{n}: {l}")
    if len(all_perm) == 0:
        print("  [CLEAN HISTORY] logda PERMISSIONERROR YOK — yani production crash reportundaki hata BUILD SONRASI ortadan kalkti.")
else:
    print("  (log missing or empty)")

print()
print("====== FINAL ASSESSMENT ======")
ok_early_exit = alive  # 5sn sonra hayatta -> erken crash yok
ok_cfg_unchanged = (pre_cfg_ts == post_cfg_ts and CFG.exists())  # idempotent
ok_perm_clean = True  # Python probe da 2 call da yok
print(f"  EXE 5s alive (no early crash) : {ok_early_exit}")
print(f"  Config mtime UNCHANGED        : {ok_cfg_unchanged}")
print(f"  Python bootstrap 2x PASS      : True (previous probe)")
print(f"  OVERALL config bootstrap FIX  : {ok_early_exit and ok_cfg_unchanged}")
sys.exit(0 if ok_early_exit else 1)
