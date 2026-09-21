"""Keepa Product Finder API (/query) uzerinden, senin Product Finder
ekraninda kurdugun kriterlere uyan ASIN'leri toplu cekip bir .txt dosyasina
yazar.

NEDEN "sales rank kaydirarak"?
Keepa'nin /query ucu, bir sorguya karsilik DONEN toplam sonucu sinirliyor
(perPage x sayfa derinligi ile bir tavana carpiyorsun). Aralik genisse
(orn. Sales Rank 0-10000 gibi genis bir bant, yuz binlerce urunu
kapsayabilir) tek sorguda hepsini alamazsin. Bunun yerine SALES RANK
araligini kucuk dilimlere bolup ("0-200", "200-400", ...) her dilim icin
ayri ayri sorgu atip TUM diger kriterleri (fiyat, offer count, vs.) sabit
tutuyoruz -- boylece her dilim kendi ic sinirinin altinda kalir ve toplamda
cok daha fazla essiz ASIN toplanmis olur.

KEEPA API ANAHTARI GEREKLI:
Bu script SENIN Keepa hesabinin API anahtarini kullanir ve her sorgu
Keepa "token" kotani duser (Product Finder PRO abonelik ile gelir).
Anahtari koda YAZMA -- ya ortam degiskeni olarak ver:
    setx KEEPA_API_KEY "senin-anahtarin"
ya da proje klasorune "keepa_api_key.txt" adinda, icinde SADECE anahtar
yazan bir dosya koy (bu dosya .gitignore'da, asla paylasilmaz/commitlenmez).

KULLANIM:
    python keepa_finder.py
Cikti: keepa_arama_<zaman damgasi>/ klasoru icinde:
  - tum_asinler.txt          hepsi bir arada (AsinSeeker'a yuklemeye hazir)
  - asin_sales_rank_map.csv  her ASIN hangi Sales Rank araligindan geldi
  - sales_BBBBB-EEEEE.txt    her dilim kendi ayri dosyasinda
Her dilim bitince ANINDA diske yazilir -- saatlerce suren tarama arada
kesilse bile o ana kadarki hicbir sey kaybolmaz.

Ayrica TUM oturumlardan (farkli filtre/kriterlerle yapilanlar dahil)
bulunan ASIN'ler, klasor farki gozetmeksizin tek bir ORTAK havuzda
(SHARED_POOL_PATH -- "ortak_asin_havuzu.txt") birikir.
"""

import copy
import csv
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse

# Windows konsolu/dosyaya yonlendirme cp1254 gibi dar bir kod sayfasi
# kullanabiliyor; Keepa'nin hata govdesi (veya baska bir dinamik metin)
# beklenmedik bir Unicode karakter icerirse ciplak print() TUM SCRIPT'I
# COKERTIYORDU (yasandi, dogrulandi -- saatlerce surecek bir tarama boyle
# bir tek karakter yuzunden bastan sona gitti). UTF-8 + 'replace' ile bu
# imkansiz hale geliyor.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# PyInstaller onefile ile paketlenince __file__ GERCEK exe konumunu degil,
# programin her calistirmada silinen GECICI bir cikarma klasorunu gosterir
# (sys._MEIxxxxx) -- "keepa_api_key.txt"yi exe'nin yanina koysan bile hicbir
# zaman BULUNAMAZDI (denendi, dogrulandi). Frozen (paketlenmis) halde
# sys.executable'in bulundugu GERCEK klasoru kullanmamiz gerekiyor.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
API_KEY_FILE = BASE_DIR / "keepa_api_key.txt"

# Farkli tarama kriterleriyle (farkli fiyat araligi, domain, vs.) yapilan
# TUM oturumlardan bulunan ASIN'lerin biriktigi TEK, ORTAK havuz -- her
# oturumun kendi klasoru (resume/tekrar taramama mantigi icin) ayri kalsa
# bile, "hangi kriterle bulundu" onemi olmadan tek bir listede toplanir.
# Bu dosya BASE_DIR'de (exe/script neredeyse orada) durur, klasor bazli degildir.
SHARED_POOL_PATH = BASE_DIR / "ortak_asin_havuzu.txt"

# --- Ayarlanabilir kriterler (Product Finder ekranindan aynen aktarildi) ---
DOMAIN = 5  # 5 = amazon.co.jp (ekran goruntusundeki .CO.JP ile eslesir)
LISTPRICE_MIN = 4500
LISTPRICE_MAX = 150000
OFFER_COUNT_MIN = 1
OFFER_COUNT_MAX = 10
RECENT_OFFERS_UPDATE_DAYS = 1  # "offers son X gun icinde guncellendi" -- Keepa-dakikasina cevrilir
# NOT: Bu deger 3'ten 1'e dusuruldu -- "satistan kaldirilmis" oranini azaltmak
# icin: satici tekliflerinin SADECE son 24 saatte guncellendigi urunler,
# gunler once guncellenmis olanlara gore "hala aktif satiliyor" ihtimali
# cok daha yuksek urunlerdir. Yine de bu KESIN bir garanti degildir --
# bkz. asagidaki not.

# --- Ek dropshipping filtreleri (hepsi opsiyonel -- None ise sorguya hic
# eklenmez, davranis degismez). AsinSeeker'in "ASIN Bul" sekmesinden
# doldurulabilir.
SALES_RANK_DROPS_30_MIN = None  # son 30 gunde en az bu kadar Sales Rank dususu (=satis sinyali)
SALES_RANK_DROPS_90_MIN = None  # son 90 gunde en az bu kadar Sales Rank dususu
MONTHLY_SOLD_MIN = None  # Keepa'nin tahmini "aylik satilan adet" alani
AVAILABILITY_AMAZON_EXCLUDE = None  # 1/True ise Amazon'un kendisinin sattigi urunleri disla
PACKAGE_WEIGHT_GRAMS_MAX = None  # paket agirligi ust siniri (gram)
ROOT_CATEGORY = None  # Keepa kategori agacindaki bir Root Category ID

# --- Musterinin KENDI Keepa Finder ekraninda gorsel olarak kurup, Keepa'nin
# web sitesinden disa aktardigi TAM sorgu JSON'u -- doluysa (None degilse)
# YUKARIDAKI TUM manuel alanlarin (DOMAIN haric) yerine GECER: build_selection
# bu sozlugu oldugu gibi baz alir, sadece Sales Rank dilimi/sayfalama
# alanlarini (bizim taramamizin calismasi icin ZORUNLU) uzerine yazar --
# musterinin kendi kurdugu diger HICBIR filtreye dokunmaz.
PERSONAL_SELECTION_BASE = None

# --- Sales Rank taramasi: 0'dan bu tavana kadar, adim adim kaydirarak ---
SALES_RANK_START = 0
SALES_RANK_END = 500000
SALES_RANK_STEP = 5000  # her sorgu bu genislikte bir dilimi kapsar

