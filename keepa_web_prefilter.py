"""Keepa web sitesindeki "Product Viewer" ozelligini (API token'larindan
TAMAMEN AYRI bir "Data Allowance" kotasi kullanir -- canli dogrulandi)
kullanarak, TOPLU halde "Tracking since" (1 yillik veri sarti icin) ve
OOS%/offer count gibi ozet sinyalleri UCRETSIZE YAKIN sekilde ceker.

ONEMLI SINIR (canli dogrulandi): Bu export SADECE ozet/anlik istatistikler
icerir -- bizim "kesinti" (gap) kontrolumuzun ihtiyac duydugu HAM zaman
serisi (COUNT_NEW grafiginin kendisi) YOKTUR. Yani bu modul, API tabanli
keepa_check_detailed_api()'nin YERINE GECMEZ -- sadece ONUNDEN bir ON-FILTRE
katmani olarak calisir: acikca kotu olanlari (1 yillik gecmisi olmayan,
son 90 gunde surekli stoksuz olan) API'ye hic sormadan elemeyi saglar,
boylece kisitli API token butcesi sadece gercekten umut vaat eden
adaylara harcanir.

Nasil calisir: Keepa'nin Product Viewer sayfasi, URL'nin kendisinde
(#!viewer/{"5":[...]}) hangi ASIN'lerin yuklu oldugunu tasir -- yani
"Import List" dugmesine tiklamaya/yazi yazmaya GEREK YOK, dogrudan bu
URL'ye gidip Export akisini otomatiklestiriyoruz (canli dogrulandi,
1000 ASIN tek seferde sorunsuz yuklendi).
"""

import csv
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from keepa_check import close_cdp_tab, open_cdp_session  # noqa: E402

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
AUTOMATION_PROFILE = r"C:\chrome_debug_temp"
DEBUG_ADDRESS = "127.0.0.1:9222"
DOMAIN = 5  # amazon.co.jp

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path.home() / "Downloads"  # Browser.setDownloadBehavior canli testte calismadi -- Chrome varsayilanina duser
STAGING_DIR = BASE_DIR / "keepa_full_kontrol" / "web_export_indirilenler"

CLICK_EXPR = """
(function (selectorList, textList) {
    function visible(el) { return !!(el && (el.offsetWidth || el.offsetHeight)); }
    for (var s = 0; s < selectorList.length; s++) {
        var el = document.querySelector(selectorList[s]);
        if (visible(el)) {
            var r = el.getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
    }
    var all = Array.from(document.querySelectorAll('*'));
    for (var t = 0; t < textList.length; t++) {
        var match = all.find(function (e) {
            return visible(e) && (e.textContent || '').trim() === textList[t];
        });
        if (match) {
            var rr = match.getBoundingClientRect();
            return {x: rr.x + rr.width / 2, y: rr.y + rr.height / 2};
        }
    }
    return null;
})(%(selectors)s, %(texts)s)
"""

PAGE_CONTAINS_EXPR = """document.body.innerText.replace(/,/g, '').indexOf(%(needle)r) !== -1"""


def _find_point(session, selectors=(), texts=()):
    expr = CLICK_EXPR % {"selectors": json.dumps(list(selectors)), "texts": json.dumps(list(texts))}
    return session.eval_json(expr)


def _click_point(session, pt):
    session.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": pt["x"], "y": pt["y"]})
    session.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pt["x"], "y": pt["y"], "button": "left", "clickCount": 1})
    session.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pt["x"], "y": pt["y"], "button": "left", "clickCount": 1})


def _click_text(session, text, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pt = _find_point(session, texts=[text])
        if pt:
            _click_point(session, pt)
            return True
        time.sleep(0.3)
    return False


def build_viewer_url(asins, domain=DOMAIN):
    payload = json.dumps({str(domain): asins}, separators=(",", ":"))
    return "https://keepa.com/#!viewer/" + urllib.parse.quote(payload, safe="")


def _ensure_chrome():
    tabs = _list_tabs()
    if tabs is not None:
        return
    subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_ADDRESS.rsplit(':', 1)[1]}",
        f"--user-data-dir={AUTOMATION_PROFILE}",
        "--profile-directory=Default",
        # Bu bayrak olmadan yeni Chrome surumleri localhost'tan gelen CDP
        # WebSocket baglantilarini bile 403 ile reddediyor (bkz.
        # easycentral_keepa_bot.py'deki ayni bayrak icin canli dogrulanmis not).
        "--remote-allow-origins=*",
        "about:blank",
    ])
    time.sleep(4)


