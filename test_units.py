"""Saf mantik / birim testleri -- Chrome, GUI ya da gercek Keepa API'si
GEREKTIRMEZ (test_gui_smoke.py'nin ucuncu katmaniyla ayni ruhta, ama GUI'ye
hic dokunmadan). Ag/harici sistem gerektiren TEK katman (`_fetch_product`)
sahte (mock) veriyle degistirilir.

Calistirma: python test_units.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keepa_check  # noqa: E402
import keepa_finder  # noqa: E402
import license_guard  # noqa: E402
import keygen  # noqa: E402

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"[OK]   {name}")
    else:
        FAIL.append((name, detail))
        print(f"[FAIL] {name} -- {detail}")


# ----------------------------------------------------------- yardimcilar

KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)


def kt(days_ago):
    """days_ago gun once icin Keepa-stili dakika damgasi."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return int((dt - KEEPA_EPOCH).total_seconds() / 60)


def make_product(count_new_series=None, title="Test Urunu", tracking_days=400):
    csv_data = [None] * 12
    if count_new_series is not None:
        csv_data[11] = count_new_series
    return {"title": title, "csv": csv_data, "stats": {}}


def with_mocked_fetch(fake_product_response, fn, *args, **kwargs):
    original = keepa_check._fetch_product
    keepa_check._fetch_product = lambda *a, **kw: fake_product_response
    try:
        return fn(*args, **kwargs)
    finally:
        keepa_check._fetch_product = original


def with_mocked_fetch_by_domain(responses_by_domain, fn, *args, **kwargs):
    """domain'e gore FARKLI sahte yanit -- hedef pazar kontrolu gibi, ayni
    ASIN icin BIRDEN FAZLA domain'e (5=JP, 1=US) ayri cagri yapan mantigi
    test etmek icin."""
    original = keepa_check._fetch_product

    def fake(asin, api_key, domain=None, stop_event=None):
        return responses_by_domain[domain]

    keepa_check._fetch_product = fake
    try:
        return fn(*args, **kwargs)
    finally:
        keepa_check._fetch_product = original


# --------------------------------------------------- keepa_check_detailed_api