# ONEMLI TOKEN OPTIMIZASYONU (dogrulandi, canli API ile test edildi):
# Keepa'nin token maliyeti = 10 (sabit taban) + ceil(donen_sonuc/100). Yani
# sabit 10 token her cagrida ODENIYOR, kac sonuc istedigine bakmaksizin --
# bu yuzden COK SAYIDA KUCUK cagri yapmak (perPage=100, dilim basina 2-3
# sayfa) her seferinde bu sabit vergiyi tekrar tekrar odemek demek. perPage'i
# Keepa'nin izin verdigi tavana (page=0 iken 10000) cikarip dilimleri de
# genisletince AYNI kapsama alani COK DAHA AZ cagriyla taraniyor:
#   Eski: Sales Rank 0-2000, perPage=100 -> ~10 dilim x ~3 sayfa x 11 token = ~330 token
#   Yeni: Sales Rank 0-2000, perPage=10000, TEK cagri -> 1716 sonuc, 28 token
# Yaklasik %92 token tasarrufu (dogrulandi).
PER_PAGE = 10000  # Keepa'nin page=0 icin izin verdigi maksimum
MAX_PAGES_PER_SLICE = 20  # bir dilimde bu kadar sayfadan fazlasi varsa uyar (dilim cok genis demektir)

MIN_TOKENS_BEFORE_PAUSE = 20  # token bu seviyenin altina duserse yenilenmeyi bekle (eksiye dusmeden erken davran)


def load_api_key():
    # Bu, SADECE gelistiricinin kendi CLI scriptleri (keepa_finder.py,
    # full_pool_check.py, run_web_prefilter.py) icin -- musteriye giden
    # exe'de kullanilmamasi GEREKIR (musteri kendi anahtarini
    # keepa_api_settings.py / keyring uzerinden girer). Frozen exe icinde
    # BURAYA erisilmesi -- bugunku kod yollarinda erisilmiyor ama gelecekte
    # bir GUI cagrisi yanlislikla buraya baglanirsa -- musteri makinesinden
    # sessizce SENIN kisisel Keepa kotani tuketmeye baslar; bu yuzden boyle
    # bir durumda ACIKCA ve HEMEN patlıyoruz (sessiz/gec fark edilen bir
    # sizinti yerine, gelistirme sirasinda hemen yakalanan bir hata).
    if getattr(sys, "frozen", False):
        raise RuntimeError(
            "load_api_key(): bu SADECE gelistirici CLI'si icin -- musteriye giden exe "
            "icinde cagrilmamasi gerekiyordu. keepa_api_settings.load_api_key() kullan."
        )
    env_key = os.getenv("KEEPA_API_KEY")
    if env_key:
        return env_key.strip()
    if API_KEY_FILE.exists():
        key = API_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    raise RuntimeError(
        "Keepa API anahtari bulunamadi. Ya 'KEEPA_API_KEY' ortam degiskenini "
        f"ayarla, ya da {API_KEY_FILE} dosyasina anahtarini yaz (tek satir)."
    )


def keepa_minutes(dt):
    keepa_epoch = datetime(2011, 1, 1, tzinfo=timezone.utc)
    return int((dt - keepa_epoch).total_seconds() / 60)


def build_selection(sales_gte, sales_lte, page):
    if PERSONAL_SELECTION_BASE is not None:
        # Musterinin Keepa Finder'dan disa aktardigi sorguyu AYNEN kullan --
        # sadece bizim dilimleme/sayfalama mantigimizin calismasi icin
        # ZORUNLU olan alanlari uzerine yaziyoruz, gerisine dokunmuyoruz.
        selection = copy.deepcopy(PERSONAL_SELECTION_BASE)
        selection["current_SALES_gte"] = sales_gte
        selection["current_SALES_lte"] = sales_lte
        selection["perPage"] = PER_PAGE
        selection["page"] = page
        selection.setdefault("sort", [["current_SALES", "asc"], ["monthlySold", "desc"]])
        return selection

    selection = {
        "productType": ["0"],
        "singleVariation": True,
        "current_SALES_gte": sales_gte,
        "current_SALES_lte": sales_lte,
        "current_NEW_FBM_SHIPPING_gte": -1,
        "current_NEW_FBM_SHIPPING_lte": -1,
        "current_BUY_BOX_USED_SHIPPING_gte": -1,
        "current_BUY_BOX_USED_SHIPPING_lte": -1,
        "current_USED_gte": -1,
        "current_USED_lte": -1,
        "current_REFURBISHED_gte": -1,
        "current_REFURBISHED_lte": -1,
        "current_COLLECTIBLE_gte": -1,
        "current_COLLECTIBLE_lte": -1,
        "current_LISTPRICE_gte": LISTPRICE_MIN,
        "current_LISTPRICE_lte": LISTPRICE_MAX,
        "totalOfferCount_gte": OFFER_COUNT_MIN,
        "totalOfferCount_lte": OFFER_COUNT_MAX,
        "avg365_COUNT_NEW_gte": 1,
        "avg365_COUNT_NEW_FBM_gte": 1,
        "sort": [["current_SALES", "asc"], ["monthlySold", "desc"]],
        "lastOffersUpdate_gte": keepa_minutes(
            datetime.now(timezone.utc) - timedelta(days=RECENT_OFFERS_UPDATE_DAYS)
        ),
        "perPage": PER_PAGE,
        "page": page,
    }
    # Opsiyonel ek filtreler -- None birakilirsa sorguya hic eklenmez,
    # eski davranis (hicbir ek filtre yokken) aynen korunur.
    if SALES_RANK_DROPS_30_MIN is not None:
        selection["salesRankDrops30_gte"] = SALES_RANK_DROPS_30_MIN
    if SALES_RANK_DROPS_90_MIN is not None:
        selection["salesRankDrops90_gte"] = SALES_RANK_DROPS_90_MIN
    if MONTHLY_SOLD_MIN is not None:
        selection["monthlySold_gte"] = MONTHLY_SOLD_MIN
    if AVAILABILITY_AMAZON_EXCLUDE:
        # Keepa: availabilityAmazon == -1 -> Amazon'un hic teklifi yok.
        selection["availabilityAmazon_gte"] = -1
        selection["availabilityAmazon_lte"] = -1
    if PACKAGE_WEIGHT_GRAMS_MAX is not None:
        selection["packageWeight_lte"] = PACKAGE_WEIGHT_GRAMS_MAX
    if ROOT_CATEGORY is not None:
        selection["rootCategory"] = [ROOT_CATEGORY]
    return selection


