"""5 hazir stratejiyi (S1-S5) SIRAYLA calistirir -- yeni 6 aylik takip
sartina ve olu-ASIN eleme alanlarina gore (2026-09-14 guncellemesi).
Sirali calisir ki full_pool_check.py ile ayni token havuzunu boguşturmasin
(paralel calistirmak ikisini de yavaslatir, sirali daha az cakisma yaratir).
Her strateji kendi output_dir'ine yazar, ortak havuz (keepa_sync/ortak_asin_havuzu.txt)
otomatik dedup ile guncellenir (fetch_all_asins'in kendi mantigi).
CALISTIRMA: python run_all_strategies.py
"""
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import keepa_finder as kf

BASE_DIR = Path(__file__).resolve().parent
STRATEGIES = list(kf.STRATEGIES.keys())


def progress(kind, payload):
    msg = payload.get("message", "")
    if msg:
        print(msg, flush=True)


def main():
    api_key = kf.load_api_key()
    # ONEMLI: shared_pool_path ACIKCA verilmezse fetch_all_asins bunu
    # output_dir'in KARDESI olarak varsayiyor (BASE_DIR/keepa_arama_X'in
    # yaninda, yani duz BASE_DIR/ortak_asin_havuzu.txt) -- bu ASIL paylasilan
    # havuz (keepa_sync/ortak_asin_havuzu.txt) DEGIL. Bir kere bu hataya
    # dusup 18.343 ASIN'i yanlis dosyaya yazdik (elle duzeltildi, bkz.
    # 2026-09-14 notu) -- BURADA ACIKCA dogru yolu veriyoruz.
    shared_pool_path = BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt"
    for name in STRATEGIES:
        output_dir = BASE_DIR / f"keepa_arama_{name}"
        print(f"\n{'='*70}\n{name} baslatiliyor -> {output_dir}\n{'='*70}", flush=True)
        try:
            kf.fetch_strategy_asins(name, output_dir, progress=progress, api_key=api_key, shared_pool_path=shared_pool_path)
        except Exception as error:
            print(f"HATA ({name}): {type(error).__name__}: {error}", flush=True)
        time.sleep(2)
    print("\n>>> TUM STRATEJILER TAMAMLANDI.", flush=True)


if __name__ == "__main__":
    main()
