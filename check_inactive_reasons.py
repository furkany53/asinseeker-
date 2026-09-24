"""EasyCentral'da 'inaktif' olarak isaretlenen (ama Amazon'un GET_MERCHANT_
LISTINGS_ALL_DATA raporunda status=Inactive cikan) SKU'lari, Amazon SP-API'nin
Listings Items API'si (issues) ve Listings Restrictions API'si ile tek tek
tarayip iki gruba ayirir:

  - onay_istenebilir: issues'da QUALIFICATION_REQUIRED kategorisi var VE
    Restrictions API reasonCode=APPROVAL_REQUIRED ile bir basvuru linki
    donduruyor -- yani SILINMEMELI, once musteri Seller Central'da o linkten
    basvuru yapip onay almayi deneyebilir (fatura/belge gerektiren, API ile
    OTOMATIKLESTIRILEMEYEN bir surec -- canli dogrulandi, link Seller
    Central'in kendi web formuna gidiyor).
  - silinebilir: qualification/onay sorunu YOK ya da restriction API baska
    (onaylanamaz) bir sebep gosteriyor -- gercekten kalici sorunlu, guvenle
    silinebilir.

Girdi: keepa_full_kontrol/amazon_inaktif_liste.csv (seller-sku, product-id, ...)
Cikti: keepa_full_kontrol/inaktif_kategorize.csv (APPEND-ONLY, kesintiye
       dayanikli -- zaten islenmis SKU'lar atlanir, full_pool_check.py ile
       AYNI desen).

KULLANIM: nohup python check_inactive_reasons.py > check_inactive_reasons.log 2>&1 &
"""
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import amazon_sp_api as sp

BASE_DIR = Path(__file__).resolve().parent
# Komut satirindan dosya adi verilebilir (ayni script farkli inaktif
# donemlerini/listelerini tekrar tekrar islemek icin, orn. bugunku 2.
# tur icin amazon_inaktif_liste2.csv / inaktif_kategorize2.csv).
_suffix = sys.argv[1] if len(sys.argv) > 1 else ""
INPUT_FILE = BASE_DIR / "keepa_full_kontrol" / f"amazon_inaktif_liste{_suffix}.csv"
OUTPUT_FILE = BASE_DIR / "keepa_full_kontrol" / f"inaktif_kategorize{_suffix}.csv"

SELLER_ID = "A317XOQ608BK9W"  # canli dogrulandi, 2026-09-22
MARKETPLACE_ID = sp.MARKETPLACE_ID_JP

# SP-API Listings Items/Restrictions API'lerinin ikisi de dusuk kotali --
# canli dogrulanmadi (resmi limit belgesi okunmadi) ama guvenli tarafta
# kalmak icin muhafazakar bir hiz: saniyede ~2 istek.
REQUEST_INTERVAL = 0.8
MAX_RETRIES = 6


def dedupe_output():
    """Onceki calismalarda AYNI SKU icin birden fazla satir birikmis olabilir
    (bir 'hata' + sonraki denemede gercek sonuc) -- canli dogrulandi, 403
    firtinasi yuzunden olustu. Her SKU icin EN IYI sonucu (hata olmayan varsa
    onu, yoksa son hatayi) tutup dosyayi tekilleştirir."""
    if not OUTPUT_FILE.exists():
        return
    with OUTPUT_FILE.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    best = {}
    for row in rows:
        sku = row["seller-sku"]
        existing = best.get(sku)
        if existing is None or (existing["category"] == "hata" and row["category"] != "hata"):
            best[sku] = row
    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["seller-sku", "product-id", "category", "reason", "approval_link"])
        writer.writeheader()
        writer.writerows(best.values())
    print(f"Tekillestirildi: {len(rows)} satir -> {len(best)} benzersiz SKU", flush=True)


