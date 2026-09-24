"""Amazon SP-API (Selling Partner API) uzerinden JP magazasinin Business
Report'unu (Sales and Traffic by ASIN) otomatik cekmek icin.

Bu, keepa_finder.py'nin load_api_key() ile ayni desen: KISISEL/IC kullanim
icin (onury'nin kendi Amazon magazasinin SP-API kimlik bilgileri), keyring
tabanli MUSTERI deposu (keepa_api_settings.py) DEGIL. Musteriye giden .exe'de
bu kimlik bilgisi dosyasi hic bulunmayacagi icin (build_tmp/AsinSeeker.spec
bunu bundle etmiyor), musteri makinesinde ilgili GUI sekmesi zaten
gorunmuyor (bkz. keepa_gui.py -- sadece frozen olmayan/gelistirme modunda
eklenir).

Kimlik bilgileri artik DUZ METIN DOSYASINDA DEGIL -- Windows Credential
Manager'da (keyring) SIFRELI saklanir (2026-09-24, musteriden geldi: proje
klasoru OneDrive'a senkronize oldugu icin duz JSON dosyasi gercek bir risk.
keepa_api_settings.py'nin musteri anahtarlari icin kullandigi AYNI mekanizma).
Ilk calistirmada eski amazon_sp_api_credentials.json bulunursa OTOMATIK
keyring'e tasinip dosya SILINIR (bkz. _migrate_legacy_file_if_needed).
Gelistirme kolayligi icin ortam degiskenleri (AMAZON_SP_API_CLIENT_ID,
AMAZON_SP_API_CLIENT_SECRET, AMAZON_SP_API_REFRESH_TOKEN) hala ONCELIKLI
override olarak calisir.

CANLI DOGRULANDI (2026-09-22): LWA token degisimi + Reports API akisi
(create -> poll -> document -> download, GZIP'li JSON) gercek JP hesabiyla
uctan uca test edildi. Uygulamaya "Product Listing" + "Inventory and Order
Tracking" + "Brand Analytics" rolleri atanmadan Reports API 403 Unauthorized
donuyordu (sadece "Selling Partner Insights" yetmiyor) -- bu proje kurali
geregi (CLAUDE.md: "verify before trusting") yeni bir Amazon rolu/parametresi
varsayimla koda gomulmedi, canli denenip dogrulandi.
"""

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import keyring
except ImportError:
    keyring = None

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "amazon_sp_api_credentials.json"  # ESKI duz-metin yol -- sadece tek seferlik gocu icin okunur
KEYRING_SERVICE = "AsinSeekerAmazonSPAPI_Internal"
KEYRING_ACCOUNT = "amazon_sp_api_credentials"

MARKETPLACE_ID_JP = "A1VC38T7YXB528"  # Amazon.co.jp -- canli dogrulandi (marketplaceParticipations)
SP_API_BASE = "https://sellingpartnerapi-fe.amazon.com"  # Far East bolgesi (JP burada)
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

REPORT_TYPE_BUSINESS = "GET_SALES_AND_TRAFFIC_REPORT"
REPORT_TYPE_INVENTORY = "GET_MERCHANT_LISTINGS_ALL_DATA"

# GET_MERCHANT_LISTINGS_ALL_DATA canli dogrulandi (2026-09-22): duz sekmeyle
# ayrilmis (TSV) metin dosyasi, GET_SALES_AND_TRAFFIC_REPORT gibi JSON DEGIL.
# Kolonlar: seller-sku, asin1, price, quantity, open-date, minimum/maximum-
# seller-allowed-price. dataStartTime/dataEndTime bu rapor turu icin anlamsiz
# (anlik envanter fotografi) -- Amazon ikisini de cagri anina esitleyip yok
# sayiyor, bos {} reportOptions ile calisiyor.
_INVENTORY_TSV_FIELDS = [
    "seller-sku", "asin1", "price", "quantity", "open-date",
    "minimum-seller-allowed-price", "maximum-seller-allowed-price",
]


def save_credentials(client_id, client_secret, refresh_token):
    """Windows Credential Manager'a (keyring) SIFRELI yazar -- duz metin
    dosyaya BIR DAHA yazilmiyor."""
    if keyring is None:
        raise RuntimeError("keyring kutuphanesi yok -- kimlik bilgisi guvenli sekilde kaydedilemedi.")
    payload = json.dumps({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token})
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, payload)