def _list_tabs():
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{DEBUG_ADDRESS}/json/list", timeout=3) as response:
            return json.loads(response.read())
    except Exception:
        return None


def export_batch(asins, out_csv_path, timeout=60):
    """Verilen ASIN listesini Product Viewer'da acar, "All active columns" +
    CSV secip Export eder, indirilen dosyayi out_csv_path'e tasir. Basarili
    olursa True, olmazsa False doner (cagiran taraf o dilimi API'ye
    dusurmeye devam edebilir -- bu adim SADECE bir optimizasyon, kritik yol
    degil)."""
    try:
        return _export_batch_inner(asins, out_csv_path, timeout)
    except Exception as error:
        # CDP/websocket baglantisi (Chrome cokmesi, sekme kapanmasi, agli
        # kesintisi) her turlu -- bu adim SADECE bir optimizasyon oldugu
        # icin (bkz. yukaridaki docstring), tek bir dilimin cokmesi TUM
        # islemi durdurmamali. Cagiran taraf bu dilimi atlar, devam eder.
        print(f"  (export_batch hatasi, dilim atlaniyor: {type(error).__name__}: {error})")
        return False


def _export_batch_inner(asins, out_csv_path, timeout):
    _ensure_chrome()
    url = build_viewer_url(asins)

    tab, session = open_cdp_session(DEBUG_ADDRESS)
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Page.navigate", {"url": url})
        time.sleep(3)

        needle = f"to {len(asins)} of {len(asins)}"
        deadline = time.time() + timeout
        loaded = False
        while time.time() < deadline:
            expr = PAGE_CONTAINS_EXPR % {"needle": needle}
            if session.eval_json(expr):
                loaded = True
                break
            time.sleep(1)
        if not loaded:
            return False

        before = set(p.name for p in DOWNLOAD_DIR.glob("KeepaExport*.csv"))

        if not _click_text(session, "Export"):
            return False
        time.sleep(0.6)
        if not _click_text(session, "All active columns"):
            return False
        if not _click_text(session, "CSV"):
            return False
        time.sleep(0.3)
        # Son "Export" dugmesi (modal icindeki, ust menudekinden farkli --
        # modal acikken metin araligimizda ikisi de "Export" iceriyor, ama
        # modal en son DOM'a eklendigi/ust katmanda oldugu icin nokta
        # arama fonksiyonumuz onu buluyor; canli dogrulandi).
        pt = _find_point(session, selectors=["button.mat-mdc-raised-button", "button[color='primary']"], texts=["Export"])
        if not pt:
            return False
        _click_point(session, pt)

        deadline = time.time() + 30
        new_file = None
        while time.time() < deadline:
            after = set(p.name for p in DOWNLOAD_DIR.glob("KeepaExport*.csv"))
            new_names = after - before
            if new_names:
                new_file = DOWNLOAD_DIR / sorted(new_names)[-1]
                break
            time.sleep(1)
        if new_file is None:
            return False

        time.sleep(1)  # dosya yazimi tamamlansin
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        out_csv_path = Path(out_csv_path)
        out_csv_path.write_bytes(new_file.read_bytes())
        new_file.unlink(missing_ok=True)
        return True
    finally:
        session.close()
        # Her dilim YENI bir sekme aciyor -- kapatmazsak Chrome'da sekmeler
        # birikip (canli dogrulandi: 10 dilimde 20 sekme) belleği/CDP baglantı
        # kararliligini bozuyor, uzun kosularda coke yol aciyor.
        try:
            close_cdp_tab(DEBUG_ADDRESS, tab.get("id"))
        except Exception:
            pass


def parse_export_csv(path):
    """Export edilen CSV'yi ASIN -> ozet sinyaller sozlugune cevirir."""
    records = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            asin = (row.get("ASIN") or "").strip()
            if not asin:
                continue
            records[asin] = {
                "tracking_since": row.get("Tracking since") or "",
                "total_offer_count": row.get("Total Offer Count") or "",
                "new_oos_90": row.get("New: 90 days OOS") or "",
                "buy_box_oos_90": row.get("Buy Box: 90 days OOS") or "",
                "sales_rank_drops_90": row.get("Sales Rank: Drops last 90 days") or "",
                "sales_rank_current": row.get("Sales Rank: Current") or "",
            }
    return records
