"""Web on-filtrenin (Product Viewer'daki "Tracking since" alanindan turetilen
has_full_year tahmini) DOGRULUGUNU, RASTGELE secilmis 100 ASIN uzerinde,
UC BAGIMSIZ yontemle karsilastirarak test eder:

  1) WEB    -- web_onfiltre.csv'deki has_full_year (Tracking since'e gore)
  2) API    -- keepa_check_detailed_api()'nin gercekte kullandigi mantik
               (csv[?]'deki ilk zaman damgasi >= 365 gun mu)
  3) CHROME -- keepa_check_detailed()'in Keepa sayfasinda GERCEKTEN "1Y"
               (1 yillik) aralik butonu var mi diye BAKMASI (en "gercek",
               kullanicinin kendi gozuyle gorecegiyle ayni sonuc)

Ciktiyi hem ekrana yazar hem validate_sonuclari.csv'ye kaydeder.
"""

import csv
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import full_pool_check as fpc  # noqa: E402
import keepa_finder as kf  # noqa: E402
from easycentral_keepa_bot import close_existing_chrome_processes, start_chrome_for_attachment  # noqa: E402
from keepa_check import keepa_check_detailed  # noqa: E402

OUT_CSV = Path(__file__).resolve().parent / "keepa_full_kontrol" / "validate_sonuclari.csv"
SAMPLE_SIZE = 100


def load_web_data():
    true_asins, false_asins = [], []
    with open(fpc.WEB_PREFILTER_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["has_full_year"] == "True":
                true_asins.append(row["asin"])
            elif row["has_full_year"] == "False":
                false_asins.append(row["asin"])
    return true_asins, false_asins


def compute_api_has_full_year_direct(asin, api_key):
    """API'nin ic mantigini (csv'deki ilk zaman damgasi >= 365 gun mu)
    dogrudan, keepa_check_detailed_api'nin ozel fonksiyonlarini kullanarak
    hesaplar -- boylece require_year bayragindan bagimsiz, HAM sonucu aliriz."""
    from datetime import datetime, timezone
    import keepa_check as kc

    data = kc._fetch_product(asin, api_key, domain=kc.API_DOMAIN)
    products = data.get("products") or []
    if not products or products[0] is None:
        return None
    csv_data = products[0].get("csv") or []
    series = csv_data[kc.COUNT_NEW_CSV_INDEX] if len(csv_data) > kc.COUNT_NEW_CSV_INDEX else None
    if not series:
        return None
    first_ts = series[0]
    now_minutes = int((datetime.now(timezone.utc) - kc._keepa_epoch()).total_seconds() / 60)
    return (now_minutes - first_ts) >= kc.API_WINDOW_DAYS * 1440


def chrome_has_full_year(asin):
    """keepa_check_detailed'in KENDI Chrome/CDP mantigini kullanarak,
    sayfada gercekten '1Y' araligi butonu var mi diye bakar."""
    try:
        result = keepa_check_detailed(asin, take_screenshot=False, require_year=True, check_gaps=False, check_dead_stock=False)
        if result["reason"] == "yillik_secenek_yok":
            return False
        if result["reason"] in ("veri_yok", "grafik_yuklenemedi", "cizgi_bulunamadi"):
            return None  # veri hic yuklenemedi, karsilastirma disi
        return True
    except Exception as error:
        return f"HATA: {error}"


def main():
    random.seed()
    true_asins, false_asins = load_web_data()
    print(f"Web on-filtrede: {len(true_asins)} '1 yillik var', {len(false_asins)} '1 yillik yok'")

    sample_true = random.sample(true_asins, min(50, len(true_asins)))
    sample_false = random.sample(false_asins, min(50, len(false_asins)))
    sample = [(a, True) for a in sample_true] + [(a, False) for a in sample_false]
    random.shuffle(sample)
    print(f"Rastgele secildi: {len(sample)} ASIN (50 'var' + 50 'yok' etiketli)")

    api_key = kf.load_api_key()

    print("\n1) API ile dogrudan kontrol ediliyor...")
    api_results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(compute_api_has_full_year_direct, asin, api_key): asin for asin, _ in sample}
        for future in as_completed(futures):
            asin = futures[future]
            try:
                api_results[asin] = future.result()
            except Exception as error:
                api_results[asin] = f"HATA: {error}"
    print(f"   {len(api_results)} ASIN icin API sonucu alindi.")

    print("\n2) Chrome ile (gercek Keepa sayfasi, '1Y' butonu var mi) kontrol ediliyor...")
    close_existing_chrome_processes()
    start_chrome_for_attachment(start_url="about:blank", headless=True)
    time.sleep(2)
    chrome_results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(chrome_has_full_year, asin): asin for asin, _ in sample}
        for future in as_completed(futures):
            asin = futures[future]
            try:
                chrome_results[asin] = future.result()
            except Exception as error:
                chrome_results[asin] = f"HATA: {error}"
    close_existing_chrome_processes()
    print(f"   {len(chrome_results)} ASIN icin Chrome sonucu alindi.")

    rows = []
    web_api_agree = web_chrome_agree = api_chrome_agree = 0
    web_api_total = web_chrome_total = api_chrome_total = 0
    all_three_agree = 0
    all_three_total = 0

    for asin, web_verdict in sample:
        api_verdict = api_results.get(asin)
        chrome_verdict = chrome_results.get(asin)
        row = {"asin": asin, "web": web_verdict, "api": api_verdict, "chrome": chrome_verdict}
        rows.append(row)

        if isinstance(api_verdict, bool):
            web_api_total += 1
            if web_verdict == api_verdict:
                web_api_agree += 1
        if isinstance(chrome_verdict, bool):
            web_chrome_total += 1
            if web_verdict == chrome_verdict:
                web_chrome_agree += 1
        if isinstance(api_verdict, bool) and isinstance(chrome_verdict, bool):
            api_chrome_total += 1
            if api_verdict == chrome_verdict:
                api_chrome_agree += 1
            all_three_total += 1
            if web_verdict == api_verdict == chrome_verdict:
                all_three_agree += 1

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["asin", "web", "api", "chrome"])
        writer.writeheader()
        writer.writerows(rows)

    print("\n=== SONUC ===")
    print(f"WEB vs API     : {web_api_agree}/{web_api_total} uyustu")
    print(f"WEB vs CHROME  : {web_chrome_agree}/{web_chrome_total} uyustu")
    print(f"API vs CHROME  : {api_chrome_agree}/{api_chrome_total} uyustu (bu ikisi zaten daha once dogrulanmisti)")
    print(f"UCU DE AYNI FIKIRDE: {all_three_agree}/{all_three_total}")
    print("\nUyusmayan ornekler:")
    for row in rows:
        vals = [row["web"], row["api"], row["chrome"]]
        bool_vals = [v for v in vals if isinstance(v, bool)]
        if len(set(bool_vals)) > 1:
            print(" ", row)
    print(f"\nTum detaylar: {OUT_CSV}")


if __name__ == "__main__":
    main()