def test_check_api_regression():
    # 1) Duzenli, kesintisiz 400 gunluk seri -> upload.
    series_clean = []
    for d in range(400, -1, -30):
        series_clean += [kt(d), 5]
    result = with_mocked_fetch(
        {"products": [make_product(series_clean)]},
        keepa_check.keepa_check_detailed_api, "B0TESTCLEAN", api_key="x",
    )
    check("API check: temiz seri -> upload", result["status"] == "upload", str(result))
    check("API check: temiz seri -> gap_count=0", result["gap_count"] == 0, str(result))

    # 2) Pencere icinde ORTADA bir -1 (kesinti) var -> delete, gap_count=1.
    series_gap = [kt(400), 5, kt(200), -1, kt(100), 5, kt(1), 5]
    result = with_mocked_fetch(
        {"products": [make_product(series_gap)]},
        keepa_check.keepa_check_detailed_api, "B0TESTGAP", api_key="x",
    )
    check("API check: ortada kesinti -> delete", result["status"] == "delete", str(result))
    check("API check: ortada kesinti -> gap_count=1", result["gap_count"] == 1, str(result))

    # 3) 1 yillik veri gecmisi YOK (ilk nokta 100 gun once) -> yillik_secenek_yok.
    series_short = [kt(100), 5, kt(1), 5]
    result = with_mocked_fetch(
        {"products": [make_product(series_short)]},
        keepa_check.keepa_check_detailed_api, "B0TESTSHORT", api_key="x",
    )
    check(
        "API check: 1 yil altinda gecmis -> yillik_secenek_yok",
        result["status"] == "delete" and result["reason"] == "yillik_secenek_yok", str(result),
    )

    # 4) products bos/None -> veri_yok.
    result = with_mocked_fetch(
        {"products": [None]}, keepa_check.keepa_check_detailed_api, "B0TESTNONE", api_key="x",
    )
    check("API check: products=[None] -> veri_yok", result["reason"] == "veri_yok", str(result))
    result = with_mocked_fetch(
        {"products": []}, keepa_check.keepa_check_detailed_api, "B0TESTEMPTY", api_key="x",
    )
    check("API check: products=[] -> veri_yok", result["reason"] == "veri_yok", str(result))

    # 5) csv[11] eksik -> cizgi_bulunamadi.
    result = with_mocked_fetch(
        {"products": [make_product(None)]}, keepa_check.keepa_check_detailed_api, "B0TESTNOCSV", api_key="x",
    )
    check("API check: csv[11] yok -> cizgi_bulunamadi", result["reason"] == "cizgi_bulunamadi", str(result))

    # 6) Tek elemanli/eslesmemis seri (bug fix regresyonu -- eskiden IndexError
    #    firlatiyordu) -> cizgi_bulunamadi, exception YOK.
    result = with_mocked_fetch(
        {"products": [make_product([kt(1)])]}, keepa_check.keepa_check_detailed_api, "B0TESTODD", api_key="x",
    )
    check(
        "API check: tek elemanli seri -> exception yok, cizgi_bulunamadi",
        result["reason"] == "cizgi_bulunamadi", str(result),
    )

    # 7) Gumruk riski tasiyan baslik (customs check acikken) -> delete.
    series_ok = [kt(400), 5, kt(1), 5]
    result = with_mocked_fetch(
        {"products": [make_product(series_ok, title="Lithium Battery Charger 5000mAh")]},
        keepa_check.keepa_check_detailed_api, "B0TESTCUSTOMS", api_key="x", check_customs_risk=True,
    )
    check(
        "API check: gumruk riskli baslik -> delete",
        result["status"] == "delete" and result["reason"].startswith("gumruk_riski"), str(result),
    )
    # Ayni baslik ama check_customs_risk=False iken ENGELLENMEMELI.
    result = with_mocked_fetch(
        {"products": [make_product(series_ok, title="Lithium Battery Charger 5000mAh")]},
        keepa_check.keepa_check_detailed_api, "B0TESTCUSTOMS2", api_key="x", check_customs_risk=False,
    )
    check(
        "API check: gumruk riskli baslik ama kontrol kapali -> upload",
        result["status"] == "upload", str(result),
    )

    # 8) Hedef pazar kontrolu (check_target_market) -- JP tarafi tertemiz
    #    ama ASIN hedef pazarda (US, domain=1) HIC bulunamiyor -> delete,
    #    reason=hedef_pazarda_yok. (99 gercek EasyCentral ornegiyle %100
    #    dogrulanmis gercek dunya senaryosu, bkz. proje konusma gecmisi.)
    result = with_mocked_fetch_by_domain(
        {5: {"products": [make_product(series_ok)]}, 1: {"products": [None]}},
        keepa_check.keepa_check_detailed_api, "B0TESTNOUS", api_key="x", check_target_market=True,
    )
    check(
        "API check: JP temiz ama hedef pazarda yok -> delete/hedef_pazarda_yok",
        result["status"] == "delete" and result["reason"] == "hedef_pazarda_yok", str(result),
    )

    # 9) Ayni JP verisi ama ASIN hedef pazarda DA var -> upload.
    result = with_mocked_fetch_by_domain(
        {5: {"products": [make_product(series_ok)]}, 1: {"products": [{"title": "US Title"}]}},
        keepa_check.keepa_check_detailed_api, "B0TESTHASUS", api_key="x", check_target_market=True,
    )
    check(
        "API check: JP temiz + hedef pazarda var -> upload",
        result["status"] == "upload", str(result),
    )

    # 10) check_target_market=False (varsayilan) iken hedef pazar HIC
    #     sorulmuyor -- ekstra token harcamiyor. domain=1 icin YANLISLIKLA
    #     bir _fetch_product cagrisi yapilirsa (mock'ta tanimsiz) KeyError
    #     firlar, bu da testin kendisini basarisiz eder -- yani bu test
    #     "hic cagrilmadi" garantisini dolayli olarak dogrular.
    result = with_mocked_fetch_by_domain(
        {5: {"products": [make_product(series_ok)]}},
        keepa_check.keepa_check_detailed_api, "B0TESTNOCHECK", api_key="x",
    )
    check(
        "API check: check_target_market=False -> hedef pazar hic sorulmuyor, upload",
        result["status"] == "upload", str(result),
    )

    # 11) target_market_domain ayarlanabilir -- varsayilan (1) yerine ozel
    #     bir domain (ornegin 6=Amazon.ca) verilirse GERCEKTEN o domain
    #     sorgulanmali. Mock'ta SADECE domain=6 icin veri var; eger kod hala
    #     sessizce domain=1'e sorarsa KeyError ile test patlar.
    result = with_mocked_fetch_by_domain(
        {5: {"products": [make_product(series_ok)]}, 6: {"products": [{"title": "CA Title"}]}},
        keepa_check.keepa_check_detailed_api, "B0TESTCUSTOMDOM", api_key="x",
        check_target_market=True, target_market_domain=6,
    )
    check(
        "API check: ozel target_market_domain=6 gercekten kullaniliyor -> upload",
        result["status"] == "upload", str(result),
    )