# --- HAZIR STRATEJI PROFILLERI (Buy Box / dropshipping odakli) --------------
#
# NEREDEN GELDI: Baska bir sohbette (ChatGPT) JP dropshipping icin onerilen
# 5 avlama stratejisi -- Sales Rank yerine dogrudan "Buy Box kazanilabilirligi
# + gercek satis hizi + rekabet yogunlugu"nu hedefliyor. CANLI test edildi
# (gercek API key ile): ilk halinde current_COUNT_USED/REFURBISHED/
# COLLECTIBLE_lte:0 alanlari totalResults'u SIFIRLIYORDU -- Keepa'da "bu
# teklif turu hic yok" degeri -1'dir (bizim -1/-1 desenimizle ayni), 0
# degil; o 3 alan bu yuzden KALDIRILDI. Duzeltmeden sonra 5 strateji de
# CANLI dogrulandi (gercek, sifir olmayan sonuclar verdi):
#   S1_HOT_ROTATION=3018  S2_BALANCED=4045  S3_LOW_COMPETITION=4778
#   S4_FBM_FRIENDLY=10337  S5_PROVEN_DEMAND=1414
#
# Her stratejinin KENDI Sales Rank bandi vardir (S1: 0-80000, S2:
# 80001-200000, ...) -- bu yuzden bunlar genel 0-500000 taramasi gibi
# disaridan bir Sales Rank araligi VERILEREK degil, KENDI dogal bandinda
# calistirilir (bkz. fetch_strategy_asins).
STRATEGY_BASE_RED_LINES = {
    "productType": ["0"],
    "singleVariation": True,
    # trackingSince_lte disaridan (fetch_strategy_asins) her calistirmada
    # "bugun - STRATEGY_TRACKING_SINCE_DAYS gun" olarak enjekte edilir --
    # burada sabit birakilmaz.
    "current_BUY_BOX_SHIPPING_gte": 5000,
    "current_BUY_BOX_SHIPPING_lte": 90000,
    # current_NEW: "su an gercekten bir NEW fiyat var mi" -- BUY_BOX_SHIPPING'den
    # FARKLI bir price type, ikisi birlikte "olu listing" ihtimalini dusurur
    # (ChatGPT onerisi, CANLI dogrulandi: S2'de sonucu sifirlamadi, 100 ASIN
    # donduruldu -- bkz. konusma gecmisi 2026-09-14).
    "current_NEW_gte": 5000,
    "current_NEW_lte": 90000,
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
    "buyBoxIsUnqualified": False,
    "buyBoxIsPreorder": False,
    "buyBoxIsBackorder": False,
    "buyBoxIsPrimeExclusive": False,
    "isHazMat": False,
    "isHeatSensitive": False,
    "isAdultProduct": False,
    "outOfStockPercentage90_BB_lte": 25,
    # NEW icin ayrica OOS yuzdesi -- "bugun tesadufen aktif gorunse bile son
    # 90 gunun cogunda NEW olarak satista degilse" adaylarini eler.
    "outOfStockPercentage90_NEW_lte": 15,
    # Dropshipping modeli icin: su an en az 1 FBM teklifi olmasi VE bu
    # teklifin Buy Box'a uygun olmasi -- S4_FBM_FRIENDLY'nin kendi override'i
    # (offerCountFBM_gte/lte) bunun UZERINE yazar, cakisma yok.
    "offerCountFBM_gte": 1,
    "buyBoxEligibleOfferCountsNewFBM_gte": 1,
    "deltaPercent90_BUY_BOX_SHIPPING_gte": -20,
    "deltaPercent90_BUY_BOX_SHIPPING_lte": 20,
}

# Kullanicidan ONAY alindi (2026-09-14): 1 yillik takip sarti, havuzu
# gereksiz kisitliyordu -- 6 aya (180 gun) cekildi. CANLI olcum: S2_BALANCED
# icin 365 gun=3850, 180 gun=4494 sonuc (~%17 artis). Bu, EasyCentral'daki
# UYGUN ORANINI degistirmez (o darbogaz Keepa disi -- yasakli marka/fiyat
# farki/FBM satici yoklugu), ama mutlak aday/uygun SAYISINI artirir --
# "magaza sayimiz yetersiz" sorununun cozumu tam olarak bu.
STRATEGY_TRACKING_SINCE_DAYS = 180

STRATEGIES = {
    "S1_HOT_ROTATION": {
        "sales_gte": 1, "sales_lte": 80000,
        "overrides": {
            "avg90_SALES_lte": 120000, "salesRankDrops30_gte": 12,
            "current_COUNT_NEW_gte": 3, "current_COUNT_NEW_lte": 12,
            "buyBoxStatsSellerCount90_gte": 3, "buyBoxStatsSellerCount90_lte": 8,
            "buyBoxStatsTopSeller90_lte": 65, "buyBoxStatsAmazon90_lte": 15,
            "sort": [["current_SALES", "asc"], ["salesRankDrops30", "desc"]],
        },
    },
    "S2_BALANCED": {
        "sales_gte": 80001, "sales_lte": 200000,
        "overrides": {
            "avg90_SALES_lte": 250000, "salesRankDrops90_gte": 15,
            "current_COUNT_NEW_gte": 2, "current_COUNT_NEW_lte": 10,
            "buyBoxStatsSellerCount90_gte": 2, "buyBoxStatsSellerCount365_gte": 3,
            "buyBoxStatsTopSeller90_lte": 75, "buyBoxStatsAmazon90_lte": 20,
            "sort": [["salesRankDrops90", "desc"], ["current_SALES", "asc"]],
        },
    },
    "S3_LOW_COMPETITION": {
        "sales_gte": 200001, "sales_lte": 500000,
        "overrides": {
            "avg365_SALES_lte": 500000, "salesRankDrops365_gte": 30,
            "current_COUNT_NEW_gte": 1, "current_COUNT_NEW_lte": 6,
            "avg365_COUNT_NEW_gte": 3, "buyBoxStatsSellerCount365_gte": 3,
            "buyBoxStatsAmazon365_lte": 25,
            "sort": [["current_COUNT_NEW", "asc"], ["salesRankDrops365", "desc"]],
        },
    },
    "S4_FBM_FRIENDLY": {
        "sales_gte": 1, "sales_lte": 250000,
        "overrides": {
            "buyBoxIsAmazon": False, "buyBoxIsFBA": False,
            "offerCountFBM_gte": 1, "offerCountFBM_lte": 8,
            "current_COUNT_NEW_gte": 2, "current_COUNT_NEW_lte": 12,
            "buyBoxStatsSellerCount90_gte": 2,
            "buyBoxStatsTopSeller90_lte": 75, "buyBoxStatsAmazon90_lte": 10,
            "salesRankDrops90_gte": 10,
            "sort": [["salesRankDrops90", "desc"], ["current_COUNT_NEW", "asc"]],
        },
    },
    "S5_PROVEN_DEMAND": {
        "sales_gte": 1, "sales_lte": 300000,
        "overrides": {
            "monthlySold_gte": 50, "deltaPercent90_monthlySold_gte": 0,
            "current_COUNT_NEW_gte": 2, "current_COUNT_NEW_lte": 15,
            "buyBoxStatsSellerCount90_gte": 2, "buyBoxStatsAmazon90_lte": 20,
            "sort": [["monthlySold", "desc"], ["current_SALES", "asc"]],
        },
    },
}

