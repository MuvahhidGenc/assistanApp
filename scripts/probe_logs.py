import os
from pathlib import Path

log_path = Path(os.environ["LOCALAPPDATA"]) / "HermesClient" / "logs" / "app.log"
cfg_path = Path(os.environ["LOCALAPPDATA"]) / "HermesClient" / "config" / "default.yaml"

print(f"[LOG] path: {log_path}")
print(f"[LOG] exists={log_path.exists()}")
if log_path.exists():
    st = log_path.stat()
    print(f"[LOG] size={st.st_size} bytes  mtime={st.st_mtime_ns}")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    N = len(lines)
    print(f"[LOG] total lines: {N}")
    K = min(25, N)
    print(f"[LOG] ---- LAST {K} LINES ----")
    for i in range(N - K, N):
        print(f"  L{i+1}: {lines[i]}")
    print(f"[LOG] ---- END ----")
    # Scan LAST 500 lines for any PermissionError / Traceback / Errno 13
    scan_start = max(0, N - 500)
    bad = []
    for idx in range(scan_start, N):
        ln = lines[idx]
        if any(x in ln for x in ("PermissionError", "Errno 13", "Errno.13", "Traceback", "Exception")):
            bad.append((idx+1, ln))
    print(f"[LOG] ERROR matches in last 500 lines: {len(bad)}")
    for ln_num, ln in bad:
        print(f"  [BAD] L{ln_num}: {ln}")
    # Find newest tray_entry_start event - check timestamp for today
    import json, datetime
    newest_boot = None
    for idx in range(max(0, N-500), N):
        raw = lines[idx]
        if "tray_entry_start" in raw or "tray_app_start" in raw:
            # parse json if possible
            try:
                obj = json.loads(raw)
                ts = obj.get("timestamp")
                if ts:
                    newest_boot = ts
            except Exception:
                newest_boot = raw[:200]
    print(f"[LOG] Newest tray_entry/tray_app_start marker timestamp: {newest_boot}")

print()
print(f"[CFG] path: {cfg_path}")
if cfg_path.exists():
    st = cfg_path.stat()
    import datetime
    mtime_utc = datetime.datetime.fromtimestamp(st.st_mtime, tz=datetime.timezone.utc).isoformat()
    print(f"[CFG] size={st.st_size} bytes  IsReadOnly={bool(st.st_file_attributes & 1)}")
    print(f"[CFG] mtime_utc = {mtime_utc}")
    print(f"[CFG] last 10 lines:")
    for i, l in enumerate(cfg_path.read_text(encoding="utf-8").splitlines()[-10:]):
        print(f"  {l}")
else:
    print("[CFG] NOT FOUND")

print()
print("[FINAL] If latest tray_app_start timestamp matches TODAY and no PermissionError, then SMOKE = PASS")
