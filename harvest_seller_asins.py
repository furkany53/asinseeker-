"""Rakip (Turk) satici kesfi + storefront hasadi.

2026-09-19 TAM YENIDEN TASARIM (musteriden geldi -- IKI ayri sorun icin):

1) GIZLILIK: Eskiden bu dosyada gelistiricinin KENDI bulup dogruladigi 29
   rakip saticinin ismi/seller_id'si koda GOMULUYDU (DEFAULT_SELLERS) ve
   saticilar.json yoksa OTOMATIK bu listeyle olusturuluyordu. Bu kod
   musteriye giden exe'ye de derleniyor -- yani musterinin makinesinde ilk
   calistirmada bu KOD (dosyadan degil, .py'nin kendisinden) gelistiricinin
   kendi rakip istihbaratini saticilar.json'a YAZARDI (musteri bunu gorebilir/
   kullanabilirdi). Bu artik YAPILMIYOR -- load_sellers() dosya yoksa BOS
   liste dondurur, hicbir isim/ID koda gomulu degil.

2) YENI KESIF MANTIGI: Sabit/bilinen bir satici listesine guvenmek yerine
   ("once ben kimin rakip oldugunu biliyorum, storefront'unu cek") artik
   OTOMATIK kesif var: once belli bir SATICI SAYISI araligindaki (orn. 15-25
   -- ne cok rekabetsiz/talepsiz ne de doymus) ASIN'leri Keepa Finder'dan
   bul, sonra bu ASIN'lerin GERCEK canli tekliflerindeki (offers) sellerId'leri
   topla, toplu /seller sorgusuyla adreslerini cek, ulke kodu hedef ulkeyle
   (varsayilan TR) eslesenleri YENI rakip olarak saticilar.json'a ekle.
   Boylece musterinin KENDI kopyasi, KENDI Keepa anahtariyla, KENDI rakiplerini
   sifirdan bulabilir -- gelistiricinin listesine bagimli degil.

CANLI DOGRULANDI (2026-09-19, proje kurali geregi -- "yeni Keepa alani
kullanmadan once totalResults/gercek yanitla dogrula"):
  - /product?asin=...&offers=20 -> products[0]['offers'] bir liste, her
    eleman 'sellerId' alani tasiyor.
  - /seller?seller=ID1,ID2,... (virgullu, toplu) -> sellers[ID]['address']
    bir liste, SON eleman ISO ulke kodu (orn. 'CN', 'TR').

KULLANIM (CLI): python harvest_seller_asins.py  -- once kesif, sonra hasat.
GUI: keepa_gui.py'nin "Rakip Kopyala" sekmesi run_discovery_and_harvest()'i
kullanir (satici sayisi araligi/hedef ulke sekmeden ayarlanabilir).
"""
import csv
import json
import gzip
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import keepa_finder as kf

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "keepa_sync" / "turk_saticilar"
SHARED_POOL = BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt"
LOG_CSV = OUTPUT_DIR / "islenen_saticilar.csv"
SELLERS_FILE = OUTPUT_DIR / "saticilar.json"

# Kesif icin varsayilanlar -- GUI'den degistirilebilir.
DISCOVERY_OFFER_COUNT_MIN = 15
DISCOVERY_OFFER_COUNT_MAX = 25
DISCOVERY_SEED_BATCH_SIZE = 50  # tek kesif turunde kac "tohum" ASIN taranacak
DISCOVERY_TARGET_COUNTRY = "TR"


def load_sellers():
    """SELLERS_FILE'dan [{"name", "seller_id", "domain"}, ...] okur. Dosya
    yoksa BOS bir liste ile olusturur -- musteri surumunde hicbir isim/ID
    koda gomulu DEGIL, liste tamamen kesif/elle-ekleme ile doldurulur."""
    if not SELLERS_FILE.exists():
        save_sellers([])
        return []
    return json.loads(SELLERS_FILE.read_text(encoding="utf-8"))


def save_sellers(sellers):
    SELLERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SELLERS_FILE.write_text(json.dumps(sellers, ensure_ascii=False, indent=2), encoding="utf-8")


def add_seller(name, seller_id, domain):
    """Yeni bir satici ekler (ayni seller_id zaten varsa yok sayar). GUI'nin
    'Yeni Satici Ekle' formu VE otomatik kesif bunu cagirir."""
    sellers = load_sellers()
    seller_id = seller_id.strip()
    for s in sellers:
        if s["seller_id"] == seller_id:
            return sellers, False  # zaten var
    sellers.append({"name": name.strip() or seller_id, "seller_id": seller_id, "domain": int(domain)})
    save_sellers(sellers)
    return sellers, True


