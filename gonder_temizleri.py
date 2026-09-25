"""Temiz (status=upload) ASIN'leri skora gore siralayip EasyCentral taramasina
gonderir (magazaya otomatik yukleme ACIK), kota tasmasin diye paketi kontrol
eder ve oncesinde/sonrasinda envanter karti kaydi alir.

Kullanim:  python gonder_temizleri.py [--max N] [--sku KOD] [--dry]

Haric tutulanlar: Amazon'da (SP-API) su an listeli olanlar, daha once Easy'ye
gonderilmis olanlar (easy_gonderilen_tum.txt) ve silinmis listeler.
Gonderilenler easy_gonderilen_tum.txt'ye eklenir (tekrar gonderilmesin).
Not: Easy'nin "Mağazana Yükle" akisi yerine tarama+otomatik yukleme kullanilir.
"""
import argparse
import csv
import sys
import time
import json
import urllib.request
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

KF = BASE_DIR / "keepa_full_kontrol"
SENT_LOG = KF / "easy_gonderilen_tum.txt"
SEED_FILES = [
    BASE_DIR / "keepa_sync" / "easycentral_batch_1.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_2.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_3.txt",
    KF / "yeni_batch_top25000.txt", KF / "kalan_dusuk_skorlu_batch.txt",
    KF / "batch4_top3000_yuklenecek.txt", KF / "magaza_yukle_top8200.txt",
    KF / "hepsi_gonder.txt", KF / "hazir_taramalar_gonder.txt",
]
DELETED_FILES = [KF / "silinecek_olu_stok_asin.txt", KF / "silinecek_inactive_asin.txt"]
SAFETY = 0.95  # limitin %95'inden fazlasina cikma


def read_set(path):
    if not Path(path).exists():
        return set()
    return {l.strip() for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()}


def candidates():
    from amazon_sp_api import fetch_inventory_report
    tmp = KF / "amazon_inventory_gonder.csv"
    res = fetch_inventory_report(str(tmp))
    if not res.get("ok"):
        raise RuntimeError(f"SP-API envanter raporu alinamadi: {res.get('message')}")
    in_store = {r["product-id"].strip() for r in csv.DictReader(tmp.open(encoding="utf-8-sig", newline=""))}
    sent = read_set(SENT_LOG)
    for p in SEED_FILES:
        sent |= read_set(p)
    deleted = set()
    for p in DELETED_FILES:
        deleted |= read_set(p)
    scores = {}
    with (KF / "sonuclar.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["status"] != "upload":
                continue
            try:
                scores[row["asin"].strip()] = float(row["score"])
            except (TypeError, ValueError):
                scores.setdefault(row["asin"].strip(), -1.0)
    excluded = in_store | sent | deleted
    ranked = sorted(((a, s) for a, s in scores.items() if a not in excluded), key=lambda x: -x[1])
    print(f"Temiz: {len(scores)}, magazada: {len(in_store)}, daha once gonderilen: {len(sent)}, "
          f"silinen: {len(deleted)} -> gonderilebilir aday: {len(ranked)}")
    return [a for a, _ in ranked]


def submit(asins, sku_code):
    import easycentral_target as et
    from easycentral_keepa_bot import DEBUG_ADDRESS
    from keepa_check import open_cdp_session
    tab, s = open_cdp_session(DEBUG_ADDRESS, tab=et._find_existing_tab(DEBUG_ADDRESS))
    try:
        s.call("Page.enable")
        s.call("Runtime.enable")
        s.call("Page.navigate", {"url": et.EASY_SEARCH_URL})
        time.sleep(14)
        s.eval_json("document.querySelectorAll('.market-input')[1].querySelector('.flag-list').click()")
        time.sleep(1)
        for _ in range(3):
            s.eval_json("""(function(){var cb=document.querySelectorAll('.market-input')[1].querySelector('input[id="0"]');
                if(cb && !cb.checked) cb.click();})()""")
            time.sleep(1)
            label = s.eval_json("document.querySelectorAll('.market-input')[1].querySelector('.flag-list span').textContent")
            if label and "Seçildi" in label:
                break
        else:
            raise RuntimeError("Magaza secilemedi")
        if not s.eval_json(et.PASTE_INTO_MARKED_EXPR % {"text": "\n".join(asins)}):
            raise RuntimeError("ASIN'ler yapistirilamadi")
        s.eval_json(et.SKU_CODE_SET_EXPR % {"code": sku_code})
        chk = s.eval_json(et.AUTO_UPLOAD_TO_STORE_CHECK_EXPR)
        if not (chk and chk.get("checked")):
            raise RuntimeError("Otomatik yukleme kutusu isaretlenemedi")
        time.sleep(2)
        clicked = s.eval_json("""(function(){var b=Array.from(document.querySelectorAll('button,a,div')).filter(
            e=>e.children.length<=2&&/Taramayı Başlat/.test(e.textContent||'')).pop(); if(!b) return false; b.click(); return true;})()""")
        if not clicked:
            raise RuntimeError("Taramayi Baslat butonu bulunamadi")
        for _ in range(20):
            time.sleep(2)
            if "product-search/history" in (s.eval_json("location.href") or ""):
                return True
        return False
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="en fazla kac ASIN")
    ap.add_argument("--sku", default="GONDR")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    from snapshot_envanter import take_snapshot
    before = take_snapshot("gonderim ONCESI")
    limit, pct = before["paket_limiti"], float(before["paket_kullanimi_pct"])
    free = int(limit * SAFETY - limit * pct / 100)
    print(f"Paket: %{pct} / {limit} -> guvenli bos yer: {free}")
    if free <= 0:
        print("Kota dolu, gonderilmedi.")
        return

    cand = candidates()
    n = min(free, args.max or len(cand), 25000, len(cand))
    batch = cand[:n]
    print(f"Gonderilecek: {n}")
    if args.dry or not batch:
        return
    ok = submit(batch, args.sku)
    if ok:
        with SENT_LOG.open("a", encoding="utf-8") as f:
            f.write("\n".join(batch) + "\n")
    print("Tarama basladi." if ok else "UYARI: gecis dogrulanamadi -- Urun Arama Gecmisi'ni kontrol et.")
    take_snapshot(f"gonderim SONRASI ({n} ASIN, {args.sku})")


if __name__ == "__main__":
    main()
