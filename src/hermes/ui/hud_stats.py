from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import psutil


def _format_uptime(seconds: float) -> str:
    delta = timedelta(seconds=int(seconds))
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def collect_hud_stats() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    return {
        "cpu": psutil.cpu_percent(interval=0.1),
        "ram_used": vm.used,
        "ram_total": vm.total,
        "ram_percent": vm.percent,
        "uptime": _format_uptime(uptime.total_seconds()),
        "host": psutil.os.uname().node if hasattr(psutil.os, "uname") else "PC",
        "clock": datetime.now().strftime("%H:%M:%S"),
    }
