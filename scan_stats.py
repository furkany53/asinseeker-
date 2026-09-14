"""Bu bilgisayara ozel, kalici tarama hiz istatistikleri.

Her PC'nin performansi (internet hizi, CPU, vs.) farkli oldugu icin bu
istatistikler makine basina yerelde tutulur (paylasilmaz) -- ayni klasoru
kullanan license_guard/keepa_credentials gibi APPDATA altinda saklanir.
"""

import json
from pathlib import Path

APP_DATA_DIR = Path.home() / "AppData" / "Local" / "KeepaKesintiKontrol"
STATS_FILE = APP_DATA_DIR / "scan_stats.json"


def _load():
    if STATS_FILE.exists():
        try:
            data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            data.setdefault("total_count", 0)
            data.setdefault("total_seconds", 0.0)
            return data
        except Exception:
            pass
    return {"total_count": 0, "total_seconds": 0.0}


def _save(data):
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATS_FILE.write_text(json.dumps(data), encoding="utf-8")


def record_scan(duration_seconds):
    """Bir ASIN taramasi tamamlandiginda (basarili ya da hatali -- durdurma
    haric) cagrilir; kumulatif (tum zamanlar) ortalamayi gunceller."""
    if duration_seconds <= 0:
        return
    data = _load()
    data["total_count"] += 1
    data["total_seconds"] += duration_seconds
    _save(data)


def all_time_average_seconds():
    data = _load()
    if data["total_count"] == 0:
        return None
    return data["total_seconds"] / data["total_count"]


def all_time_total_count():
    return _load()["total_count"]


def all_time_total_seconds():
    return _load()["total_seconds"]


def format_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} sa {minutes} dk"
    if minutes:
        return f"{minutes} dk {secs} sn"
    return f"{secs} sn"
