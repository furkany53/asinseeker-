"""Uzun sureli, denetimsiz calisan arka plan scriptleri (full_pool_check.py,
keepa_web_prefilter.py) icin basit bir PID kilit dosyasi.

NEDEN: Bu scriptler nohup ile baslatilip unutulabiliyor -- kazayla ayni
scripti ikinci kez baslatmak (ya da iki senkron makinede ayni anda
calistirmak) sonuclar.csv'ye birbirine karisan yazimlara ve ayni ASIN'in
ciftlenerek API token harcamasiyla yeniden kontrol edilmesine yol acar.
Process icindeki write_lock (threading.Lock) sadece thread'leri
serilestirir, ayri bir process'i DEGIL -- bu yuzden ayrica bir PID kilit
dosyasi gerekiyor.
"""

import os
import sys
from pathlib import Path


def _pid_is_running(pid):
    if sys.platform == "win32":
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ProcessLock:
    """with ProcessLock(path): ... -- ayni kilit dosyasina sahip BASKA bir
    calisan process varsa RuntimeError firlatir. Sahibi artik calismiyorsa
    (crash/kill sonrasi kalmis "stale" kilit) sessizce uzerine yazar."""

    def __init__(self, lock_path):
        self.lock_path = Path(lock_path)
        self._acquired = False

    def acquire(self):
        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pid = None
            if pid and pid != os.getpid() and _pid_is_running(pid):
                raise RuntimeError(
                    f"Bu script zaten calisiyor gibi gorunuyor (PID {pid}, kilit dosyasi: "
                    f"{self.lock_path}). Gercekten calismiyorsa kilit dosyasini silip tekrar dene."
                )
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        self._acquired = True
        return self

    def release(self):
        if self._acquired:
            try:
                self.lock_path.unlink()
            except OSError:
                pass
            self._acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