def remove_seller(seller_id):
    sellers = load_sellers()
    remaining = [s for s in sellers if s["seller_id"] != seller_id]
    save_sellers(remaining)
    return remaining


def _api_call(url, tries=6):
    """429 (kota asimi) icin artan bekleme ile tekrar dener -- keepa_finder.py
    ile AYNI mantik (birden fazla surec/oturum ayni anahtari paylasabiliyor)."""
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url)
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < tries - 1:
                time.sleep(min(30 * (attempt + 1), 180))
                continue
            raise


def fetch_seller_asins(api_key, seller_id, domain):
    params = {"key": api_key, "domain": domain, "seller": seller_id, "storefront": 1}
    data = _api_call("https://api.keepa.com/seller?" + urllib.parse.urlencode(params))
    sellers = data.get("sellers") or {}
    info = sellers.get(seller_id)
    if not info:
        return None, data.get("tokensConsumed")
    return info, data.get("tokensConsumed")


def fetch_seller_name(api_key, seller_id, domain):
    """Tek bir seller_id icin Keepa'dan isim ceker (businessName oncelikli,
    yoksa sellerName) -- 'Yeni Rakip Satici Ekle' formunda isim ARTIK ELLE
    girilmiyor, Ekle'ye basinca buradan otomatik cekiliyor (musteriden
    geldi, 2026-09-20). Bulunamazsa None doner (cagiran taraf seller_id'yi
    isim olarak kullanir)."""
    params = {"key": api_key, "domain": domain, "seller": seller_id}
    data = _api_call("https://api.keepa.com/seller?" + urllib.parse.urlencode(params))
    sellers = data.get("sellers") or {}
    info = sellers.get(seller_id)
    if not info:
        return None
    return info.get("businessName") or info.get("sellerName") or None


def load_already_processed():
    if not LOG_CSV.exists():
        return set()
    with LOG_CSV.open(encoding="utf-8") as f:
        return {row["seller_id"] for row in csv.DictReader(f)}


# ------------------------------------------------------- 1) tohum ASIN kesfi
def find_seed_asins(api_key, offer_count_min, offer_count_max, batch_size, domain=5):
    """Belirtilen satici-sayisi araligindaki ASIN'leri Keepa Finder'dan
    ceker -- ne cok rekabetsiz (talep suphesi) ne de asiri doymus urunler,
    Turk dropshipper'larin da tercih ettigi 'orta yogunluk' bandi.

    CANLI DOGRULANDI (2026-09-19): page=0 icin perPage'in bir ALT SINIRI var
    -- 10 ve 20 ile "combination of perPage and page exeeds limit or is too
    small" (HTTP 400) hatasi alindi, 50 ile basarili oldu (2.244.396
    totalResults, 11 token). Bu yuzden batch_size hicbir zaman 50'nin altina
    dusurulmuyor."""
    batch_size = max(50, batch_size)
    selection = {
        "productType": ["0"],
        "singleVariation": True,
        "totalOfferCount_gte": offer_count_min,
        "totalOfferCount_lte": offer_count_max,
        "avg365_COUNT_NEW_gte": 1,
        "perPage": batch_size,
        "page": 0,
    }
    # kf.query_keepa'nin kendi 429 tekrar mantigi yok (o, fetch_all_asins'in
    # cagiran dongusunde) -- burada da AYNI backoff'u uyguluyoruz, cunku bu
    # anahtar full_pool_check.py gibi baska ic surecerle paylasilabiliyor.
    for attempt in range(6):
        try:
            data = kf.query_keepa(api_key, selection)
            break
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 5:
                time.sleep(min(30 * (attempt + 1), 180))
                continue
            raise
    return data.get("asinList") or [], data.get("tokensConsumed") or 0