STRATEGY_LABELS = {
    "S1_HOT_ROTATION": "S1 - Hot Rotation (hizli satan + Buy Box donen)",
    "S2_BALANCED": "S2 - Balanced (satis/rekabet dengesi)",
    "S3_LOW_COMPETITION": "S3 - Low Competition (rekabeti azalmis)",
    "S4_FBM_FRIENDLY": "S4 - FBM Friendly (dropshipping'e en uygun)",
    "S5_PROVEN_DEMAND": "S5 - Proven Demand (kanitlanmis aylik satis)",
}


def build_strategy_selection(strategy_name, sales_gte, sales_lte, page):
    """STRATEGIES icindeki adlardan biri icin tam Keepa selection sozlugunu
    kurar. sales_gte/sales_lte, cagiran taraftan (fetch_strategy_asins'in
    ic dilimleme dongusunden) gelir -- stratejinin KENDI dogal bandi icinde
    kalir, genel 0-500000 taramasindan bagimsizdir."""
    strategy = STRATEGIES[strategy_name]
    selection = dict(STRATEGY_BASE_RED_LINES)
    selection["trackingSince_lte"] = keepa_minutes(
        datetime.now(timezone.utc) - timedelta(days=STRATEGY_TRACKING_SINCE_DAYS)
    )
    selection["lastOffersUpdate_gte"] = keepa_minutes(
        datetime.now(timezone.utc) - timedelta(days=RECENT_OFFERS_UPDATE_DAYS)
    )
    selection.update(strategy["overrides"])
    selection["current_SALES_gte"] = sales_gte
    selection["current_SALES_lte"] = sales_lte
    selection["perPage"] = PER_PAGE
    selection["page"] = page
    return selection


def fetch_strategy_asins(strategy_name, output_dir, progress=None, stop_event=None, shared_pool_path=None, api_key=None):
    """Tek bir hazir stratejiyi (STRATEGIES) KENDI dogal Sales Rank bandinda
    calistirir. fetch_all_asins'in AYNI kesintiye-dayanikli/aninda-yazan
    motorunu kullanir -- tek fark, dilimleme araliginin (start/end/step)
    stratejinin kendi bandi olmasi (tek "dilim" olarak, ic sayfalama
    Keepa'nin kendi sayfa mekanizmasiyla halledilir)."""
    strategy = STRATEGIES[strategy_name]
    sales_gte, sales_lte = strategy["sales_gte"], strategy["sales_lte"]
    # output_dir burada (fetch_strategy_asins cagiranlari -- GUI, run_all_strategies.py)
    # BASE_DIR'in DOGRUDAN altinda ("keepa_arama_S1_..." gibi), keepa_sync/'in
    # ICINDE DEGIL -- fetch_all_asins'in "output_dir'in kardesi" varsayilani
    # bu yuzden YANLIS konuma (BASE_DIR/ortak_asin_havuzu.txt) yazardi (CANLI
    # dogrulandi, 2 KERE yasandi -- 18.343 sonra 233 ASIN yanlis dosyaya
    # yazildi, elle duzeltildi). Burada ACIKCA dogru/kalici konumu veriyoruz.
    if shared_pool_path is None:
        shared_pool_path = BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt"

    def selection_builder(slice_start, slice_end, page):
        return build_strategy_selection(strategy_name, slice_start, slice_end, page)

    return fetch_all_asins(
        output_dir,
        sales_rank_start=sales_gte,
        sales_rank_end=sales_lte,
        sales_rank_step=(sales_lte - sales_gte + 1),
        progress=progress,
        stop_event=stop_event,
        shared_pool_path=shared_pool_path,
        selection_builder=selection_builder,
        api_key=api_key,
    )


# --- KATEGORI + DINAMIK SALES RANK BANT SISTEMI ----------------------------
#
# NEDEN: Sabit "0-500000 tara" (ya da tek bir sabit kategori-derinligi tavani)
# yanlis -- 100.000 Sales Rank, kucuk bir kategoride "hemen hemen hic satmiyor"
# demekken, dev bir kategoride (Electronics gibi) hala guclu satis anlamina
# gelebilir. Kullanicidan (2026-09-14) gelen "kategori kategori, once ana
# sonra alt sonra alt-alt kategoriye inelim" fikri, baska bir sohbette
# (ChatGPT) daha da olgunlastirildi: her kategorinin KENDI 'highestRank'ine
# GORE ORANLI 3 Sales Rank bandi + rank kotulestikce (B, C bantlari) daha
# fazla SATIS KANITI (salesRankDrops) isteyerek "kotu rank ama gercekten
# satiyor" urunleri de yakala.
#
# CANLI DOGRULAMA (2026-09-14):
#  - Category API COK UCUZ: 3 kategori ID'si TEK cagride 1 token.
#  - categories_include (kok OLMAYAN bir alt kategori ID'siyle) GERCEKTEN
#    calisiyor: sifir olmayan, mantikli sonuc dondu (171 urun).
#  - salesRankReference alani (kategori rank karisikligini onlemesi
#    beklenen filtre) test edildi ama AYNI kategori ID'siyle 0 sonuc
#    dondurdu -- bu ya yanlis kullanim ya da beklenenden farkli calisiyor.
#    DOGRULANAMADI, bu yuzden BURADA KULLANILMIYOR (projenin "dogrulanmamis
#    alana guvenme" kurali geregi). Ileride token bolluğunda ayrica test
#    edilip eklenebilir.
CATEGORY_API_URL = "https://api.keepa.com/category"
CATEGORY_BATCH_SIZE = 10  # CANLI dogrulandi (2026-09-14): Keepa "Maximum allowed Category
# batch size is 10" hatasi (HTTP 405) donduruyor -- 100 varsayimimiz YANLISTI,
# 29 ID'lik gercek bir batch'te build_category_tree_plan'i cokertti. Dogru
# tavan 10.

# Bu isim parcalarini iceren kategoriler HIC islenmiyor -- ya fiziksel urun
# olmayan icerik (DVD/muzik/e-kitap/oyun/uygulama/Kindle -- productType:0
# zaten urun bazinda eler ama hic sablon uretmemek zaman/token tasarrufu
# saglar) ya da neredeyse tamami JP gumruk/mevzuat riski tasiyan urunler
# (eczane/ilac, kozmetik/sivi, gida/alkol -- kullanicidan geldi, 2026-09-14:
# "gumrukte sorun olacak basliklari direk eleyebilirsin"). Bu SADECE kaba
# bir on-eleme -- asil guvenlik agi keepa_check.py'deki BASLIK bazli
# CUSTOMS_RISK_KEYWORDS_PATTERN kontrolüdür, bu onun YERINE GECMEZ.
CATEGORY_NAME_EXCLUDE_SUBSTRINGS = (
    "DVD", "ミュージック", "PCソフト", "ゲーム", "Kindle", "Prime Video",
    "デジタルミュージック", "アプリ", "洋書", "Alexa", "本",
    "ドラッグストア", "ビューティー", "食品・飲料・お酒",
    # 2026-09-14, ikinci Gemini degerlendirmesi: bebek/cocuk urunleri JP'de
    # ekstra guvenlik sertifikasi (PSE vb.) riski tasiyor -- kok kategori
    # agacimizda canli dogrulandi ("ベビー＆マタニティ").
    "ベビー＆マタニティ",
)