# ------------------------------------------------------------- license_guard

def test_license_guard():
    machine_a = "TEST-MACHINE-AAAA"
    machine_b = "TEST-MACHINE-BBBB"
    key_a = keygen.generate_license_key(machine_a)

    check("Lisans: dogru makine icin uretilen key gecerli", license_guard.is_license_valid(machine_a, key_a))
    check("Lisans: baska makine icin GECERSIZ", not license_guard.is_license_valid(machine_b, key_a))

    tampered = key_a[:-1] + ("A" if key_a[-1] != "A" else "B")
    check("Lisans: bozulmus imza GECERSIZ", not license_guard.is_license_valid(machine_a, tampered))

    check("Lisans: bos key GECERSIZ", not license_guard.is_license_valid(machine_a, ""))
    check("Lisans: None key GECERSIZ", not license_guard.is_license_valid(machine_a, None))
    check("Lisans: rastgele metin GECERSIZ", not license_guard.is_license_valid(machine_a, "bu-gecerli-bir-key-degil"))

    # Kucuk harf / bosluk / tire toleransi -- kullanicinin kopyala-yapistir
    # sirasinda ekleyebilecegi varyasyonlar kabul edilmeli.
    messy = key_a.lower().replace("-", " ")
    check("Lisans: kucuk harf/bosluk varyasyonu hala gecerli", license_guard.is_license_valid(machine_a, messy))


# ----------------------------------------------------------- STRATEGIES guard

# Keepa'da bu alanlar icin -1 = 'bu teklif turu hic yok' (canli dogrulanmis,
# STRATEGY_BASE_RED_LINES'in yaninda belgelendi) -- 0 YANLISLIKLA yazilirsa
# Keepa totalResults'u SESSIZCE sifirliyor (bu proje bunu iki kez canli
# yasadi). Bu test, o hatanin BIR DAHA sessizce geri gelmemesini saglar.
OFFER_TYPE_SENTINEL_FIELDS = (
    "current_NEW_FBM_SHIPPING_gte", "current_NEW_FBM_SHIPPING_lte",
    "current_BUY_BOX_USED_SHIPPING_gte", "current_BUY_BOX_USED_SHIPPING_lte",
    "current_USED_gte", "current_USED_lte",
    "current_REFURBISHED_gte", "current_REFURBISHED_lte",
    "current_COLLECTIBLE_gte", "current_COLLECTIBLE_lte",
)


def _assert_sentinels(selection, label):
    for field in OFFER_TYPE_SENTINEL_FIELDS:
        if field in selection:
            check(
                f"Sentinel guard [{label}]: {field} == -1 (0 degil)",
                selection[field] == -1, f"gercek: {selection[field]!r}",
            )


def test_strategies_sentinel_guard():
    check("Domain: keepa_finder.DOMAIN == 5 (JP)", keepa_finder.DOMAIN == 5)
    check("Domain: keepa_check.API_DOMAIN == 5 (JP)", keepa_check.API_DOMAIN == 5)

    _assert_sentinels(keepa_finder.build_selection(1, 500000, 0), "build_selection")
    _assert_sentinels(keepa_finder.STRATEGY_BASE_RED_LINES, "STRATEGY_BASE_RED_LINES")
    for name in keepa_finder.STRATEGIES:
        selection = keepa_finder.build_strategy_selection(name, 1, 100000, 0)
        _assert_sentinels(selection, name)


