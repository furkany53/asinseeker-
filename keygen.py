"""SADECE senin (yazilim sahibinin) kullanmasi icin -- bu dosyayi VE yaninda
uretilen license_signing_key.pem'i paylastigin/kurulum paketine koydugun
makinelere KOPYALAMA.

Ozel anahtar (license_signing_key.pem) burada yoksa ILK CALISTIRMADA
otomatik uretilir ve o an ekrana basilan public key'i license_guard.py'deki
_LICENSE_PUBLIC_KEY_HEX degiskenine SEN elle yapistirman gerekir (o dosya
musteriye giden exe'nin icinde, bu yuzden otomatik guncellenmez).

license_signing_key.pem'i KAYBETMEK gecmiste uretilmis lisans anahtarlarini
GECERSIZ KILMAZ (musterilerde calismaya devam ederler) ama YENI anahtar
URETEMEZ HALE GELIRSIN -- guvenli bir yere YEDEKLE, asla paylasma.

Kullanim: musteri "Lisans Anahtari Iste" ekranindaki Makine Kimligi'ni sana
gonderir, sen burada calistirip karsiligindaki anahtari ona geri yollarsin.
"""

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE_DIR = Path(__file__).resolve().parent
PRIVATE_KEY_FILE = BASE_DIR / "license_signing_key.pem"


def _load_or_create_private_key():
    if PRIVATE_KEY_FILE.exists():
        return serialization.load_pem_private_key(PRIVATE_KEY_FILE.read_bytes(), password=None)

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PRIVATE_KEY_FILE.write_bytes(pem)

    public_hex = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    print(f"\n>>> YENI imzalama anahtar cifti uretildi: {PRIVATE_KEY_FILE}")
    print(">>> Bu public key'i license_guard.py'deki _LICENSE_PUBLIC_KEY_HEX degiskenine yapistir:")
    print(f"    {public_hex}\n")
    print(">>> license_signing_key.pem dosyasini guvenli bir yere YEDEKLE ve ASLA paylasma.\n")
    return private_key


def generate_license_key(machine_id):
    private_key = _load_or_create_private_key()
    normalized = machine_id.strip().upper()
    signature = private_key.sign(normalized.encode("utf-8"))
    raw = base64.b32encode(signature).decode("ascii").rstrip("=")
    return "-".join(raw[i:i + 8] for i in range(0, len(raw), 8))


def main():
    if len(sys.argv) > 1:
        machine_id = sys.argv[1]
    else:
        machine_id = input("Makine Kimligi (musteriden gelen): ").strip()

    if not machine_id:
        print("Makine kimligi bos olamaz.")
        return

    key = generate_license_key(machine_id)
    print(f"\nMakine Kimligi : {machine_id}")
    print(f"Lisans Anahtari: {key}\n")


if __name__ == "__main__":
    main()