def _load_from_keyring():
    if keyring is None:
        return None
    try:
        raw = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _migrate_legacy_file_if_needed():
    """ESKI amazon_sp_api_credentials.json (duz metin) bulunursa keyring'e
    tasir ve dosyayi SILER -- proje klasoru OneDrive'a senkronize oldugu
    icin duz metin sirlarin diskte kalici durmasi gercek bir risk (2026-09-24,
    musteriden geldi). Tek seferlik, sessiz gecis."""
    if not CREDENTIALS_FILE.exists():
        return
    try:
        with CREDENTIALS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("client_id") and data.get("client_secret") and data.get("refresh_token"):
            save_credentials(data["client_id"], data["client_secret"], data["refresh_token"])
        CREDENTIALS_FILE.unlink()
    except Exception:
        pass  # goc basarisiz olursa sessizce eski dosyaya birakiyoruz, load_credentials yine de okuyabilir


def load_credentials():
    """Oncelik sirasi: 1) ortam degiskenleri (gelistirme override'i),
    2) keyring (SIFRELI, kalici deposu), 3) eski duz-metin dosya (SADECE
    hala varsa -- once otomatik keyring'e goc ettirilir). Musteriye giden
    exe'de bunlarin hicbiri olmayacagi icin RuntimeError firlatir (cagiran
    taraf -- keepa_gui.py'deki sekme -- bunu yakalayip kullaniciya
    'yapilandirilmamis' mesaji gosterir)."""
    client_id = os.getenv("AMAZON_SP_API_CLIENT_ID")
    client_secret = os.getenv("AMAZON_SP_API_CLIENT_SECRET")
    refresh_token = os.getenv("AMAZON_SP_API_REFRESH_TOKEN")
    if client_id and client_secret and refresh_token:
        return {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}

    _migrate_legacy_file_if_needed()

    data = _load_from_keyring()
    if data and data.get("client_id") and data.get("client_secret") and data.get("refresh_token"):
        return data

    if CREDENTIALS_FILE.exists():
        with CREDENTIALS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("client_id") and data.get("client_secret") and data.get("refresh_token"):
            return data

    raise RuntimeError(
        "Amazon SP-API kimlik bilgileri bulunamadi. Ya AMAZON_SP_API_CLIENT_ID / "
        "AMAZON_SP_API_CLIENT_SECRET / AMAZON_SP_API_REFRESH_TOKEN ortam degiskenlerini "
        "ayarla, ya da save_credentials() ile keyring'e yaz."
    )


def get_access_token(credentials=None):
    credentials = credentials or load_credentials()
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": credentials["refresh_token"],
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
    }).encode()
    request = urllib.request.Request(LWA_TOKEN_URL, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())["access_token"]