def test_fetch_product_retries_network_errors():
    """2026-09-16'da full_pool_check.py'nin 108K'lik taramasinda canli
    dogrulandi: "hata" satirlarinin %95'i Keepa'yla ilgisiz DNS/baglanti
    hatasiydi (getaddrinfo failed vb.) ve HIC yeniden denenmiyordu --
    _fetch_product SADECE HTTP 429'u retry ediyordu. Bu test, URLError'in
    (DNS/baglanti) da retry edildigini ve birkac basarisizliktan SONRA
    basarili olursa dogru sonucu dondurdugunu dogrular."""
    import urllib.error
    import urllib.request

    original_urlopen = urllib.request.urlopen
    call_count = {"n": 0}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"products": [{"title": "OK"}]}'

    def fake_urlopen(request, timeout=30):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise urllib.error.URLError("getaddrinfo failed (sahte)")
        return _FakeResponse()

    original_sleep = keepa_check.time.sleep
    urllib.request.urlopen = fake_urlopen
    keepa_check.time.sleep = lambda s: None
    try:
        data = keepa_check._fetch_product("B0TESTNET", "fake-key")
        check(
            "_fetch_product: URLError (DNS/baglanti) 2 kez basarisiz olup 3. denemede basarili",
            call_count["n"] == 3 and data.get("products", [{}])[0].get("title") == "OK",
            f"call_count={call_count['n']}, data={data}",
        )
    finally:
        urllib.request.urlopen = original_urlopen
        keepa_check.time.sleep = original_sleep


def test_priority_score_v2_sales_volume():
    """2026-09-18'de musteriden geldi: skorlama artik sadece risk degil,
    "kanitlanmis satis hacmi" de iceriyor (monthlySold / trend). Bu test,
    sentetik stats verisiyle yeni bilesenlerin dogru agirlikta calistigini
    dogrular -- gercek API cagrisi yapmaz."""
    base_stats = {
        "outOfStockPercentage90": [-1, 0, -1],  # NEW=0 -> tam 25p
        "salesRankDrops90": 20,  # tavan -> tam 20p
        "buyBoxIsAmazon": False,  # tam 15p
        "totalOfferCount": 1,  # tam 15p
    }
    # monthlySold + trend YOK -> ikisi de notr (7.5 + 5 = 12.5), toplam 87.5
    product_no_volume = {"stats": dict(base_stats)}
    score_no_volume = keepa_check.compute_priority_score(product_no_volume)
    check(
        "Skor v2: monthlySold/trend eksikken notr fallback (87.5)",
        score_no_volume == 87.5,
        f"skor={score_no_volume}",
    )

    # monthlySold=50 (tavan) + trend=+100 (tavan) -> ikisi de tam puan, toplam 100
    stats_max_volume = dict(base_stats, monthlySold=50, deltaPercent90_monthlySold=100)
    score_max_volume = keepa_check.compute_priority_score({"stats": stats_max_volume})
    check(
        "Skor v2: monthlySold=50 + trend=+100 -> tavan (100.0)",
        score_max_volume == 100.0,
        f"skor={score_max_volume}",
    )

    # monthlySold=0 + trend=-100 (satis hacmi sifirlandi) -> ikisi de 0 puan
    stats_zero_volume = dict(base_stats, monthlySold=0, deltaPercent90_monthlySold=-100)
    score_zero_volume = keepa_check.compute_priority_score({"stats": stats_zero_volume})
    check(
        "Skor v2: monthlySold=0 + trend=-100 -> ikisi de 0 katki (75.0)",
        score_zero_volume == 75.0,
        f"skor={score_zero_volume}",
    )

    check(
        "Skor v2: sonuc her zaman 0-100 araliginda",
        0.0 <= score_no_volume <= 100.0 and 0.0 <= score_max_volume <= 100.0 and 0.0 <= score_zero_volume <= 100.0,
    )


def main():
    test_check_api_regression()
    test_license_guard()
    test_strategies_sentinel_guard()
    test_fetch_product_retries_network_errors()
    test_priority_score_v2_sales_volume()

    print(f"\n=== OZET: {len(PASS)} basarili, {len(FAIL)} basarisiz ===")
    if FAIL:
        for name, detail in FAIL:
            print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
