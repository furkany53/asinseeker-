"""EasyCentral'daki CANLI envanteri (inventory-list.csv) "InventoryScore" ile
puanlar -- musterinin talebi: "gercek kar / buybox% / donusum" agirlikli bir
skor (SESSION_DURUM_2026-09-21.md'de "hala insa edilmedi, ertelendi" diye not
edilen ChatGPT onerisi).

Bu, compute_priority_score (keepa_check.py) ile AYNI SEY DEGIL -- o, HENUZ
magazada olmayan ADAY ASIN'leri Keepa sinyalleriyle (stok surekliligi, satis
hizi vb.) siralar. Bu script ise ZATEN magazada olan, GERCEK satis/rekabet
sonucu bulunan ASIN'leri puanlar -- "hangisini tutalim, hangisini cikaralim"
sorusuna cevap vermek icin. Musterinin istegi geregi FARKLI bir algoritma.

Veri kaynaklari (uc ayri rapor, ASIN uzerinden birlestirilir):
  1. inventory-list.csv (EasyCentral)  -- KATALOG/anlik durum: SATIS-FIYATI-KAR
     (teorik birim kar, USD), US-ALIS-FIYATI (USD maliyet), BUYBOX-DURUMUM
     (anlik 1/0 anlik snapshot), RAKIP-SATICI-SAYISI, SIPARIS-SAYISI (kumulatif).
  2. data (N).csv (Amazon Business Report + EasyCentral overlay) -- GERCEK
     donem-ici performans: BUYBOX % (raporlama penceresinde buybox'ta kalinan
     yuzde), UNITS SESSION PERCENTAGE (=gercek donusum orani), SESSION (trafik
     hacmi -- guvenilirlik esigi icin).
  3. satis_gecmisi_asin.csv (analyze_sales_report.py ciktisi, EasyCentral'in
     kendi SatisRaporu'ndan) -- GERCEK gerceklesmis kar orani (aylik genel
     gider satirlari zaten ayiklanmis, bkz. o script'in docstring'i).

Oncelik sirasi HER bilesen icin: GERCEK sonuc > KATALOG/teorik deger > NOTR
(veri yoksa/guvenilmezse) -- compute_priority_score'daki "eksik veri
cezalandirmaz ama avantaj da saglamaz" prensibiyle ayni.

Bilesenler (toplam 100 puan):
  - 40p KAR: once satis_gecmisi_asin.csv'deki GERCEK ortalama_kar_orani
    (gerceklesmis siparislerden); yoksa inventory-list'teki KATALOG marji
    (SATIS-FIYATI-KAR / (US-ALIS-FIYATI + SATIS-FIYATI-KAR) * 100 -- ikisi de
    USD, SATIS-FIYATI JPY oldugu icin KULLANILMADI). Hedef tavan %30 (satis
    gecmisindeki en iyi performanslara gore kalibre edildi, TARGET_PROFIT_RATE_PCT
    ile ayarlanabilir) -- %30+ marj tam puan, negatif marj 0 puan.
  - 30p BUYBOX%: data (N).csv'deki GERCEK "BUYBOX %" -- SESSION >=
    MIN_SESSIONS_FOR_BUYBOX_TRUST (=1) oldugunda GUVENILIR sayilir. Bu,
    DONUSUM bileseninden FARKLI bir esik -- BUYBOX % Amazon'un dogrudan
    olcup rapor ettigi "sure yuzdesi" (kac oturumdan kacinin sonuc verdigi
    degil), bu yuzden dusuk oturum sayisinda bile GURULTULU DEGIL. 2026-09-22
    CANLI DOGRULANDI: EasyCentral'in kendi "Envanterde zayif olabilecek
    urunleri getir" filtresiyle cikan 1.257 ASIN'lik liste incelendi --
    bunlarin SESSION'lari hep 1-10 arasinda (eski MIN_SESSIONS_FOR_TRUST=10
    esiginin TAMAMEN ALTINDA), ama REKABET-DURUMUM=1 (katalogda "rekabet
    edebiliyor" gorunen) 115 ASIN alt-grubunda GUNCEL BUYBOX%=0 orani %99.1
    (114/115) cikti -- yani dusuk-oturumlu ASIN'lerde bile BUYBOX%=0 gercek
    ve kalici bir sinyal, "veri yok" degil. Eski (session>=10) esikle bu 115
    ASIN ort. 64.2 puan aliyordu (notr donusum + KATALOG buybox-durumu
    fallback'i nedeniyle yapay sekilde sisirilmis); duzeltmeden sonra dogru
    sekilde cok dusuk puan aliyorlar. inventory-list'teki anlik BUYBOX-DURUMUM
    (1/0) SADECE business report'ta o ASIN icin HIC kayit yoksa (0 session)
    fallback olarak kullanilir.
  - 30p DONUSUM: data (N).csv'deki GERCEK "UNITS SESSION PERCENTAGE" --
    MIN_SESSIONS_FOR_CONVERSION_TRUST (=10) esigiyle, BUYBOX%'tan FARKLI
    (daha yuksek) esik: bu bir units/session ORANI, dusuk oturum sayisinda
    gercekten istatistiksel olarak gurultulu (1 oturumda 0 siparis, salt
    sansla da olabilir) -- BUYBOX% gibi Amazon'un dogrudan olcup rapor
    ettigi bir yuzde degil. Hedef tavan %10 (canli veride donusumu olan
    urunlerin ezici cogunlugu %10'un altinda kaliyor -- TARGET_CONVERSION_PCT
    ile ayarlanabilir).

Cikti: keepa_full_kontrol/envanter_skorlari.csv (TUM envanter, skora gore
sirali) -- en dusuk skorlular yer-degistirme/budama adayi, en yuksekler
"neden calisiyor" ogrenmek icin ornek.
"""
import csv
import glob
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS = Path(os.path.expanduser("~")) / "Downloads"
SATIS_GECMISI = BASE_DIR / "keepa_full_kontrol" / "satis_gecmisi_asin.csv"
OUT_CSV = BASE_DIR / "keepa_full_kontrol" / "envanter_skorlari.csv"