# ------------------------------------------------------- 2) satici toplama
def collect_sellers_from_asins(api_key, asins, domain=5, chunk_size=20):
    """ASIN'leri (offers=20 ile) parca parca /product'a gonderip canli
    tekliflerdeki TUM sellerId'leri toplar (tekillestirilmis)."""
    seller_ids = set()
    tokens_total = 0
    for i in range(0, len(asins), chunk_size):
        chunk = asins[i:i + chunk_size]
        params = {"key": api_key, "domain": domain, "asin": ",".join(chunk), "offers": 20}
        data = _api_call("https://api.keepa.com/product?" + urllib.parse.urlencode(params))
        tokens_total += data.get("tokensConsumed") or 0
        for product in data.get("products") or []:
            for offer in product.get("offers") or []:
                sid = offer.get("sellerId")
                if sid:
                    seller_ids.add(sid)
    return seller_ids, tokens_total


# ------------------------------------------------------- 3) ulke filtresi
def filter_sellers_by_country(api_key, seller_ids, target_country, domain=5, chunk_size=100):
    """Toplu /seller sorgusuyla adres bilgisini ceker, address[-1] (ISO ulke
    kodu) target_country'e esit olanlari dondurur: [(seller_id, isim), ...]."""
    seller_ids = list(seller_ids)
    matches = []
    tokens_total = 0
    for i in range(0, len(seller_ids), chunk_size):
        chunk = seller_ids[i:i + chunk_size]
        params = {"key": api_key, "domain": domain, "seller": ",".join(chunk)}
        data = _api_call("https://api.keepa.com/seller?" + urllib.parse.urlencode(params))
        tokens_total += data.get("tokensConsumed") or 0
        sellers = data.get("sellers") or {}
        for sid, info in sellers.items():
            address = info.get("address") or []
            country = address[-1] if address else None
            if country == target_country:
                name = info.get("businessName") or info.get("sellerName") or sid
                matches.append((sid, name))
    return matches, tokens_total


def discover_new_sellers(
    api_key,
    offer_count_min=DISCOVERY_OFFER_COUNT_MIN,
    offer_count_max=DISCOVERY_OFFER_COUNT_MAX,
    seed_batch_size=DISCOVERY_SEED_BATCH_SIZE,
    target_country=DISCOVERY_TARGET_COUNTRY,
    domain=5,
    max_new_sellers=None,
    progress=None,
):
    """Tam kesif zinciri: tohum ASIN bul -> satici topla -> ulkeye gore
    filtrele -> YENI olanlari saticilar.json'a ekle. Donen: yeni eklenen
    [(seller_id, isim), ...] listesi.

    max_new_sellers: verilirse, bir kesif turunde EN FAZLA bu kadar yeni
    satici eklenir (2026-09-20, musteriden geldi -- 'max 5 satici bul').
    Ulke kontrolu YINE TUM adaylar icin yapilir (ucuz, ~1 token/100 satici),
    sadece EKLEME asamasi bu sayida kesilir."""
    def emit(kind, message="", **extra):
        if progress:
            progress(kind, {"message": message, **extra})
        elif message:
            print(message)

    emit("log", f">>> Kesif: satıcı sayısı {offer_count_min}-{offer_count_max} olan {seed_batch_size} tohum ASIN aranıyor...")
    seed_asins, tokens1 = find_seed_asins(api_key, offer_count_min, offer_count_max, seed_batch_size, domain)
    emit("log", f"  {len(seed_asins)} tohum ASIN bulundu ({tokens1} token).")
    if not seed_asins:
        emit("log", "  Tohum ASIN bulunamadı, kesif durduruldu.")
        return []

    emit("log", f">>> Bu ASIN'lerin canlı satıcıları toplanıyor...")
    seller_ids, tokens2 = collect_sellers_from_asins(api_key, seed_asins, domain)
    emit("log", f"  {len(seller_ids)} benzersiz satıcı bulundu ({tokens2} token).")

    known = {s["seller_id"] for s in load_sellers()}
    new_candidates = seller_ids - known
    emit("log", f"  Bunlardan {len(new_candidates)} tanesi zaten bilinen listede değil, ülke kontrolü yapılıyor...")

    matches, tokens3 = filter_sellers_by_country(api_key, new_candidates, target_country, domain)
    emit("log", f"  {len(matches)} yeni '{target_country}' menşeli satıcı bulundu ({tokens3} token).")
    if max_new_sellers is not None and len(matches) > max_new_sellers:
        emit("log", f"  Limit uygulanıyor: sadece ilk {max_new_sellers} tanesi eklenecek.")
        matches = matches[:max_new_sellers]

    added = []
    for seller_id, name in matches:
        _sellers, was_added = add_seller(name, seller_id, domain)
        if was_added:
            added.append((seller_id, name))
            emit("log", f"  + YENİ rakip eklendi: {name} ({seller_id})")

    emit(
        "discover_complete",
        f">>> Keşif bitti: {len(added)} yeni rakip satıcı eklendi (toplam token: {tokens1 + tokens2 + tokens3}).",
        added=len(added),
    )
    return added


