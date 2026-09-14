"""ChatGPT'den gelen 5'li JP dropshipping ASIN avlama stratejisini (S1-S5),
BASE 'kirmizi cizgiler' ile birlikte, GERCEK API key ile CANLI test eder.
Her strateji icin sadece totalResults + ilk birkac ASIN'i cekip (ucuz,
~11-20 token/strateji) gercek verimi gorup rapor eder -- tam hasat
YAPMADAN once, sayilarin gercekci olup olmadigini anlamak icin.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import gzip
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keepa_finder as kf  # noqa: E402

DOMAIN = 5
KEEPA_EPOCH_OFFSET_MIN = 21_564_000


def keepa_minutes(dt):
    return int(dt.timestamp() // 60) - KEEPA_EPOCH_OFFSET_MIN


now = datetime.now(timezone.utc)
ONE_YEAR_AGO = keepa_minutes(now - timedelta(days=365))
OFFERS_72H = keepa_minutes(now - timedelta(hours=72))

# $30-$600 -> bugunku kur yaklasik ¥152 -- ChatGPT'nin ¥4565-¥91293 degerleri
# yerine, projemizde zaten kullandigimiz yuvarlak araligi (LISTPRICE_MIN/MAX
# ile ayni mantik) kullanacagiz: ¥5000-¥90000.
BASE_RED_LINES = {
    "productType": ["0"],
    "singleVariation": True,
    "trackingSince_lte": ONE_YEAR_AGO,
    "current_BUY_BOX_SHIPPING_gte": 5000,
    "current_BUY_BOX_SHIPPING_lte": 90000,
    "current_COUNT_NEW_gte": 1,
    "current_COUNT_NEW_lte": 15,
    "avg365_COUNT_NEW_gte": 3,
    "current_BUY_BOX_USED_SHIPPING_gte": -1,
    "current_BUY_BOX_USED_SHIPPING_lte": -1,
    "current_USED_gte": -1,
    "current_USED_lte": -1,
    "current_REFURBISHED_gte": -1,
    "current_REFURBISHED_lte": -1,
    "current_COLLECTIBLE_gte": -1,
    "current_COLLECTIBLE_lte": -1,
    # current_COUNT_USED/REFURBISHED/COLLECTIBLE_lte: 0 alanlari CANLI
    # TESTTE totalResults'u SIFIRLADI -- Keepa'da "bu teklif turu hic yok"
    # degeri -1'dir (bizim kendi kodumuzun da guvendigi kural), 0 degil;
    # bu yuzden bu 3 alan kaldirildi, yukaridaki -1/-1 cifti zaten ayni
    # islevi dogru sekilde goruyor.
    "buyBoxIsUnqualified": False,
    "buyBoxIsPreorder": False,
    "buyBoxIsBackorder": False,
    "buyBoxIsPrimeExclusive": False,
    "isHazMat": False,
    "isHeatSensitive": False,
    "isAdultProduct": False,
    "outOfStockPercentage90_BB_lte": 25,
    "deltaPercent90_BUY_BOX_SHIPPING_gte": -20,
    "deltaPercent90_BUY_BOX_SHIPPING_lte": 20,
    "lastOffersUpdate_gte": OFFERS_72H,
    "perPage": 100,
    "page": 0,
}

STRATEGIES = {
    "S1_HOT_ROTATION": {
        "current_SALES_gte": 1, "current_SALES_lte": 80000,
        "avg90_SALES_lte": 120000, "salesRankDrops30_gte": 12,
        "current_COUNT_NEW_gte": 3, "current_COUNT_NEW_lte": 12,
        "buyBoxStatsSellerCount90_gte": 3, "buyBoxStatsSellerCount90_lte": 8,
        "buyBoxStatsTopSeller90_lte": 65, "buyBoxStatsAmazon90_lte": 15,
        "sort": [["current_SALES", "asc"], ["salesRankDrops30", "desc"]],
    },
    "S2_BALANCED": {
        "current_SALES_gte": 80001, "current_SALES_lte": 200000,
        "avg90_SALES_lte": 250000, "salesRankDrops90_gte": 15,
        "current_COUNT_NEW_gte": 2, "current_COUNT_NEW_lte": 10,
        "buyBoxStatsSellerCount90_gte": 2, "buyBoxStatsSellerCount365_gte": 3,
        "buyBoxStatsTopSeller90_lte": 75, "buyBoxStatsAmazon90_lte": 20,
        "sort": [["salesRankDrops90", "desc"], ["current_SALES", "asc"]],
    },
    "S3_LOW_COMPETITION": {
        "current_SALES_gte": 200001, "current_SALES_lte": 500000,
        "avg365_SALES_lte": 500000, "salesRankDrops365_gte": 30,
        "current_COUNT_NEW_gte": 1, "current_COUNT_NEW_lte": 6,
        "avg365_COUNT_NEW_gte": 3, "buyBoxStatsSellerCount365_gte": 3,
        "buyBoxStatsAmazon365_lte": 25,
        "sort": [["current_COUNT_NEW", "asc"], ["salesRankDrops365", "desc"]],
    },
    "S4_FBM_FRIENDLY": {
        "current_SALES_gte": 1, "current_SALES_lte": 250000,
        "buyBoxIsAmazon": False, "buyBoxIsFBA": False,
        "offerCountFBM_gte": 1, "offerCountFBM_lte": 8,
        "current_COUNT_NEW_gte": 2, "current_COUNT_NEW_lte": 12,
        "buyBoxStatsSellerCount90_gte": 2,
        "buyBoxStatsTopSeller90_lte": 75, "buyBoxStatsAmazon90_lte": 10,
        "salesRankDrops90_gte": 10,
        "sort": [["salesRankDrops90", "desc"], ["current_COUNT_NEW", "asc"]],
    },
    "S5_PROVEN_DEMAND": {
        "current_SALES_gte": 1, "current_SALES_lte": 300000,
        "monthlySold_gte": 50, "deltaPercent90_monthlySold_gte": 0,
        "current_COUNT_NEW_gte": 2, "current_COUNT_NEW_lte": 15,
        "buyBoxStatsSellerCount90_gte": 2, "buyBoxStatsAmazon90_lte": 20,
        "sort": [["monthlySold", "desc"], ["current_SALES", "asc"]],
    },
}


def query_keepa(selection, api_key, attempts=6):
    params = {"key": api_key, "domain": DOMAIN, "selection": json.dumps(selection, separators=(",", ":"))}
    url = "https://api.keepa.com/query?" + urllib.parse.urlencode(params)
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url)
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < attempts - 1:
                time.sleep(15)
                continue
            raise


def main():
    api_key = kf.load_api_key()
    print("BASE + her strateji icin CANLI test (sadece ilk sayfa, ucuz):\n")
    for name, overrides in STRATEGIES.items():
        selection = dict(BASE_RED_LINES)
        selection.update(overrides)
        try:
            data = query_keepa(selection, api_key)
        except Exception as error:
            print(f"{name}: HATA {error}")
            continue
        total = data.get("totalResults")
        asins = data.get("asinList") or []
        tokens_consumed = data.get("tokensConsumed")
        tokens_left = data.get("tokensLeft")
        err = data.get("error")
        print(f"{name}: totalResults={total}  ilkSayfa={len(asins)}  token={tokens_consumed}  kalanToken={tokens_left}  err={err}")
        if asins:
            print(f"   ornek ASIN'ler: {asins[:5]}")
        time.sleep(2)


if __name__ == "__main__":
    main()