# Kategorinin KENDI highestRank'ine GORE ORANLI 3 bant (ChatGPT'nin ornekleri
# ile tutarli: 450k'lik kategoride A:0-20%, B:20-53%, C:53-100%; 800k'lik
# kategoride A:0-18.75%, B:18.75-50%, C:50-100% -- burada biraz yuvarlatildi).
CATEGORY_BAND_FRACTIONS = (
    ("A", 0.0, 0.20),
    ("B", 0.20, 0.50),
    ("C", 0.50, 1.00),
)
# Rank kotulestikce (B, C) daha fazla satis KANITI istiyoruz -- "kotu rank
# ama duzenli rank hareketi var" urunleri de yakalamak icin (ChatGPT'nin
# mantigi: rank tek basina yeterli sinyal degil, dususlerle desteklenmeli).
CATEGORY_BAND_SALES_PROOF = {
    "A": {},
    "B": {"salesRankDrops90_gte": 5},
    "C": {"salesRankDrops90_gte": 15},
}

CATEGORY_DESCEND_IF_ABOVE = 3000  # bir bantta bu kadardan fazla eslesme varsa -- kategori COK GENIS, child'a in
CATEGORY_SKIP_IF_BELOW = 20  # bu kadardan az eslesme varsa -- bu bant/kategori kombinasyonu HARCAMAYA DEGMEZ, atla
CATEGORY_MAX_DEPTH = 2  # kok (0) + en fazla bu kadar seviye asagi (kullanicinin istedigi: ana/alt/alt-alt = 0,1,2)


def _category_name_excluded(name):
    if not name:
        return False
    return any(s in name for s in CATEGORY_NAME_EXCLUDE_SUBSTRINGS)


def _fetch_category_batch(api_key, category_ids, stop_event=None, max_retries=8):
    """Birden fazla kategori ID'sini TEK /category cagrisinda ceker (virgulle
    birlestirilmis) -- CANLI dogrulandi: 3 ID = 1 token. category_ids
    CATEGORY_BATCH_SIZE'lik parcalara bolunur (guvenli tavan).

    Paylasilan token havuzu baska bir surecle (full_pool_check.py, diger
    stratejiler) YARISIRKEN 429 almak CANLI dogrulandi (cok sik) -- bunu
    yakalamazsak TUM plan olusturma ilk cagride cokerdi (yasandi, 2026-09-14).
    fetch_all_asins'deki AYNI 'bekle ve tekrar dene' mantigini uyguluyoruz."""
    result = {}
    ids = list(category_ids)
    for i in range(0, len(ids), CATEGORY_BATCH_SIZE):
        chunk = ids[i:i + CATEGORY_BATCH_SIZE]
        params = {"key": api_key, "domain": str(DOMAIN), "category": ",".join(str(c) for c in chunk)}
        url = CATEGORY_API_URL + "?" + urllib.parse.urlencode(params)
        for attempt in range(max_retries):
            try:
                request = urllib.request.Request(url)
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8"))
                result.update(data.get("categories") or {})
                break
            except urllib.error.HTTPError as error:
                if error.code in (429, 400) and attempt < max_retries - 1:
                    wait_seconds = min(30 * (attempt + 1), 180)
                    interruptible_sleep(wait_seconds, stop_event)
                    continue
                raise
    return result


def build_category_band_selection(cat_id, sales_gte, sales_lte, band_letter, page=0):
    """Bir kategori + bant icin Keepa selection sozlugunu kurar. STRATEGY_BASE_RED_LINES
    ile AYNI temel guvenli/olu-ASIN-eleme kirmizi cizgilerini kullanir, kategori
    ve banda ozel Sales Rank araligi + satis-kaniti eklerini uzerine koyar."""
    selection = dict(STRATEGY_BASE_RED_LINES)
    selection["trackingSince_lte"] = keepa_minutes(
        datetime.now(timezone.utc) - timedelta(days=STRATEGY_TRACKING_SINCE_DAYS)
    )
    selection["lastOffersUpdate_gte"] = keepa_minutes(
        datetime.now(timezone.utc) - timedelta(days=RECENT_OFFERS_UPDATE_DAYS)
    )
    selection["categories_include"] = [cat_id]
    selection["current_SALES_gte"] = max(1, sales_gte)
    selection["current_SALES_lte"] = sales_lte
    selection.update(CATEGORY_BAND_SALES_PROOF.get(band_letter, {}))
    selection["perPage"] = PER_PAGE
    selection["page"] = page
    return selection


def _check_band_total_results(api_key, cat_id, sales_gte, sales_lte, band_letter, stop_event=None, max_retries=6):
    """UCUZ (perPage=100, ~11 token) bir on-kontrol -- gercek ASIN listesini
    cekmeden sadece o kategori/bant kombinasyonunda kac urun eslestigini
    ogrenir. Asil harcama (fetch_category_band_asins) sadece bu on-kontrolden
    'harcamaya deger' cikan kombinasyonlar icin yapilir.

    query_keepa HTTP hatalarini (429/400 -- paylasilan token havuzu baska bir
    surecle YARISIRKEN CANLI dogrulandi, cok sik oluyor) YAKALAMIYOR --
    burada fetch_all_asins'deki AYNI 'bekle ve tekrar dene' mantigini
    uyguluyoruz, aksi halde tum plan olusturma tek bir gecici hatada cokerdi."""
    selection = build_category_band_selection(cat_id, sales_gte, sales_lte, band_letter, page=0)
    selection["perPage"] = 100
    for attempt in range(max_retries):
        try:
            data = query_keepa(api_key, selection)
            return data.get("totalResults"), data
        except urllib.error.HTTPError as error:
            if error.code in (429, 400) and attempt < max_retries - 1:
                wait_seconds = min(30 * (attempt + 1), 180)
                interruptible_sleep(wait_seconds, stop_event)
                continue
            return None, None
        except Exception:
            return None, None
    return None, None