MIN_SESSIONS_FOR_BUYBOX_TRUST = 1
MIN_SESSIONS_FOR_CONVERSION_TRUST = 10
TARGET_PROFIT_RATE_PCT = 30.0
TARGET_CONVERSION_PCT = 10.0


def newest_matching(pattern):
    """Downloads'ta pattern'e uyan dosyalardan EN SON DEGISTIRILENI secer --
    musteri her yeni raporu indirdiginde Windows dosya adina ' (N)' ekliyor,
    sabit kodlanmis bir numara (select_replacement_batch.py'deki gibi) bir
    sonraki oturumda sessizce ESKI veriye kilitlenebiliyor."""
    matches = glob.glob(str(DOWNLOADS / pattern))
    if not matches:
        raise FileNotFoundError(f"Downloads'ta '{pattern}' ile eslesen dosya yok")
    return Path(max(matches, key=os.path.getmtime))


def widest_matching(pattern):
    """newest_matching'in aksine, EN COK SATIR ICEREN dosyayi secer. 'data (N).csv'
    adiyla hem TAM Business Report (Genel Filtre'siz, ~6000 ASIN) hem de
    EasyCentral'in "Onerilen Filtreler" (ornegin "zayif olabilecek urunleri
    getir") ile SUZULMUS, cok daha kucuk bir alt-kume export edilebiliyor --
    2026-09-22 CANLI YASANDI: mtime'a gore en yeniyi secince (data (3).csv,
    sadece 1.257 satirlik "zayif" filtre ciktisi) yanlislikla TAM rapor yerine
    kullanildi, envanterin buyuk kismi business-report'suz kalip notr'e dustu.
    Genel/filtresiz rapor HER ZAMAN filtreli bir alt-kumeden BUYUK olacagi icin
    "en genis" secim daha guvenilir bir sezgisel."""
    matches = glob.glob(str(DOWNLOADS / pattern))
    if not matches:
        raise FileNotFoundError(f"Downloads'ta '{pattern}' ile eslesen dosya yok")

    def row_count(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            return sum(1 for _ in csv.reader(f)) - 1

    # esit satir sayisinda (ayni raporu iki kez indirmis olabilir) EN YENIYI
    # tercih et -- salt satir sayisi tek basina yeterli ayirt edici degil.
    return Path(max(matches, key=lambda p: (row_count(p), os.path.getmtime(p))))


def to_float(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def to_int01(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def clamp01(x):
    return max(0.0, min(1.0, x))


def load_inventory():
    path = newest_matching("inventory-list*.csv")
    rows = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            asin = (row.get("ASIN") or "").strip()
            if asin:
                rows[asin] = row
    print(f"Envanter (katalog): {path.name} -- {len(rows)} ASIN")
    return rows


def load_business_report():
    path = widest_matching("data (*).csv")
    rows = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "UNITS SESSION PERCENTAGE" not in (reader.fieldnames or []):
            raise ValueError(
                f"{path.name} beklenen 'UNITS SESSION PERCENTAGE' kolonunu icermiyor -- "
                "bu Business Report + EasyCentral overlay raporu degil mi?"
            )
        for row in reader:
            asin = (row.get("CHILD ASIN") or "").strip()
            if asin:
                rows[asin] = row
    print(f"Business Report (gercek donem performansi): {path.name} -- {len(rows)} ASIN")
    return rows


def load_sales_history():
    rows = {}
    if not SATIS_GECMISI.exists():
        print(f"UYARI: {SATIS_GECMISI} yok -- kar bileseni tamamen katalog/notr'e dusecek. "
              "Once analyze_sales_report.py calistirilmali.")
        return rows
    with SATIS_GECMISI.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            asin = row["asin"].strip()
            rows[asin] = row
    print(f"Satis gecmisi (gercek kar): {SATIS_GECMISI.name} -- {len(rows)} ASIN")
    return rows


def profit_component(asin, inv_row, sales_row):
    if sales_row is not None and to_int01(sales_row.get("siparis_sayisi")):
        rate = to_float(sales_row.get("ortalama_kar_orani"))
        if rate is not None:
            return 40 * clamp01(rate / TARGET_PROFIT_RATE_PCT), rate, "gercek"

    kar = to_float(inv_row.get("SATIS-FIYATI-KAR"))
    maliyet = to_float(inv_row.get("US-ALIS-FIYATI"))
    if kar is not None and maliyet is not None and (maliyet + kar) > 0:
        rate = 100 * kar / (maliyet + kar)
        return 40 * clamp01(rate / TARGET_PROFIT_RATE_PCT), rate, "katalog"

    return 40 * 0.5, None, "notr"


def buybox_component(inv_row, biz_row):
    sessions = to_float(biz_row.get("SESSION")) if biz_row else None
    if biz_row is not None and sessions is not None and sessions >= MIN_SESSIONS_FOR_BUYBOX_TRUST:
        pct = to_float(biz_row.get("BUYBOX %"))
        if pct is not None:
            return 30 * clamp01(pct / 100.0), pct, "gercek"

    bb_bin = to_int01(inv_row.get("BUYBOX-DURUMUM (1:BUYBOX - 0:BUYBOX DEGIL)"))
    if bb_bin in (0, 1):
        return 30 * bb_bin, float(bb_bin * 100), "katalog_anlik"

    return 30 * 0.5, None, "notr"


def conversion_component(biz_row):
    sessions = to_float(biz_row.get("SESSION")) if biz_row else None
    if biz_row is not None and sessions is not None and sessions >= MIN_SESSIONS_FOR_CONVERSION_TRUST:
        pct = to_float(biz_row.get("UNITS SESSION PERCENTAGE"))
        if pct is not None:
            return 30 * clamp01(pct / TARGET_CONVERSION_PCT), pct, "gercek"

    return 30 * 0.5, None, "notr_trafik_yetersiz"


def main():
    inv_rows = load_inventory()
    biz_rows = load_business_report()
    sales_rows = load_sales_history()

    results = []
    for asin, inv_row in inv_rows.items():
        biz_row = biz_rows.get(asin)
        sales_row = sales_rows.get(asin)

        profit_pts, profit_rate, profit_src = profit_component(asin, inv_row, sales_row)
        buybox_pts, buybox_pct, buybox_src = buybox_component(inv_row, biz_row)
        conv_pts, conv_pct, conv_src = conversion_component(biz_row)

        score = round(profit_pts + buybox_pts + conv_pts, 1)
        results.append({
            "asin": asin,
            "envanter_skoru": score,
            "urun_adi": (inv_row.get("URUN ADI") or "")[:80],
            "kar_orani_pct": round(profit_rate, 2) if profit_rate is not None else "",
            "kar_kaynagi": profit_src,
            "buybox_pct": round(buybox_pct, 2) if buybox_pct is not None else "",
            "buybox_kaynagi": buybox_src,
            "donusum_pct": round(conv_pct, 2) if conv_pct is not None else "",
            "donusum_kaynagi": conv_src,
            "rakip_sayisi": inv_row.get("RAKIP-SATICI-SAYISI", ""),
            "siparis_sayisi_kumulatif": inv_row.get("SIPARIS-SAYISI", ""),
            "satis_fiyati": inv_row.get("SATIS-FIYATI", ""),
        })

    results.sort(key=lambda r: -r["envanter_skoru"])

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nYazildi: {OUT_CSV} ({len(results)} ASIN, skora gore sirali)")

    gercek_kar = sum(1 for r in results if r["kar_kaynagi"] == "gercek")
    gercek_bb = sum(1 for r in results if r["buybox_kaynagi"] == "gercek")
    gercek_conv = sum(1 for r in results if r["donusum_kaynagi"] == "gercek")
    print(f"\nBilesen kaynagi dagilimi (kac ASIN'de GERCEK veri kullanildi):")
    print(f"  Kar: {gercek_kar}/{len(results)} ({100*gercek_kar/len(results):.1f}%)")
    print(f"  Buybox%: {gercek_bb}/{len(results)} ({100*gercek_bb/len(results):.1f}%)")
    print(f"  Donusum: {gercek_conv}/{len(results)} ({100*gercek_conv/len(results):.1f}%)")

    scores = [r["envanter_skoru"] for r in results]
    print(f"\nSkor dagilimi: min={min(scores):.1f} max={max(scores):.1f} "
          f"ortalama={sum(scores)/len(scores):.1f}")

    print(f"\n=== EN YUKSEK SKORLU 15 (referans -- 'neden calisiyor') ===")
    for r in results[:15]:
        print(f"  {r['asin']} [{r['envanter_skoru']}] kar%={r['kar_orani_pct']}({r['kar_kaynagi']}) "
              f"buybox%={r['buybox_pct']}({r['buybox_kaynagi']}) donusum%={r['donusum_pct']} -- {r['urun_adi']}")

    print(f"\n=== EN DUSUK SKORLU 15 (yer-degistirme/budama adayi) ===")
    for r in results[-15:]:
        print(f"  {r['asin']} [{r['envanter_skoru']}] kar%={r['kar_orani_pct']}({r['kar_kaynagi']}) "
              f"buybox%={r['buybox_pct']}({r['buybox_kaynagi']}) donusum%={r['donusum_pct']} -- {r['urun_adi']}")


if __name__ == "__main__":
    main()