def _sp_api_request(access_token, method, path, body=None, query=None, sp_api_base=None):
    url = (sp_api_base or SP_API_BASE) + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("x-amz-access-token", access_token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _create_report(access_token, report_type, data_start, data_end, report_options, marketplace_id=None, sp_api_base=None):
    body = {
        "reportType": report_type,
        "marketplaceIds": [marketplace_id or MARKETPLACE_ID_JP],
        "dataStartTime": data_start,
        "dataEndTime": data_end,
        "reportOptions": report_options,
    }
    return _sp_api_request(access_token, "POST", "/reports/2021-06-30/reports", body=body, sp_api_base=sp_api_base)["reportId"]


def _wait_for_report(access_token, report_id, timeout=180, interval=6, sp_api_base=None):
    """DONE/CANCELLED/FATAL'a kadar bekler. Reports API'nin kendi kotasi var
    (asiri sik pollamamak icin interval >= 5sn tutuluyor -- canli dogrulandi,
    3 gunluk bir rapor ~20-25sn'de bitiyor)."""
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        status = _sp_api_request(access_token, "GET", f"/reports/2021-06-30/reports/{report_id}", sp_api_base=sp_api_base)
        last_status = status.get("processingStatus")
        if last_status in ("DONE", "CANCELLED", "FATAL"):
            return status
        time.sleep(interval)
    raise TimeoutError(f"Rapor {timeout}sn icinde bitmedi (son durum: {last_status}).")


def _download_report_document(access_token, report_document_id, sp_api_base=None):
    doc = _sp_api_request(access_token, "GET", f"/reports/2021-06-30/documents/{report_document_id}", sp_api_base=sp_api_base)
    with urllib.request.urlopen(doc["url"], timeout=60) as response:
        raw = response.read()
    if doc.get("compressionAlgorithm") == "GZIP":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _flatten_business_report(parsed):
    """salesAndTrafficByAsin listesini duz satirlara cevirir -- kolon adlari
    elle indirilen Business Report CSV'sindeki (SESSIONS, PAGE VIEWS,
    BUYBOX %, UNITS ORDERED, ORDER PRODUCT SALES) karsiliklarina yakin
    tutuldu (analyze_new_reports.py'nin bakabilecegi bicimde, birebir ayni
    basliklar degil -- kapsam sadece 'indirme', analiz scripti ayri)."""
    rows = []
    for entry in parsed.get("salesAndTrafficByAsin", []):
        sales = entry.get("salesByAsin", {})
        traffic = entry.get("trafficByAsin", {})
        ordered_sales = sales.get("orderedProductSales", {})
        rows.append({
            "PARENT_ASIN": entry.get("parentAsin", ""),
            "CHILD_ASIN": entry.get("childAsin", ""),
            "SESSIONS": traffic.get("sessions", 0),
            "PAGE_VIEWS": traffic.get("pageViews", 0),
            "BUYBOX_PERCENTAGE": traffic.get("buyBoxPercentage", 0),
            "UNITS_ORDERED": sales.get("unitsOrdered", 0),
            "ORDER_PRODUCT_SALES": ordered_sales.get("amount", 0),
            "ORDER_PRODUCT_SALES_CURRENCY": ordered_sales.get("currencyCode", ""),
            "UNITS_REFUNDED": sales.get("unitsRefunded", 0),
            "REFUND_RATE": sales.get("refundRate", 0),
            "TOTAL_ORDER_ITEMS": sales.get("totalOrderItems", 0),
        })
    return rows


def _write_csv(rows, out_path):
    import csv
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "PARENT_ASIN", "CHILD_ASIN", "SESSIONS", "PAGE_VIEWS", "BUYBOX_PERCENTAGE",
        "UNITS_ORDERED", "ORDER_PRODUCT_SALES", "ORDER_PRODUCT_SALES_CURRENCY",
        "UNITS_REFUNDED", "REFUND_RATE", "TOTAL_ORDER_ITEMS",
    ]
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fetch_business_report(out_path, days=30, start_date=None, end_date=None, progress=None, credentials=None, marketplace_id=None, sp_api_base=None):
    """Uctan uca: Sales and Traffic (Business Report) verisini ceker, ASIN
    bazinda duzlestirip `out_path`'e CSV olarak yazar. Diger CDP tabanli
    fonksiyonlarla (easycentral_target.py) AYNI sozlesme: basari/basarisizlik
    bilgisini bir dict olarak dondurur, hata durumunda exception FIRLATMAZ
    (cagiran GUI'nin try/except'e ihtiyaci olmasin diye).

    Tarih araligi -- ONCELIK SIRASI:
      1) start_date/end_date verilmisse (ikisi de "YYYY-MM-DD" metni ya da
         datetime.date/datetime) -- ozel/manuel secilen aralik.
      2) verilmezse `days` kullanilir -- bugunden geriye `days` gun
         (varsayilan 30). ONCEKI HALDE bu deger her zaman sabitti (30,
         hicbir ayar yoktu) -- musteriden geldi (2026-09-22), artik GUI'de
         hem hazir secenekler (7/30/60/90 gun) hem ozel baslangic/bitis
         tarihi girilebiliyor.

    credentials/marketplace_id/sp_api_base: opsiyonel -- verilmezse bu
    modulun kendi load_credentials()'i (ic/kisisel kullanim icin ortam
    degiskeni/yerel dosya) ve JP pazar yeri kullanilir. AsinSeeker GUI'si
    MUSTERININ kendi (keyring'deki, bkz. amazon_sp_api_settings.py) kimlik
    bilgilerini ve pazar yerini BURADAN vermek zorunda -- vermezse dahili
    (kisisel) kimlik araniyor ve musteri makinesinde bulunamaz.

    progress: opsiyonel, tek string parametre alan callback -- GUI'nin
    'Durum' etiketini canli guncellemesi icin (bkz. easycentral_target.py'deki
    ayni desen)."""
    def report(msg):
        if progress:
            progress(msg)

    try:
        report("Kimlik bilgileri okunuyor...")
        credentials = credentials or load_credentials()
        marketplace_id = marketplace_id or MARKETPLACE_ID_JP
        report("Amazon'a giris yapiliyor (LWA)...")
        access_token = get_access_token(credentials)

        import datetime

        def _to_date(value):
            if isinstance(value, str):
                return datetime.datetime.strptime(value, "%Y-%m-%d").date()
            if isinstance(value, datetime.datetime):
                return value.date()
            return value

        if start_date and end_date:
            start_d = _to_date(start_date)
            end_d = _to_date(end_date)
            if start_d > end_d:
                return {"ok": False, "message": "Başlangıç tarihi bitiş tarihinden sonra olamaz."}
            data_start = start_d.strftime("%Y-%m-%dT00:00:00Z")
            data_end = (end_d + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
            range_desc = f"{start_d} -> {end_d}"
        else:
            end = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
            start = end - datetime.timedelta(days=days)
            data_start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
            data_end = end.strftime("%Y-%m-%dT%H:%M:%SZ")
            range_desc = f"son {days} gün"

        report(f"Rapor talep ediliyor ({range_desc})...")
        report_id = _create_report(
            access_token, REPORT_TYPE_BUSINESS, data_start, data_end,
            {"dateGranularity": "DAY", "asinGranularity": "CHILD"},
            marketplace_id=marketplace_id, sp_api_base=sp_api_base,
        )

        report("Rapor hazirlaniyor, bekleniyor...")
        status = _wait_for_report(access_token, report_id, sp_api_base=sp_api_base)
        if status.get("processingStatus") != "DONE":
            return {"ok": False, "message": f"Rapor basarisiz oldu (durum: {status.get('processingStatus')})."}

        report("Rapor indiriliyor...")
        parsed = _download_report_document(access_token, status["reportDocumentId"], sp_api_base=sp_api_base)
        rows = _flatten_business_report(parsed)

        report(f"{len(rows)} ASIN satiri CSV'ye yaziliyor...")
        _write_csv(rows, out_path)

        return {
            "ok": True,
            "message": f"{len(rows)} ASIN'lik Business Report indirildi -> {out_path}",
            "path": str(out_path),
            "row_count": len(rows),
        }
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return {"ok": False, "message": f"Amazon API hatasi ({e.code}): {detail[:300]}"}
    except Exception as e:
        return {"ok": False, "message": f"Beklenmeyen hata: {e}"}


def fetch_inventory_report(out_path, progress=None, credentials=None, marketplace_id=None, sp_api_base=None):
    """Uctan uca: magazanin GUNCEL aktif listeleme/stok envanterini
    (GET_MERCHANT_LISTINGS_ALL_DATA -- Seller Central'daki 'Manage
    Inventory' export'unun SP-API karsiligi) ceker, ham TSV'yi normal
    virgullu CSV'ye cevirip `out_path`'e yazar. CANLI DOGRULANDI (JP
    pazar yerinde): 19.893 ASIN, quantity (stok adedi) kolonu dahil --
    EasyCentral'daki 'Aktif Urunler' sayisiyla (19.627-19.891 araligi,
    canli dogrulandi) ayni buyuklukte.

    credentials/marketplace_id/sp_api_base: bkz. fetch_business_report."""
    def report(msg):
        if progress:
            progress(msg)

    try:
        report("Kimlik bilgileri okunuyor...")
        credentials = credentials or load_credentials()
        marketplace_id = marketplace_id or MARKETPLACE_ID_JP
        report("Amazon'a giris yapiliyor (LWA)...")
        access_token = get_access_token(credentials)

        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

        report("Envanter raporu talep ediliyor...")
        report_id = _create_report(access_token, REPORT_TYPE_INVENTORY, now, now, {}, marketplace_id=marketplace_id, sp_api_base=sp_api_base)

        report("Rapor hazirlaniyor, bekleniyor...")
        status = _wait_for_report(access_token, report_id, sp_api_base=sp_api_base)
        if status.get("processingStatus") != "DONE":
            return {"ok": False, "message": f"Rapor basarisiz oldu (durum: {status.get('processingStatus')})."}

        report("Rapor indiriliyor...")
        access_token = get_access_token(credentials)  # onceki bekleme uzun surmus olabilir, token tazele
        doc = _sp_api_request(access_token, "GET", f"/reports/2021-06-30/documents/{status['reportDocumentId']}", sp_api_base=sp_api_base)
        with urllib.request.urlopen(doc["url"], timeout=60) as response:
            raw = response.read()
        if doc.get("compressionAlgorithm") == "GZIP":
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8-sig")

        import csv
        import io
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        rows = list(reader)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=reader.fieldnames or _INVENTORY_TSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        report(f"{len(rows)} ASIN satırı CSV'ye yazılıyor...")
        return {
            "ok": True,
            "message": f"{len(rows)} ASIN'lik envanter (stok) raporu indirildi -> {out_path}",
            "path": str(out_path),
            "row_count": len(rows),
        }
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return {"ok": False, "message": f"Amazon API hatasi ({e.code}): {detail[:300]}"}
    except Exception as e:
        return {"ok": False, "message": f"Beklenmeyen hata: {e}"}