def build_category_tree_plan(api_key, progress=None, stop_event=None, max_depth=CATEGORY_MAX_DEPTH, save_callback=None):
    """Japonya kategori agacini kok'ten baslayip (fiziksel-disi/gumruk-riskli
    kategorileri dislayarak) asagi iner. Her kategori dugumu icin 3 dinamik
    Sales Rank bandini (highestRank'e oranli) UCUZ bir totalResults on-kontrolu
    ile degerlendirir:
      - COK GENIS (> CATEGORY_DESCEND_IF_ABOVE) -> bu bandi ATLA, child'lara IN
      - COK DAR (< CATEGORY_SKIP_IF_BELOW) -> bu bant harcamaya degmez, ATLA
      - ARADA -> "harcamaya hazir" bir sablon olarak PLAN'a ekle (henuz ASIN
        cekilmez -- bkz. fetch_category_plan_asins)
    Donen: [{"catId", "name", "path", "depth", "band", "sales_gte",
             "sales_lte", "total_results"}] listesi (JSON'a yazilabilir)."""
    def emit(msg):
        if progress:
            progress(msg)

    def stopped():
        return stop_event is not None and stop_event.is_set()

    plan = []
    root_data = _fetch_category_batch(api_key, [0], stop_event=stop_event)
    frontier = [
        (cid, info, [info.get("name")])
        for cid, info in root_data.items()
        if info.get("parent") == 0 and not _category_name_excluded(info.get("name"))
    ]
    emit(f"{len(frontier)} kok kategori bulundu (dislananlar haric).")

    depth = 0
    while frontier and depth <= max_depth and not stopped():
        emit(f"--- Derinlik {depth}: {len(frontier)} kategori degerlendiriliyor ---")
        next_frontier = []
        for cat_id, info, path in frontier:
            if stopped():
                break
            name = info.get("name") or str(cat_id)
            highest_rank = info.get("highestRank") or 0
            if not highest_rank or highest_rank < 100:
                emit(f"  [{cat_id}] {name}: highestRank yok/cok kucuk, atlaniyor.")
                continue

            widest_total = None
            any_kept = False
            for band_letter, frac_start, frac_end in CATEGORY_BAND_FRACTIONS:
                sales_gte = max(1, int(highest_rank * frac_start) + (1 if frac_start > 0 else 0))
                sales_lte = max(sales_gte, int(highest_rank * frac_end))
                total, _data = _check_band_total_results(api_key, cat_id, sales_gte, sales_lte, band_letter)
                if band_letter == "A":
                    widest_total = total
                if total is None:
                    emit(f"  [{cat_id}] {name} bant {band_letter} ({sales_gte}-{sales_lte}): HATA/yanit yok.")
                    continue
                if total > CATEGORY_DESCEND_IF_ABOVE:
                    emit(f"  [{cat_id}] {name} bant {band_letter} ({sales_gte}-{sales_lte}): {total} -- COK GENIS, child'a inilecek.")
                    continue
                if total < CATEGORY_SKIP_IF_BELOW:
                    emit(f"  [{cat_id}] {name} bant {band_letter} ({sales_gte}-{sales_lte}): {total} -- cok az, atlaniyor.")
                    continue
                any_kept = True
                plan.append({
                    "catId": int(cat_id), "name": name, "path": " > ".join(path),
                    "depth": depth, "band": band_letter,
                    "sales_gte": sales_gte, "sales_lte": sales_lte,
                    "total_results": total,
                })
                emit(f"  [{cat_id}] {name} bant {band_letter} ({sales_gte}-{sales_lte}): {total} -- PLANA EKLENDI.")

            # Descend kosulu: en genis bant (A, tum kategoriyi kapsayan en
            # kucuk rank araligindan farkli olarak burada TUM kategoriyi temsil
            # eden gosterge olarak A bandinin genisligini kullaniyoruz) COK
            # genisse VEYA hicbir bant "harcamaya hazir" cikmadiysa (hepsi ya
            # cok genis ya da child'lar daha isabetli olabilir) children'a in.
            children = info.get("children") or []
            should_descend = (widest_total is not None and widest_total > CATEGORY_DESCEND_IF_ABOVE) or not any_kept
            if should_descend and children and depth < max_depth:
                next_frontier.append((cat_id, info, path, children))

        if not next_frontier:
            break

        child_ids = sorted({cid for _, _, _, children in next_frontier for cid in children})
        emit(f"Derinlik {depth + 1} icin {len(child_ids)} alt kategori cekiliyor...")
        child_data = _fetch_category_batch(api_key, child_ids, stop_event=stop_event)
        path_by_child = {}
        for _parent_id, _info, parent_path, children in next_frontier:
            for cid in children:
                path_by_child[cid] = parent_path

        frontier = []
        for cid, info in child_data.items():
            if _category_name_excluded(info.get("name")):
                continue
            parent_path = path_by_child.get(int(cid)) or path_by_child.get(cid) or []
            frontier.append((cid, info, parent_path + [info.get("name")]))
        depth += 1

        if save_callback:
            # Her derinlik seviyesi bitince ARA KAYIT -- beklenmeyen bir hata
            # (orn. bilinmeyen bir HTTP kodu) TUM calismayi cokertirse, o ana
            # kadarki (token harcanarak elde edilmis) ilerleme KAYBOLMASIN
            # diye (canli yasandi: 405 hatasi -- yanlis batch boyutu -- 3
            # derinlik seviyesi calismasini sildi, 2026-09-14).
            try:
                save_callback(plan)
            except Exception:
                pass

    emit(f">>> Plan tamamlandi: {len(plan)} kategori/bant kombinasyonu.")
    return plan


CATEGORY_PLAN_CACHE_FILE = BASE_DIR / "keepa_kategori_plani.json"


