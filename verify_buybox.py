"""EasyCentral'in 'BuyBox olduğum ürünler' filtresinin (inventory?filter=buybox
export'u) gercek dogrulugunu Amazon'un canli Pricing API'siyle (SellerId +
IsBuyBoxWinner) test eder. Kesintiye dayanikli, APPEND-ONLY -- full_pool_check.py
ile ayni desen.

Girdi: Downloads'daki EasyCentral BuyBox export'u (elle indirilen)
Cikti: keepa_full_kontrol/buybox_dogrulama.csv
"""
import csv
import random
import sys
import time
import urllib.error
from pathlib import Path

import amazon_sp_api as sp

INPUT_FILE = Path(r"C:\Users\onury\Downloads\inventory-list (3).csv")
OUTPUT_FILE = Path(__file__).resolve().parent / "keepa_full_kontrol" / "buybox_dogrulama.csv"
MY_SELLER_ID = "A317XOQ608BK9W"
SAMPLE_SIZE = 200
REQUEST_INTERVAL = 1.2
MAX_RETRIES = 6


def already_done():
    if not OUTPUT_FILE.exists():
        return set()
    with OUTPUT_FILE.open(encoding="utf-8-sig") as f:
        return {row["asin"] for row in csv.DictReader(f)}


def check_asin(access_token_holder, asin):
    for attempt in range(MAX_RETRIES):
        try:
            res = sp._sp_api_request(
                access_token_holder["token"], "GET", f"/products/pricing/v0/items/{asin}/offers",
                query={"MarketplaceId": sp.MARKETPLACE_ID_JP, "ItemCondition": "New"},
            )
            offers = res.get("payload", {}).get("Offers", [])
            my_offer = next((o for o in offers if o.get("SellerId") == MY_SELLER_ID), None)
            if my_offer is None:
                return "teklifim_yok"
            return "gercekten_buybox" if my_offer.get("IsBuyBoxWinner") else "buybox_bende_degil"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                access_token_holder["token"] = sp.get_access_token()
                continue
            if e.code in (403, 429):
                time.sleep(min(3 * (2 ** attempt), 60))
                continue
            return f"hata_{e.code}"
        except Exception as e:
            return f"hata_{e}"
    return "hata_retry_tukendi"


def main():
    with INPUT_FILE.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        all_asins = [row["ASIN"] for row in reader if row.get("ASIN")]

    random.seed(42)
    sample = random.sample(all_asins, min(SAMPLE_SIZE, len(all_asins)))

    done = already_done()
    print(f"Toplam EasyCentral BuyBox listesi: {len(all_asins)}, ornek: {len(sample)}, zaten yapilmis: {len(done)}", flush=True)

    write_header = not OUTPUT_FILE.exists()
    access_token_holder = {"token": sp.get_access_token()}

    with OUTPUT_FILE.open("a", encoding="utf-8-sig", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["asin", "result"])
        if write_header:
            writer.writeheader()

        count = 0
        for asin in sample:
            if asin in done:
                continue
            time.sleep(REQUEST_INTERVAL)
            result = check_asin(access_token_holder, asin)
            writer.writerow({"asin": asin, "result": result})
            out_f.flush()
            count += 1
            if count % 25 == 0:
                print(f"{count} islendi", flush=True)

    print("TAMAMLANDI", flush=True)


if __name__ == "__main__":
    main()
