"""Her kullanicinin KENDI Keepa API anahtarini girip saklamasi icin.

license_guard.py / keepa_credentials.py ile ayni desen: yerel makineye,
kullanicilar arasi paylasilmayan sekilde saklanir. Anahtar keyring
(Windows Credential Manager) ile saklanir -- keepa_finder.py'nin kullandigi
"keepa_api_key.txt" dosyasi BIZIM kendi ic aracimiz icindi, bu MODUL ise
AsinSeeker'i kullanan HER MUSTERININ kendi anahtarini girmesi icindir.
"""

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request

try:
    import keyring
except ImportError:
    keyring = None

SERVICE_NAME = "AsinSeekerKeepaAPI"
ACCOUNT_NAME = "keepa_api_key"

# Kullaniciya rehberlik icin -- gercek Keepa arayuzunde dogrulandi (canli test edildi).
API_KEY_INSTRUCTIONS = (
    "Keepa API anahtarini almak icin:\n"
    "1) keepa.com adresine git ve hesabina giris yap\n"
    "2) Ust menuden 'API' sekmesine tikla\n"
    "3) 'Your Access' sekmesinde 'Private API access key' basligi altinda anahtarini gorursun\n"
    "4) Kopyalayip asagiya yapistir\n\n"
    "Not: Bu ozellik Keepa'nin API erisimi olan bir aboneligi gerektirir."
)


def save_api_key(key):
    key = (key or "").strip()
    if keyring is not None and key:
        try:
            keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, key)
            return
        except Exception:
            pass
    # keyring yoksa/basarisizsa sessizce hicbir yere yazmiyoruz -- yari
    # guvensiz bir duz metin dosyasi yerine, kullaniciya haber verilmeli
    # (cagiran taraf keyring'in calisip calismadigini kontrol edebilir).
    raise RuntimeError("Anahtar guvenli sekilde kaydedilemedi (keyring kullanilamiyor).")


def load_api_key():
    if keyring is None:
        return None
    try:
        return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        return None


def has_api_key():
    return bool(load_api_key())


def check_account_status(api_key):
    """Keepa'nin /token ucunu kullanir -- TAMAMEN UCRETSIZ (tokensConsumed=0,
    canli dogrulandi), token harcamadan hesap durumunu gosterir."""
    url = "https://api.keepa.com/token?" + urllib.parse.urlencode({"key": api_key})
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))
