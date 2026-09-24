"""sonuclar.csv'deki 'upload' (uygun) ASIN'lerden, EasyCentral'a bir SONRAKI
parti olarak gonderilmeye hazir olanlari hesaplar.

IKI AYRI disleme kriteri var (ikisi de gerekli, biri digerinin yerine
gecmez):
  1) DAHA ONCE GONDERILMIS (keepa_sync/easycentral_batch_*.txt +
     skorlu_gonderim_listesi.txt) -- ayni ASIN'i EasyCentral'a tekrar
     gondermenin anlami yok, o taramayi zaten yapti.
  2) BILINEN ENGELLI (keepa_full_kontrol/bilinen_engelli_asinler.txt) --
     Keepa tarafinda "uygun" olsa BILE, Amazon'da (bu musteri hesabina ozel)
     marka onayi/gating yuzunden kalici inaktif dusup silinmis ASIN'ler.
     full_pool_check.py bunlari tekrar "uygun" bulmaya devam eder (Keepa
     acisindan dogrudur) ama BURADA ayriyoruz ki bir DAHA gonderilmesinler.

KULLANIM: python prepare_next_batch.py [cikti_dosyasi.txt]
"""
import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SONUCLAR = BASE_DIR / "keepa_full_kontrol" / "sonuclar.csv"
BLOCKED_FILE = BASE_DIR / "keepa_full_kontrol" / "bilinen_engelli_asinler.txt"
SENT_FILES = list((BASE_DIR / "keepa_sync").glob("easycentral_batch_*.txt")) + [
    BASE_DIR / "keepa_full_kontrol" / "skorlu_gonderim_listesi.txt",
]


def _read_asin_set(path):
    if not path.exists():
        return set()
    return {line.strip().upper() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def compute_next_batch():
    upload_asins = set()
    with SONUCLAR.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["status"] == "upload":
                upload_asins.add(row["asin"])

    sent = set()
    for fp in SENT_FILES:
        sent |= _read_asin_set(fp)

    blocked = _read_asin_set(BLOCKED_FILE)

    remaining = upload_asins - sent - blocked
    return {
        "toplam_upload": len(upload_asins),
        "daha_once_gonderilmis": len(sent),
        "bilinen_engelli": len(blocked),
        "hazir": sorted(remaining),
    }


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = compute_next_batch()
    print(f"sonuclar.csv toplam upload (benzersiz): {result['toplam_upload']}")
    print(f"daha once gonderilmis (batch dosyalari + skorlu): {result['daha_once_gonderilmis']}")
    print(f"bilinen engelli (bir daha gonderilmeyecek): {result['bilinen_engelli']}")
    print(f"SONRAKI PARTI ICIN HAZIR: {len(result['hazir'])}")
    if out_path:
        out_path.write_text("\n".join(result["hazir"]), encoding="utf-8")
        print(f"yazildi: {out_path}")


if __name__ == "__main__":
    main()
