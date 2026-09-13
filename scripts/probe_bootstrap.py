import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes.config.paths import user_config_path, ensure_user_dirs
from hermes.config.settings_store import bootstrap_user_config
from hermes.config_client import ensure_client_config, write_yaml  # noqa: F401

ensure_user_dirs()
p = user_config_path()
pre_exists = p.exists()
pre_size = p.stat().st_size if pre_exists else -1
pre_mtime = p.stat().st_mtime_ns if pre_exists else -1
print(f"[1] Before bootstrap: path={p}  exists={pre_exists}  size={pre_size}")

t0 = time.perf_counter()
try:
    r = bootstrap_user_config()
except PermissionError as e:
    print(f"[FAIL] PermissionError during first bootstrap: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(2)
dt = (time.perf_counter() - t0) * 1000
post_exists = p.exists()
post_size = p.stat().st_size if post_exists else -1
post_mtime = p.stat().st_mtime_ns if post_exists else -1
print(f"[2] bootstrap_user_config() returned={r!r}  duration={dt:.1f} ms")
print(f"    After: exists={post_exists}  size={post_size}")
rewrote = pre_exists and post_exists and pre_mtime != post_mtime
print(f"    Rewrote file on first call: {rewrote}")

print("[3] Config content head:")
for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()[:20]):
    print(f"    L{i+1:02d}: {line}")

print("[4] === SECOND bootstrap_user_config call (idempotency check) ===")
t1 = time.perf_counter()
try:
    r2 = bootstrap_user_config()
except PermissionError as e:
    print(f"[FAIL] PermissionError on SECOND bootstrap call: {e}")
    sys.exit(2)
dt2 = (time.perf_counter() - t1) * 1000
post2_mtime = p.stat().st_mtime_ns
unchanged = post_exists and post2_mtime == post_mtime
print(f"    Second call returned={r2!r}  duration={dt2:.1f} ms")
print(f"    mtime unchanged (NO rewrite): {unchanged}")

print("[5] === Result ===")
ok = unchanged or not pre_exists
if ok:
    print("[PASS] bootstrap_user_config: PermissionError NEVER raised. Second call NO rewrite (idempotent).")
    sys.exit(0)
else:
    print("[WARN] Second call rewrote the file. Still no PermissionError, but non-ideal.")
    sys.exit(1)
