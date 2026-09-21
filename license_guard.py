import base64
import binascii
import json
import subprocess
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

try:
    import winreg
except ImportError:
    winreg = None

# Kota/lisans bilgisi KASITLI olarak kurulum klasorunun (Program Files /
# LOCALAPPDATA\Programs\...) DISINDA saklanir -- normal bir "kaldir ve tekrar
# kur" bu klasorlere dokunmaz, bu yuzden deneme kotasi kendiliginden YENILENMEZ.
# Ayrica ayni sayi Windows Registry'e de yansitilir (iki farkli yerden okunup
# BUYUK OLANI gecerli sayilir) -- sadece AppData klasorunu silmek tek basina
# kotayi sifirlamaz, kullanicinin ayrica registry anahtarini da bulup silmesi
# gerekir. Bu %100 kirilmaz bir DRM degil (yerel/offline calisan her mekanizma
# gibi), ama rastgele bir "sil-tekrar kur" ile kotanin yenilenmesini engeller.
APP_DATA_DIR = Path.home() / "AppData" / "Local" / "KeepaKesintiKontrol"
USAGE_FILE = APP_DATA_DIR / "usage.dat"
LICENSE_FILE = APP_DATA_DIR / "license.key"
REGISTRY_KEY_PATH = r"Software\KeepaKesintiKontrol"
REGISTRY_VALUE_NAME = "UsageCount"

TRIAL_LIMIT = 100

PROGRAM_NAME = "AsinSeeker"
# Hangi musterinin hangi build'i calistirdigini anlayabilmek icin (destek
# talebi geldiginde) -- build_tmp/version_info.txt'deki FileVersion ile
# ELLE senkron tutulmali (PyInstaller'in .exe'nin dosya ozelliklerine
# yazdigi versiyonla BURADAKI, GUI ustbilgisinde/hakkinda kutusunda
# gosterilen versiyon ayni olsun diye).
VERSION = "1.1.0"
# Kisisel isim/e-posta UI'da GOSTERILMIYOR (musteriye gitmeden once gizlenmesi
# istendi) -- gercek iletisim kanali (site/e-posta) belirlenince buraya
# eklenir. Bos oldugu surece CONTACT_PLACEHOLDER kullanilir.
OWNER_NAME = ""
OWNER_CONTACT = ""
CONTACT_PLACEHOLDER = "İletişim bilgileri yakında eklenecek."
COPYRIGHT_NOTICE = (
    f"© {PROGRAM_NAME} — Bu yazilim izinsiz cogaltilamaz, "
    f"paylasilamaz veya dagitilamaz."
)

# ASIMETRIK imza (Ed25519) -- bu sadece PUBLIC anahtar, imza DOGRULAMAK
# icin kullanilir. Ozel anahtar (license_signing_key.pem) SADECE
# keygen.py'nin yaninda, yazilim sahibinde kalir; musteri makinesine hicbir
# zaman kopyalanmaz. Bu yuzden bu public key'in exe icinde acikta olmasi
# guvenlik riski OLUSTURMAZ -- ondan gecerli bir imza/lisans anahtari
# URETILEMEZ, sadece DOGRULANABILIR. (Eskiden simetrik bir HMAC secret'i
# kullaniliyordu; exe decompile edilip o secret cikarilirsa sinirsiz
# gecerli lisans uretilebiliyordu -- bu yuzden asimetrige gecildi.)
_LICENSE_PUBLIC_KEY_HEX = "7e54c20041f3d4f1c938e98a7734161967556d9b24ceb4986fd8733af68af504"


def _public_key():
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(_LICENSE_PUBLIC_KEY_HEX))


def get_machine_id():
    # Windows'a ozgu, format atmadan/yeniden kurulumdan etkilenmeyen bir
    # donanim kimligi. Bulunamazsa MAC adresine (uuid.getnode) duser.
    try:
        output = subprocess.check_output(
            ["wmic", "csproduct", "get", "UUID"],
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        ).decode(errors="ignore")
        lines = [line.strip() for line in output.splitlines() if line.strip() and "UUID" not in line.upper()]
        if lines and lines[0] and lines[0] != "00000000-0000-0000-0000-000000000000":
            return lines[0]
    except Exception:
        pass
    return f"MAC-{uuid.getnode()}"


def is_license_valid(machine_id, key):
    if not key:
        return False
    normalized_key = key.strip().upper().replace(" ", "").replace("-", "")
    padding = "=" * (-len(normalized_key) % 8)
    try:
        signature = base64.b32decode(normalized_key + padding)
    except (binascii.Error, ValueError):
        return False
    try:
        _public_key().verify(signature, machine_id.strip().upper().encode("utf-8"))
        return True
    except InvalidSignature:
        return False


def _read_file_usage():
    if USAGE_FILE.exists():
        try:
            return int(json.loads(USAGE_FILE.read_text(encoding="utf-8")).get("clean_count", 0))
        except Exception:
            return 0
    return 0


def _write_file_usage(total):
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps({"clean_count": total}), encoding="utf-8")


def _read_registry_usage():
    if winreg is None:
        return 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, REGISTRY_VALUE_NAME)
            return int(value)
    except Exception:
        return 0


def _write_registry_usage(total):
    if winreg is None:
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH) as key:
            winreg.SetValueEx(key, REGISTRY_VALUE_NAME, 0, winreg.REG_DWORD, int(total))
    except Exception:
        pass


def load_usage():
    # Iki kaynaktan da okuyup BUYUK OLANI esas alir -- sadece birini silmek
    # kotayi geri getirmez.
    return max(_read_file_usage(), _read_registry_usage())


def add_usage(clean_count):
    if clean_count <= 0:
        return load_usage()
    total = load_usage() + clean_count
    _write_file_usage(total)
    _write_registry_usage(total)
    return total


def load_license_key():
    if LICENSE_FILE.exists():
        return LICENSE_FILE.read_text(encoding="utf-8").strip()
    return None


def save_license_key(key):
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(key.strip(), encoding="utf-8")


def is_licensed():
    return is_license_valid(get_machine_id(), load_license_key())


def remaining_trial():
    return max(0, TRIAL_LIMIT - load_usage())


def status_text():
    if is_licensed():
        return "Lisanslı sürüm"
    remaining = remaining_trial()
    if remaining <= 0:
        return f"Deneme süresi doldu ({TRIAL_LIMIT}/{TRIAL_LIMIT} temiz ASIN) — lisans anahtarı gerekli"
    return f"Deneme sürümü: {load_usage()} / {TRIAL_LIMIT} temiz ASIN kullanıldı"