def already_processed():
    """SADECE gercekten sonuclanmis (hata olmayan) satirlari 'islenmis'
    sayar -- 403 hatasi (canli dogrulandi: bu Amazon hesabinda Listings
    Items API'si beklenenden dusuk bir rate-limit/throttle penceresine
    sahip gorunuyor, surekli istek atinca uzun sureli 403'e giriyor) yuzunden
    bircok satir hataya dusuyor, bunlarin YENIDEN denenmesi gerekiyor."""
    if not OUTPUT_FILE.exists():
        return set()
    with OUTPUT_FILE.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return {row["seller-sku"] for row in reader if row["category"] != "hata"}


def api_call_with_retry(access_token_holder, method, path, query=None):
    for attempt in range(MAX_RETRIES):
        try:
            return sp._sp_api_request(access_token_holder["token"], method, path, query=query)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                access_token_holder["token"] = sp.get_access_token()
                continue
            if e.code in (403, 429):
                # 403 burada token expiry DEGIL (canli dogrulandi, taze
                # token ile de olusuyor) -- throttle/rate-limit gibi
                # davraniyor, 429 ile AYNI uzun backoff uygulanmali.
                time.sleep(min(3 * (2 ** attempt), 60))
                continue
            raise
    raise RuntimeError(f"Basarisiz (retry tukendi): {path}")


def classify_sku(access_token_holder, sku, asin):
    time.sleep(REQUEST_INTERVAL)
    try:
        item = api_call_with_retry(
            access_token_holder, "GET", f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
            query={"marketplaceIds": MARKETPLACE_ID, "includedData": "issues"},
        )
    except Exception as e:
        return {"category": "hata", "reason": f"items_api: {e}", "approval_link": ""}

    issues = item.get("issues", [])
    has_qualification_issue = any(
        "QUALIFICATION_REQUIRED" in (issue.get("categories") or []) for issue in issues
    )
    if not has_qualification_issue:
        other_msgs = "; ".join(i.get("message", "") for i in issues[:3])
        return {"category": "silinebilir", "reason": other_msgs or "sorun bulunamadi", "approval_link": ""}

    time.sleep(REQUEST_INTERVAL)
    try:
        restr = api_call_with_retry(
            access_token_holder, "GET", "/listings/2021-08-01/restrictions",
            query={"asin": asin, "sellerId": SELLER_ID, "marketplaceIds": MARKETPLACE_ID, "conditionType": "new_new"},
        )
    except Exception as e:
        return {"category": "hata", "reason": f"restrictions_api: {e}", "approval_link": ""}

    for restriction in restr.get("restrictions", []):
        for r in restriction.get("reasons", []):
            if r.get("reasonCode") == "APPROVAL_REQUIRED":
                link = ""
                for l in r.get("links", []):
                    if l.get("verb") == "GET":
                        link = l.get("resource", "")
                        break
                return {"category": "onay_istenebilir", "reason": r.get("message", ""), "approval_link": link}

    return {"category": "silinebilir", "reason": "qualification sorunu var ama onay yolu yok", "approval_link": ""}


def main():
    dedupe_output()

    with INPUT_FILE.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    done = already_processed()
    print(f"Toplam: {len(rows)}, zaten islenmis: {len(done)}", flush=True)

    write_header = not OUTPUT_FILE.exists()
    access_token_holder = {"token": sp.get_access_token()}

    with OUTPUT_FILE.open("a", encoding="utf-8-sig", newline="") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=["seller-sku", "product-id", "category", "reason", "approval_link"])
        if write_header:
            writer.writeheader()

        processed_count = 0
        for row in rows:
            sku = row["seller-sku"]
            asin = row["product-id"]
            if sku in done:
                continue

            result = classify_sku(access_token_holder, sku, asin)
            writer.writerow({"seller-sku": sku, "product-id": asin, **result})
            out_f.flush()

            processed_count += 1
            if processed_count % 50 == 0:
                print(f"{processed_count} yeni islendi (toplam ilerleme: {len(done) + processed_count}/{len(rows)})", flush=True)

    dedupe_output()
    print("TAMAMLANDI", flush=True)


if __name__ == "__main__":
    main()
