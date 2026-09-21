"""Her ASIN'in HANGI arama yontemiyle (S1-S5 stratejisi, kategori taramasi,
genel Sales Rank taramasi, YA DA hangi RAKIP TURK SATICIDAN kopyalandigi)
bulundugunu geriye donuk olarak cikarip kalici bir haritaya (asin -> 5
haneli kod) yazar.

NEDEN: EasyCentral'a gonderilen her partiye bir "Stok Kodu" (SKU on eki)
atiyoruz. Ileride hangi SKU'nun daha cok sattigini gorunce, o SKU'nun
HANGI arama yontemiyle (ya da HANGI rakip saticidan) bulunmus urunlerden
olustugunu bilip ayni yontemi/saticiyi tekrar kullanabilelim diye
(musteriden geldi, 2026-09-17).

Kaynak (oncelik sirasiyla -- bir ASIN birden fazla yontemde bulunmus
olabilir, ILK rastlanan kazanir):
  1. keepa_sync/turk_saticilar/*.txt -- harvest_seller_asins.py'nin
     cektigi, DOGRULANMIS Turk rakip saticilarin storefront'lari (en
     spesifik/degerli sinyal -- zaten GERCEKTEN satiliyor).
  2. keepa_arama_S1..S5_*/tum_asinler.txt -- Buy Box stratejileri.
  3. keepa_arama_kategori_*/tum_asinler.txt -- kategori taramalari.
  4. Diger keepa_arama_*.txt -- genel Sales Rank taramasi.
"""

import csv
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = BASE_DIR / "keepa_full_kontrol" / "asin_kaynak_yontemi.csv"
SELLERS_DIR = BASE_DIR / "keepa_sync" / "turk_saticilar"

# (klasor/dosya adi eslesme deseni, 5 haneli kod, aciklama) -- SIRA ONEMLI:
# daha spesifik olanlar ONCE kontrol edilir.
METHOD_RULES = [
    ("S1_HOT_ROTATION", "S1HOT", "Strateji S1 (Hot Rotation)"),
    ("S2_BALANCED", "S2BAL", "Strateji S2 (Balanced)"),
    ("S3_LOW_COMPETITION", "S3LOW", "Strateji S3 (Low Competition)"),
    ("S4_FBM_FRIENDLY", "S4FBM", "Strateji S4 (FBM Friendly)"),
    ("S5_PROVEN_DEMAND", "S5PRV", "Strateji S5 (Proven Demand)"),
    ("kategori_", "KATEG", "Kategori bazli tarama"),
]
GENERIC_CODE = ("GENEL", "Genel Sales Rank taramasi (strateji/kategori disi)")


def classify(folder_name):
    for pattern, code, label in METHOD_RULES:
        if pattern in folder_name:
            return code, label
    return GENERIC_CODE


def find_seller_files():
    """(ASIN listesi dosyasi, kod, aciklama) -- harvest_seller_asins.py'nin
    yazdigi <isim>_<sellerId>.txt dosyalarindan. Kod, saticinin kisa
    isminin ilk 5 harfi (buyuk harf) -- ayni isimden birden fazla satici
    varsa sellerId'nin son karakterleriyle ayirt edilir."""
    results = []
    if not SELLERS_DIR.exists():
        return results
    seen_codes = set()
    for path in sorted(SELLERS_DIR.glob("*.txt")):
        match = re.match(r"^(.*)_([A-Z0-9]{10,})$", path.stem)
        if not match:
            continue
        name, seller_id = match.groups()
        base_code = re.sub(r"[^A-Za-z]", "", name).upper()[:5] or "SATIC"
        code = base_code
        suffix_i = 0
        while code in seen_codes:
            suffix_i += 1
            code = (base_code[: 5 - len(str(suffix_i))] + str(suffix_i))
        seen_codes.add(code)
        results.append((path, code, f"Rakip Turk satici: {name} ({seller_id})"))
    return results


def find_source_files():
    """(tum_asinler.txt yolu, siniflandirma-icin-kullanilacak-ad) ciftleri."""
    results = []
    for search_root in (BASE_DIR, BASE_DIR / "keepa_sync"):
        if not search_root.exists():
            continue
        for entry in search_root.iterdir():
            if entry.is_dir() and entry.name.startswith("keepa_arama_"):
                tum_asinler = entry / "tum_asinler.txt"
                if tum_asinler.exists():
                    results.append((tum_asinler, entry.name))
            elif entry.is_file() and entry.name.startswith("keepa_arama_") and entry.suffix == ".txt":
                results.append((entry, entry.name))
    return results


def main():
    seller_sources = find_seller_files()
    sources = find_source_files()
    print(f"{len(seller_sources)} satici dosyasi, {len(sources)} arama-yontemi dosyasi bulundu.")

    asin_to_method = {}
    per_method_count = {}

    # Satici kaynaklari ONCE islenir (en spesifik/degerli sinyal, oncelikli
    # kazansin diye -- bkz. dosya basi aciklama).
    for path, code, label in seller_sources:
        with path.open(encoding="utf-8") as f:
            for line in f:
                asin = line.strip().upper()
                if not asin:
                    continue
                if asin not in asin_to_method:
                    asin_to_method[asin] = (code, label)

    for path, name in sources:
        code, label = classify(name)
        with path.open(encoding="utf-8") as f:
            for line in f:
                asin = line.strip().upper()
                if not asin:
                    continue
                if asin not in asin_to_method:
                    asin_to_method[asin] = (code, label)

    for code, _label in asin_to_method.values():
        per_method_count[code] = per_method_count.get(code, 0) + 1

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["asin", "method_code", "method_detail"])
        for asin, (code, label) in asin_to_method.items():
            writer.writerow([asin, code, label])

    print(f"Toplam eslesen ASIN: {len(asin_to_method)}")
    print("Yontem bazinda dagilim:")
    for code, count in sorted(per_method_count.items(), key=lambda x: -x[1]):
        print(f"  {code}: {count}")
    print(f"Yazildi: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
