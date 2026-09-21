"""keepa_kategori_plani.json'daki TUM kategori/bant kombinasyonlarini sirayla
gercekten tarayip ASIN cekeer -- build_category_plan.py'nin SADECE plan
cikardigi yerden devam eder. Her kombinasyon kendi keepa_arama_kategori_<catId>_<band>
klasorune yazar, ortak havuz (keepa_sync/ortak_asin_havuzu.txt) dedup ile guncellenir.
CALISTIRMA: python run_category_harvest.py
"""
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import keepa_finder as kf

BASE_DIR = Path(__file__).resolve().parent


def progress(kind, payload):
    msg = payload.get("message", "")
    if msg:
        print(msg, flush=True)


def main():
    api_key = kf.load_api_key()
    plan = kf.load_category_plan()
    if not plan:
        print("HATA: keepa_kategori_plani.json bulunamadi/bos.")
        return

    print(f"Toplam {len(plan)} kategori/bant kombinasyonu taranacak.\n")
    for i, entry in enumerate(plan, 1):
        output_dir = BASE_DIR / f"keepa_arama_kategori_{entry['catId']}_{entry['band']}"
        print(f"\n{'='*70}\n[{i}/{len(plan)}] {entry['path']} -- bant {entry['band']} "
              f"(Sales Rank {entry['sales_gte']}-{entry['sales_lte']}, ~{entry['total_results']} urun)\n{'='*70}", flush=True)
        try:
            kf.fetch_category_plan_asins(entry, output_dir, progress=progress, api_key=api_key)
        except Exception as error:
            print(f"HATA ({entry['path']} {entry['band']}): {type(error).__name__}: {error}", flush=True)

    print("\n>>> TUM KATEGORI/BANT KOMBINASYONLARI TARANDI.", flush=True)


if __name__ == "__main__":
    main()
