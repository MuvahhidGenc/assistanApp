from __future__ import annotations

import socket
from datetime import datetime, timedelta
from typing import Any

_BOOT = datetime.now()


def collect_hud_stats() -> dict[str, Any]:
    cpu = 0.0
    ram_used = 0.0
    ram_total = 1.0
    ram_pct = 0.0
    disk_used = 0.0
    disk_total = 1.0
    disk_pct = 0.0
    try:
        import psutil

        cpu = float(psutil.cpu_percent(interval=None))
        mem = psutil.virtual_memory()
        ram_used = mem.used / (1024**3)
        ram_total = max(mem.total / (1024**3), 0.1)
        ram_pct = float(mem.percent)
        disk = psutil.disk_usage("C:\\")
        disk_used = disk.used / (1024**3)
        disk_total = max(disk.total / (1024**3), 0.1)
        disk_pct = float(disk.percent)
    except Exception:
        pass

    uptime = datetime.now() - _BOOT
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    try:
        host = socket.gethostname()
    except OSError:
        host = "windows"
    load = min(100.0, (cpu * 0.6) + (ram_pct * 0.4))
    level = "Dusuk" if load < 30 else "Orta" if load < 70 else "Yuksek"
    return {
        "cpu": cpu,
        "ram_used": ram_used,
        "ram_total": ram_total,
        "ram_pct": ram_pct,
        "disk_used": disk_used,
        "disk_total": disk_total,
        "disk_pct": disk_pct,
        "uptime": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
        "host": host,
        "load": load,
        "load_label": level,
        "clock": datetime.now().strftime("%I:%M:%S %p  |  %d %B %Y"),
    }


def format_uptime_delta(delta: timedelta) -> str:
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