# ------------------------------------------------- 5) en cok satan ASIN filtresi
def fetch_sales_ranks(api_key, asins, domain=5, chunk_size=100):
    """Verilen ASIN'lerin GUNCEL Sales Rank'ini EN UCUZ sekilde ceker --
    stats/offers gibi ek parametre YOK, sadece 'csv' dizisi geliyor
    (CANLI DOGRULANDI 2026-09-20: 2 ASIN icin tokensConsumed=2, yani 1
    token/ASIN -- storefront'un TAMAMINI cekmekten (10 token/satici, ASIN
    sayisindan BAGIMSIZ) cok daha pahali olabilir, sadece filtreleme
    ISTENDIGINDE kullanilmali).

    csv[3] = Sales Rank zaman serisi, [zaman,deger,zaman,deger,...] seklinde
    ikili -- son deger (indeks -1) GUNCEL Sales Rank'tir (dizi her zaman
    cift uzunlukta oldugu icin son indeks bir 'deger' pozisyonuna denk
    gelir). Donen: {asin: sales_rank_or_None}."""
    ranks = {}
    tokens_total = 0
    for i in range(0, len(asins), chunk_size):
        chunk = asins[i:i + chunk_size]
        params = {"key": api_key, "domain": domain, "asin": ",".join(chunk)}
        data = _api_call("https://api.keepa.com/product?" + urllib.parse.urlencode(params))
        tokens_total += data.get("tokensConsumed") or 0
        for product in data.get("products") or []:
            csv_data = product.get("csv") or []
            sr_series = csv_data[3] if len(csv_data) > 3 else None
            ranks[product.get("asin")] = sr_series[-1] if sr_series else None
    return ranks, tokens_total


def keep_best_selling(api_key, asin_list, limit, domain=5, progress=None):
    """asin_list'i (bir saticinin TAM storefront'u) Sales Rank'e gore
    (dusuk=daha cok satiyor) siralayip EN IYI 'limit' tanesini dondurur.
    Rank bilinmeyenler (None) en sona atilir. limit None ya da liste zaten
    kisaysa, hicbir ek API cagrisi yapmadan oldugu gibi dondurulur."""
    def emit(kind, message="", **extra):
        if progress:
            progress(kind, {"message": message, **extra})
        elif message:
            print(message)

    if limit is None or len(asin_list) <= limit:
        return asin_list, 0
    ranks, tokens = fetch_sales_ranks(api_key, asin_list, domain)
    emit("log", f"    (en çok satanı bulmak için {len(asin_list)} ASIN'in Sales Rank'i çekildi, {tokens} token)")
    ranked = sorted(asin_list, key=lambda a: (ranks.get(a) is None, ranks.get(a)))
    return ranked[:limit], tokens