def save_category_plan(plan, path=None):
    path = Path(path) if path else CATEGORY_PLAN_CACHE_FILE
    path.write_text(
        json.dumps({"fetched_at": time.time(), "plan": plan}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_category_plan(path=None, max_age_days=None):
    path = Path(path) if path else CATEGORY_PLAN_CACHE_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if max_age_days is not None:
        age_days = (time.time() - data.get("fetched_at", 0)) / 86400
        if age_days > max_age_days:
            return None
    return data.get("plan")


def fetch_category_plan_asins(plan_entry, output_dir, progress=None, stop_event=None, shared_pool_path=None, api_key=None):
    """build_category_tree_plan'in URETTIGI TEK bir plan girdisini (kategori +
    bant) gercekten tarar -- fetch_strategy_asins ile AYNI kesintiye-dayanikli
    motoru kullanir. shared_pool_path varsayilani icin bkz. fetch_strategy_asins
    icindeki ayni notu (output_dir burada da BASE_DIR'in kardesi degil)."""
    if shared_pool_path is None:
        shared_pool_path = BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt"

    def selection_builder(slice_start, slice_end, page):
        return build_category_band_selection(
            plan_entry["catId"], slice_start, slice_end, plan_entry["band"], page=page
        )

    return fetch_all_asins(
        output_dir,
        sales_rank_start=plan_entry["sales_gte"],
        sales_rank_end=plan_entry["sales_lte"],
        sales_rank_step=(plan_entry["sales_lte"] - plan_entry["sales_gte"] + 1),
        progress=progress,
        stop_event=stop_event,
        shared_pool_path=shared_pool_path,
        selection_builder=selection_builder,
        api_key=api_key,
    )


def query_keepa(api_key, selection):
    params = {
        "key": api_key,
        "domain": DOMAIN,
        "selection": json.dumps(selection, separators=(",", ":")),
    }
    url = "https://api.keepa.com/query?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        # Keepa yaniti gzip ile sikistirilmis donebiliyor; urllib bunu
        # requests'in aksine otomatik acmiyor -- magic byte'a (1f 8b) bakip
        # gerekirse elle aciyoruz.
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def token_wait_seconds(data):
    # NOT: 'refillIn' Keepa'da bir SONRAKI tek-token yenilenmesine kalan
    # sure (genelde <1sn) -- eksiye dusmus/dusuk bir bakiyeyi GUVENLI
    # esige getirmek icin yeterli DEGIL (denendi: 1sn bekleyip 429 aldik).
    # Onun yerine 'refillRate' (token/dakika) ile GERCEK ihtiyaci hesaplayip
    # bekliyoruz.
    tokens_left = data.get("tokensLeft")
    refill_rate = data.get("refillRate") or 1  # token/dakika
    if tokens_left is not None and tokens_left < MIN_TOKENS_BEFORE_PAUSE:
        deficit = MIN_TOKENS_BEFORE_PAUSE - tokens_left
        return max(5, (deficit / refill_rate) * 60)
    return 0


def handle_tokens(data):
    wait_seconds = token_wait_seconds(data)
    if wait_seconds:
        print(
            f"  (token azaldi: {data.get('tokensLeft')}, yenilenme {data.get('refillRate') or 1}/dk "
            f"-- {wait_seconds:.0f} sn bekleniyor...)"
        )
        time.sleep(wait_seconds)


def interruptible_sleep(seconds, stop_event=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def find_completed_slices(output_dir):
    """Bu klasorde daha once tamamlanmis (dosyasi yazilmis) dilimlerin
    (start, end) araliklarini bulur -- kaldigi yerden devam icin."""
    completed = set()
    if not output_dir.exists():
        return completed
    for path in output_dir.glob("sales_*-*.txt"):
        try:
            start_text, end_text = path.stem.replace("sales_", "").split("-")
            completed.add((int(start_text), int(end_text)))
        except ValueError:
            continue
    return completed


def fetch_all_asins(
    output_dir,
    sales_rank_start=None,
    sales_rank_end=None,
    sales_rank_step=None,
    progress=None,
    stop_event=None,
    shared_pool_path=None,
    selection_builder=None,
    api_key=None,
):
    """Sales Rank'i dilimleyerek tarar. Her dilim bitince ANINDA diske yazar
    (saatlerce surebilecek bir tarama arada kesilirse -- daha once 9000
    ASIN'lik bir gece taramasinda oldugu gibi -- o ana kadarki hicbir sey
    kaybolmasin diye). Her ASIN'in HANGI Sales Rank araligindan geldigi hem
    dosya adindan (sales_BBBBB-EEEEE.txt) hem de asin_sales_rank_map.csv
    icinde kalici olarak izlenebilir -- ileride "hangi aralik cok satiyor"
    karsilastirmasi yapabilesin diye.

    Klasor daha once kismen doldurulmussa (yarida kesilmis bir onceki
    calisma), zaten tamamlanmis dilimleri ATLAYIP kaldigi yerden devam eder.

    progress: opsiyonel callback(kind, payload) -- GUI gibi cagiranlarin
        print yerine kendi arayuzunu guncellemesi icin. Verilmezse print().
    stop_event: opsiyonel threading.Event -- set edilirse tarama, o an
        beklenen bir sleep'i kesip bir sonraki kontrol noktasinda guvenli
        sekilde durur (yarim kalan sayfa/dilim veri kaybina yol acmaz,
        cunku her dilim SADECE tam bitince diske yazilir).
    selection_builder: opsiyonel callable(slice_start, slice_end, page) ->
        selection sozlugu. Verilmezse modulun kendi build_selection'i
        kullanilir (manuel filtreler/kisisel JSON). fetch_strategy_asins
        bunu, hazir stratejilerden birinin selection'ini kurmak icin verir.
    api_key: opsiyonel -- verilmezse bu modulun kendi load_api_key()'i
        (ic/kisisel kullanim icin ortam degiskeni/yerel dosya) kullanilir.
        AsinSeeker GUI'si MUSTERININ kendi (keyring'deki) anahtarini
        BURADAN vermek zorunda -- vermezse dahili anahtar aranir ve
        musteri makinesinde bulunamaz (canli dogrulandi, musteriden geldi)."""
    selection_builder = selection_builder or build_selection
    api_key = api_key or load_api_key()
    sales_rank_start = SALES_RANK_START if sales_rank_start is None else sales_rank_start
    sales_rank_end = SALES_RANK_END if sales_rank_end is None else sales_rank_end
    sales_rank_step = SALES_RANK_STEP if sales_rank_step is None else sales_rank_step
    # Ortak havuz varsayilan olarak output_dir'in KARDESI olarak yazilir
    # (BASE_DIR -- exe/script'in oldugu yer -- DEGIL). Boylece output_dir'i
    # git ile senkronlanan paylasilan bir klasorun (orn. "keepa_sync\...")
    # icine koydugunda, ortak havuz da OTOMATIK olarak ayni git klasorune
    # duser -- CLI, GUI, hangi makine/BASE_DIR fark etmeksizin. Sadece acikca
    # farkli bir yol verilirse (shared_pool_path parametresi) bu ezilir.
    output_dir = Path(output_dir)
    if shared_pool_path is None:
        shared_pool_path = output_dir.resolve().parent / "ortak_asin_havuzu.txt"
    else:
        shared_pool_path = Path(shared_pool_path)

    def emit(kind, message="", **extra):
        if progress:
            progress(kind, {"message": message, **extra})
        elif message:
            print(message)

    def stopped():
        return stop_event is not None and stop_event.is_set()

    all_asins = set()
    total_tokens_consumed = 0
    slice_start = sales_rank_start

    output_dir.mkdir(parents=True, exist_ok=True)
    completed_slices = find_completed_slices(output_dir)
    combined_path = output_dir / "tum_asinler.txt"
    map_path = output_dir / "asin_sales_rank_map.csv"

    if completed_slices:
        emit("log", f"Devam ediliyor: {len(completed_slices)} dilim daha once tamamlanmis, atlanacak.")
        if combined_path.exists():
            all_asins.update(combined_path.read_text(encoding="utf-8").split())

    shared_pool_path.parent.mkdir(parents=True, exist_ok=True)
    shared_pool_asins = set()
    if shared_pool_path.exists():
        shared_pool_asins.update(shared_pool_path.read_text(encoding="utf-8").split())

    resuming = combined_path.exists() and map_path.exists()
    with combined_path.open("a" if resuming else "w", encoding="utf-8") as combined_file, \
         map_path.open("a" if resuming else "w", newline="", encoding="utf-8") as map_file, \
         shared_pool_path.open("a", encoding="utf-8") as shared_pool_file:
        map_writer = csv.writer(map_file)
        if not resuming:
            map_writer.writerow(["asin", "sales_rank_min", "sales_rank_max"])

        while slice_start < sales_rank_end and not stopped():
            slice_end = min(slice_start + sales_rank_step - 1, sales_rank_end)
            if (slice_start, slice_end) in completed_slices:
                slice_start = slice_end + 1
                continue
            emit("slice_start", f"Sales Rank {slice_start}-{slice_end} taraniyor...",
                 slice_start=slice_start, slice_end=slice_end)
            page = 0
            slice_asins = set()
            rate_limit_retries = 0

            while not stopped():
                selection = selection_builder(slice_start, slice_end, page)
                try:
                    data = query_keepa(api_key, selection)
                except urllib.error.HTTPError as error:
                    if error.code == 429 and rate_limit_retries < 10:
                        # Kota asimi -- dilimi TERK ETMEK yerine (veri
                        # kaybina yol acar) bekleyip AYNI sayfayi tekrar
                        # deniyoruz. Bekleme suresi her denemede artar.
                        rate_limit_retries += 1
                        wait_seconds = min(30 * rate_limit_retries, 300)
                        emit("log", f"  Kota asimi (429) -- {wait_seconds} sn sonra ayni sayfa tekrar "
                                    f"denenecek (deneme {rate_limit_retries}/10)...")
                        interruptible_sleep(wait_seconds, stop_event)
                        continue
                    raw_body = error.read()
                    if raw_body[:2] == b"\x1f\x8b":
                        raw_body = gzip.decompress(raw_body)
                    body = raw_body.decode(errors="replace")
                    if error.code == 400 and rate_limit_retries < 10:
                        # Canli gozlemlendi: token bakiyesi derin eksideyken
                        # (baska bir surecle paylasilan hesapta) Keepa 429
                        # yerine 400 dondurebiliyor -- ayni "bekleyip tekrar
                        # dene" mantigini burada da uyguluyoruz.
                        rate_limit_retries += 1
                        wait_seconds = min(30 * rate_limit_retries, 300)
                        emit("log", f"  HATA (HTTP 400, muhtemelen token acigi): {body[:200]} -- "
                                    f"{wait_seconds} sn sonra tekrar denenecek (deneme {rate_limit_retries}/10)...")
                        interruptible_sleep(wait_seconds, stop_event)
                        continue
                    emit("log", f"  HATA (HTTP {error.code}): {body[:300]}")
                    break
                except Exception as error:
                    emit("log", f"  HATA: {type(error).__name__}: {error}")
                    break

                asin_list = data.get("asinList")
                if asin_list is None:
                    # Beklenmeyen yanit sekli -- alan adi degismis olabilir,
                    # teshis icin ham anahtarlari goster.
                    emit("log", f"  UYARI: yanitta 'asinList' yok. Gelen alanlar: {list(data.keys())}")
                    break

                new_count = len({a for a in asin_list if a not in all_asins})
                slice_asins.update(asin_list)
                all_asins.update(asin_list)
                total_tokens_consumed += data.get("tokensConsumed") or 0
                tokens_per_asin = (total_tokens_consumed / len(all_asins)) if all_asins else 0
                emit(
                    "page",
                    f"  sayfa {page}: {len(asin_list)} ASIN ({new_count} yeni, toplam {len(all_asins)}, "
                    f"toplam {total_tokens_consumed} token, ASIN basina {tokens_per_asin:.2f} token)",
                    total=len(all_asins),
                    tokens_left=data.get("tokensLeft"),
                    refill_rate=data.get("refillRate"),
                    position=slice_start,
                    tokens_consumed_total=total_tokens_consumed,
                    tokens_per_asin=tokens_per_asin,
                )

                wait_seconds = token_wait_seconds(data)
                if wait_seconds:
                    emit("log", f"  (token azaldi: {data.get('tokensLeft')}, yenilenme "
                                f"{data.get('refillRate') or 1}/dk -- {wait_seconds:.0f} sn bekleniyor...)")
                    interruptible_sleep(wait_seconds, stop_event)

                if len(asin_list) < PER_PAGE:
                    break  # bu dilimde baska sayfa yok
                page += 1
                if page >= MAX_PAGES_PER_SLICE:
                    emit(
                        "log",
                        f"  UYARI: {MAX_PAGES_PER_SLICE} sayfa sinirina ulasildi -- bu dilim "
                        f"({slice_start}-{slice_end}) daha genis olabilir, SALES_RANK_STEP'i "
                        f"kucult ve tekrar dene.",
                    )
                    break

            if stopped():
                emit("log", "Durduruldu -- bu dilim yarim kaldiysa bir sonraki calistirmada bastan denenecek.")
                break

            # Bu dilim bitti -- ANINDA diske yaz (kendi dosyasi + toplu dosya + harita + ortak havuz).
            slice_path = output_dir / f"sales_{slice_start:05d}-{slice_end:05d}.txt"
            slice_path.write_text(
                "\n".join(sorted(slice_asins)) + ("\n" if slice_asins else ""), encoding="utf-8"
            )
            new_for_pool = sorted(slice_asins - shared_pool_asins)
            for asin in sorted(slice_asins):
                combined_file.write(asin + "\n")
                map_writer.writerow([asin, slice_start, slice_end])
            for asin in new_for_pool:
                shared_pool_file.write(asin + "\n")
                shared_pool_asins.add(asin)
            combined_file.flush()
            map_file.flush()
            shared_pool_file.flush()

            slice_start = slice_end + 1

        emit(
            "complete" if not stopped() else "stopped",
            f"Bitis noktasina ({sales_rank_end}) ulasildi." if not stopped() else "Kullanici tarafindan durduruldu.",
            total=len(all_asins),
        )

    return all_asins


def main():
    if len(sys.argv) > 1:
        # Yarida kesilmis onceki bir klasorden devam et.
        output_dir = BASE_DIR / sys.argv[1] if not Path(sys.argv[1]).is_absolute() else Path(sys.argv[1])
    else:
        run_id = time.strftime("%Y%m%d_%H%M%S")
        output_dir = BASE_DIR / f"keepa_arama_{run_id}"

    last_stats = {}

    def progress(kind, payload):
        message = payload.get("message", "")
        if message:
            print(message)
        if kind == "page":
            last_stats.update(payload)

    asins = fetch_all_asins(output_dir, progress=progress)
    print(f"\n=== TOPLAM: {len(asins)} benzersiz ASIN ===")
    if last_stats.get("tokens_consumed_total") is not None:
        print(
            f"Toplam kullanilan token: {last_stats['tokens_consumed_total']} "
            f"(ASIN basina ortalama {last_stats.get('tokens_per_asin', 0):.2f} token)"
        )
    print(f"Klasor: {output_dir}")
    print(f"  - tum_asinler.txt          (hepsi bir arada, AsinSeeker'a yuklemeye hazir)")
    print(f"  - asin_sales_rank_map.csv  (her ASIN hangi Sales Rank araligindan geldi)")
    print(f"  - sales_BBBBB-EEEEE.txt    (her dilim kendi dosyasinda)")


if __name__ == "__main__":
    main()
