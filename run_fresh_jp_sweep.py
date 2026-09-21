"""Yeni bir JP Sales Rank taramasi (0-500000) -- ortak havuza dogrudan
yazarak full_pool_check.py'nin bir sonraki calismasinda otomatik
kullanilmasini saglar."""
from pathlib import Path
import time

import keepa_finder as kf

BASE_DIR = Path(__file__).resolve().parent
run_id = time.strftime("%Y%m%d_%H%M%S")
output_dir = BASE_DIR / f"keepa_arama_{run_id}"
shared_pool_path = BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt"

api_key = kf.load_api_key()

def progress(kind, payload):
    message = payload.get("message", "")
    if message:
        print(message, flush=True)

asins = kf.fetch_all_asins(
    output_dir, progress=progress, shared_pool_path=shared_pool_path, api_key=api_key,
)
print(f"\n=== TOPLAM: {len(asins)} benzersiz ASIN ===")
print(f"Klasor: {output_dir}")
