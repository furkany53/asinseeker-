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


def main():
    test_check_api_regression()
    test_license_guard()
    test_strategies_sentinel_guard()

    print(f"\n=== OZET: {len(PASS)} basarili, {len(FAIL)} basarisiz ===")
    if FAIL:
        for name, detail in FAIL:
            print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