# ------------------------------------------------------- 4) storefront hasadi
def harvest_all(api_key, progress=None, stop_event=None, shared_pool_path=None, per_seller_asin_limit=None):
    """TUM (henuz islenmemis) saticilarin storefront'unu cekip ortak havuza
    ekler. Donen deger: {"islenen": N, "yeni_asin": N, "toplam_havuz": N}

    per_seller_asin_limit: verilirse, bir saticinin TAM listesi bundan
    uzunsa Sales Rank'e gore (keep_best_selling) SADECE en iyi bu kadar
    ASIN'i havuza eklenir -- geri kalani hic alinmaz (2026-09-20,
    musteriden geldi -- 'her saticinin en cok satan ASIN'lerini alalim')."""
    def emit(kind, message="", **extra):
        if progress:
            progress(kind, {"message": message, **extra})
        elif message:
            print(message)

    def stopped():
        return stop_event is not None and stop_event.is_set()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sellers = load_sellers()
    already = load_already_processed()
    todo = [s for s in sellers if s["seller_id"] not in already]

    pool_path = Path(shared_pool_path) if shared_pool_path else SHARED_POOL
    pool = set()
    if pool_path.exists():
        pool = {line.strip().upper() for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    pool_before = len(pool)

    is_new_log = not LOG_CSV.exists()
    islenen = 0
    yeni_asin_toplam = 0
    with LOG_CSV.open("a", newline="", encoding="utf-8") as logf:
        writer = csv.DictWriter(logf, fieldnames=["seller_id", "seller_name", "domain", "asin_count", "tokens"])
        if is_new_log:
            writer.writeheader()

        emit("start", f">>> {len(todo)} satıcı işlenecek ({len(sellers) - len(todo)} zaten işlenmiş, atlanacak).",
             total=len(todo))

        for seller in todo:
            if stopped():
                emit("log", "Durduruldu.")
                break
            name, seller_id, domain = seller["name"], seller["seller_id"], seller["domain"]
            emit("log", f"{name} ({seller_id}) çekiliyor...")
            try:
                info, tokens = fetch_seller_asins(api_key, seller_id, domain)
            except Exception as error:
                emit("log", f"  HATA: {type(error).__name__}: {error}")
                continue
            if info is None:
                emit("log", f"  BULUNAMADI (tokens={tokens})")
                continue
            asin_list = info.get("asinList") or []
            emit("log", f"  {len(asin_list)} ASIN bulundu, {tokens} token harcandı.")

            if per_seller_asin_limit is not None and len(asin_list) > per_seller_asin_limit:
                asin_list, rank_tokens = keep_best_selling(
                    api_key, asin_list, per_seller_asin_limit, domain, progress=progress
                )
                emit("log", f"  Limit uygulandı: en çok satan {len(asin_list)} ASIN alınıyor.")

            seller_file = OUTPUT_DIR / f"{name}_{seller_id}.txt"
            seller_file.write_text("\n".join(asin_list) + "\n", encoding="utf-8")

            new_count = 0
            for asin in asin_list:
                a = asin.strip().upper()
                if a and a not in pool:
                    pool.add(a)
                    new_count += 1
            emit("log", f"  Ortak havuza {new_count} YENİ ASIN eklendi.")
            yeni_asin_toplam += new_count

            writer.writerow({
                "seller_id": seller_id, "seller_name": name, "domain": domain,
                "asin_count": len(asin_list), "tokens": tokens,
            })
            logf.flush()
            islenen += 1
            emit("seller_done", islenen=islenen, total=len(todo), toplam_havuz=len(pool), yeni_asin_toplam=yeni_asin_toplam)

    if len(pool) != pool_before:
        pool_path.write_text("\n".join(sorted(pool)) + "\n", encoding="utf-8")

    emit(
        "complete" if not stopped() else "stopped",
        f"Bitti: {islenen} satıcı işlendi, ortak havuz {pool_before} -> {len(pool)} (+{len(pool) - pool_before}).",
        islenen=islenen, toplam_havuz=len(pool), yeni_asin_toplam=yeni_asin_toplam,
    )
    return {"islenen": islenen, "yeni_asin": yeni_asin_toplam, "toplam_havuz": len(pool)}


def run_discovery_and_harvest(
    api_key,
    offer_count_min=DISCOVERY_OFFER_COUNT_MIN,
    offer_count_max=DISCOVERY_OFFER_COUNT_MAX,
    seed_batch_size=DISCOVERY_SEED_BATCH_SIZE,
    target_country=DISCOVERY_TARGET_COUNTRY,
    domain=5,
    max_new_sellers=None,
    per_seller_asin_limit=None,
    progress=None,
    stop_event=None,
    shared_pool_path=None,
):
    """'İşlenmemiş Satıcıları Çek' butonunun TAM akışı: önce otomatik kesif
    (yeni rakip satıcı bul, en fazla max_new_sellers tanesi), sonra TUM
    (yeni + eskiden bilinen) islenmemis saticilarin storefront'unu hasat
    et (her saticiden en fazla per_seller_asin_limit -- en cok satan --
    ASIN alinir)."""
    discover_new_sellers(
        api_key, offer_count_min, offer_count_max, seed_batch_size, target_country,
        domain, max_new_sellers=max_new_sellers, progress=progress,
    )
    if stop_event is not None and stop_event.is_set():
        return {"islenen": 0, "yeni_asin": 0, "toplam_havuz": 0}
    return harvest_all(
        api_key, progress=progress, stop_event=stop_event, shared_pool_path=shared_pool_path,
        per_seller_asin_limit=per_seller_asin_limit,
    )


def main():
    api_key = kf.load_api_key()
    run_discovery_and_harvest(api_key)


if __name__ == "__main__":
    main()
