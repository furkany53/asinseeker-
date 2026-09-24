import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websocket  # pip: websocket-client

# PyInstaller onefile ile paketlenince __file__ GERCEK exe konumunu degil,
# her calistirmada silinen GECICI bir cikarma klasorunu gosterir --
# ekran goruntuleri boylece hicbir zaman kalici olmayan bir yere
# yaziliyordu (denendi, dogrulandi). Frozen halde sys.executable'in
# bulundugu GERCEK klasoru kullaniyoruz.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_DIR / "keepa_screenshots"
KEEPA_URL_TEMPLATE = "https://keepa.com/#!product/{domain}-{asin}"
DEBUG_ADDRESS = os.getenv("CHROME_DEBUG_ADDRESS", "127.0.0.1:9222")

# --- Keepa satıcı-sayısı grafiği kontrolü (Chrome DevTools Protocol) -------------
#
# Onceki yaklasim ekran goruntusu alip HSV renk araligiyla mor/menekse cizgiyi
# piksel piksel tarardi; DPI, zoom, tema ve canvas kirpma tahminlerine bagimliydi
# ve guvenilir kesinti tespiti yapamiyordu.
#
# Bunun yerine Keepa'nin grafigi cizerken kullandigi CanvasRenderingContext2D
# metodlarini (moveTo/lineTo/stroke) sayfa yuklenmeden once "hook"luyoruz.
# Keepa, veride bir kopukluk oldugunda cizgiyi yeni bir alt-parcaya (moveTo ile)
# basliyor; yani gercek kesinti sayisi = o serinin path'indeki moveTo sayisi - 1.
# Bu, piksel taramasindan tamamen bagimsiz, dogrudan Keepa'nin kendi cizim
# komutlarindan okunan kesin bir sinyal.
#
# NOT: Selenium'un execute_cdp_cmd()'i her cagrida gecici bir CDP baglantisi acip
# hemen kapattigi icin, buraya kaydedilen "addScriptToEvaluateOnNewDocument" hook'u
# driver.get() navigasyonuna kadar hayatta kalmiyor (denendi, dogrulandi). Bu yuzden
# Keepa kontrolu icin Selenium yerine DevTools Protokolune kalici, kendi actigimiz
# bir WebSocket baglantisi kullaniyoruz.
#
# TARGET_LINE_STYLE: 2026-09-06/07 tarihinde canli Keepa sayfalarinda hook ile
# olculdu -- "New Offer Count" serisi bu renkle ciziliyor.
TARGET_LINE_STYLE = "#8888dd"

CANVAS_HOOK_JS = """
(function () {
    if (window.__hooked) return;
    window.__hooked = true;
    window.__paths = [];
    var proto = CanvasRenderingContext2D.prototype;
    var stateMap = new WeakMap();
    function state(ctx) {
        var s = stateMap.get(ctx);
        if (!s) { s = { ops: [] }; stateMap.set(ctx, s); }
        return s;
    }
    var origBeginPath = proto.beginPath;
    proto.beginPath = function () {
        state(this).ops = [];
        return origBeginPath.apply(this, arguments);
    };
    var origMoveTo = proto.moveTo;
    proto.moveTo = function (x, y) {
        state(this).ops.push({ t: 'm', x: x, y: y });
        return origMoveTo.apply(this, arguments);
    };
    var origLineTo = proto.lineTo;
    proto.lineTo = function (x, y) {
        state(this).ops.push({ t: 'l', x: x, y: y });
        return origLineTo.apply(this, arguments);
    };
    var origStroke = proto.stroke;
    proto.stroke = function () {
        var s = state(this);
        if (s.ops && s.ops.length) {
            window.__paths.push({
                style: this.strokeStyle,
                canvasW: this.canvas ? this.canvas.width : null,
                canvasH: this.canvas ? this.canvas.height : null,
                ops: s.ops.slice()
            });
        }
        return origStroke.apply(this, arguments);
    };
})();
"""

YEAR_ACTIVE_EXPR = """
(function () {
    var els = document.querySelectorAll("td.legendRange[range='8760']");
    if (!els.length) return false;
    var el = els[els.length - 1];
    var classes = (el.className || '').toString().split(/\\s+/);
    return classes.indexOf('active') !== -1;
})()
"""

# Tek sorguda hem "grafik kontrolleri hic geldi mi" hem "1Y secenegi var mi"
# hem de "1Y zaten aktif mi" bilgisini toplar -- ucu ayri ayri sorunun ucu ayri
# nedenlere isaret ediyor (bkz. asagidaki keepa_check_detailed).
YEAR_RANGE_STATE_EXPR = """
(function () {
    var all = document.querySelectorAll("td.legendRange");
    var yearEls = document.querySelectorAll("td.legendRange[range='8760']");
    var yearEl = yearEls.length ? yearEls[yearEls.length - 1] : null;
    var active = yearEl
        ? (yearEl.className || '').toString().split(/\\s+/).indexOf('active') !== -1
        : false;
    return { anyRangeExists: all.length > 0, yearExists: !!yearEl, yearActive: active };
})()
"""

# Denendi/dogrulandi: 1Y secenegi sayfada VAR olsa bile Keepa bazen onceki
# oturumdan hatirlanan farkli bir araligi varsayilan aktif gosteriyor (rastgele,
# gorunmez pencere + coklu sekme altinda daha sik). Pasif bekleme yerine dogrudan
# tiklayip aktif hale getirmek daha guvenilir. Keepa'nin dinleyicisi hangi mouse
# olayina bagliysa yakalansin diye ucunu de tetikliyoruz.
CLICK_YEAR_RANGE_EXPR = """
(function () {
    var els = document.querySelectorAll("td.legendRange[range='8760']");
    if (!els.length) return false;
    var el = els[els.length - 1];
    ['mousedown', 'mouseup', 'click'].forEach(function (type) {
        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    });
    return true;
})()
"""

# Keepa'da grafigin hic olusmadigi iki farkli kalici mesaj gorduk: urun hic
# stokta olmamis, veya Amazon bu urun icin hic veri saglamiyor (dogrulandi:
# B07K1KH7J9/B0F4DJHJSH ilki, B08D8SJF9W ikincisi). Ikisi de "bu ASIN icin
# grafik zaten hic olmayacak" demek, tekrar denemenin anlami yok.
NO_DATA_EXPR = """
(function () {
    var text = document.body.innerText || '';
    return text.indexOf('never been in stock') !== -1
        || text.indexOf('does not provide any data') !== -1;
})()
"""

# Ekran goruntusunde sadece bizim taradigimiz grafik (satici sayisi / Rating-
# Offer grafigi) gorunsun istiyoruz -- ust taraftaki fiyat grafigi bizim icin
# onemli degil. Sayfadaki buyuk canvas'lar arasindan en altta olani (DOM'da
# ve ekranda) hedef aliyoruz, tipki eski piksel-tarama yonteminde oldugu gibi.
BOTTOM_CANVAS_RECT_EXPR = """
(function () {
    var canvases = Array.prototype.filter.call(document.querySelectorAll('canvas'), function (c) {
        return c.width > 300 && c.height > 80;
    });
    if (!canvases.length) return null;
    var bottom = canvases[0];
    var bottomTop = bottom.getBoundingClientRect().top;
    canvases.forEach(function (c) {
        var top = c.getBoundingClientRect().top;
        if (top > bottomTop) { bottom = c; bottomTop = top; }
    });
    var rect = bottom.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
})()
"""

# NOT: canvas'in TAM genisligi grafik alani degildir -- saginda bir legend
# kutusu var (yaklasik %25-30). "Telef supheli" kontrolu icin "bugune kadar
# uzaniyor mu" sorusunu canvas genisligine degil, AYNI canvas'ta cizilen
# TUM path'lerin (eksenler, diger seriler dahil) ulastigi gercek en sag
# noktaya (plotMaxX) gore soruyoruz -- bu, legend'i otomatik disarida birakir.
BEST_PATH_OPS_EXPR = f"""
(function () {{
    var allPaths = (window.__paths || []);
    var targetPaths = allPaths.filter(function (p) {{ return p.style === '{TARGET_LINE_STYLE}'; }});
    if (!targetPaths.length) return null;
    var best = targetPaths.reduce(function (a, b) {{
        return (b.canvasW * b.canvasH > a.canvasW * a.canvasH) ? b : a;
    }});
    var sameCanvas = allPaths.filter(function (p) {{
        return p.canvasW === best.canvasW && p.canvasH === best.canvasH;
    }});
    var plotMaxX = 0;
    sameCanvas.forEach(function (p) {{
        p.ops.forEach(function (op) {{ if (op.x > plotMaxX) plotMaxX = op.x; }});
    }});
    return {{ canvasW: best.canvasW, ops: best.ops, plotMaxX: plotMaxX }};
}})()
"""

# Keepa'nin cizim kutuphanesi, gercek bir veri kopmasi olmasa bile ara sira
# ayni (ya da neredeyse ayni) noktada 1-5 pikselik "sahte" moveTo kopmasi
# birakiyor (dogrulandi: B007SWRBVO'da x=164.4->166.4, y AYNI kaliyor -- gozle
# gorulur hicbir sey yok). Bunlari gercek kesintilerden ayirmak icin kopmayi
# mutlak piksel yerine cizginin KENDI toplam genisligine oranla degerlendiriyoruz;
# esik altindaki kopmalar rendering artefakti sayilip yok sayilir.
GAP_MIN_FRACTION = float(os.getenv("KEEPA_GAP_FRACTION", "0.02"))


def _count_real_gaps(ops):
    if not ops:
        return None, []
    xs = [op["x"] for op in ops]
    total_span = max(xs) - min(xs)
    threshold = total_span * GAP_MIN_FRACTION if total_span > 0 else 0
    gaps = []
    prev_x = None
    for index, op in enumerate(ops):
        if op["t"] == "m" and index > 0:
            gap_px = op["x"] - prev_x
            if gap_px > threshold:
                gaps.append(round(gap_px, 1))
        prev_x = op["x"]
    return len(gaps), gaps


# "Telef supheli" (satislari/stoktan dusmus urun) kontrolu: satici sayisi
# cizgisi grafigin SAG UCUNA (bugune) kadar uzanmiyorsa, gecmiste satici
# olsa bile su an hic aktif satici gorunmuyor demektir -- urun fiilen
# satisa kapanmis/stoktan dusmus olabilir. Bu, orta kisimda gecici bir
# kesintiden (kesinti_var) farkli, daha ciddi bir sinyal.
DEAD_STOCK_TRAILING_FRACTION = float(os.getenv("KEEPA_DEAD_STOCK_FRACTION", "0.02"))


def _is_dead_stock(ops, plot_max_x):
    if not ops or not plot_max_x:
        return False
    last_x = ops[-1]["x"]
    return (plot_max_x - last_x) > (plot_max_x * DEAD_STOCK_TRAILING_FRACTION)


def http_json(debug_address, path, method="GET"):
    url = f"http://{debug_address}{path}"
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def open_cdp_tab(debug_address):
    # Chrome 111+ '/json/new' icin PUT bekliyor.
    return http_json(debug_address, "/json/new?about:blank", method="PUT")


def close_cdp_tab(debug_address, tab_id):
    try:
        http_json(debug_address, f"/json/close/{tab_id}")
    except Exception:
        pass


def open_cdp_session(debug_address, tab=None):
    """Bir CDP sekmesi + oturumu birlikte acar. `tab` verilmezse YENI bir
    sekme acilir; CDPSession kurulumu (websocket connect) BASARISIZ olursa
    bu YENI acilan sekme kapatilir ve exception yeniden firlatilir -- boru
    hatti asagi cagrilarin try/finally'e ULASAMADAN cikip sekmeyi sizdirmasini
    onler (bircok yerde ayni desen tekrarlaniyordu, bkz. keepa_web_prefilter.py
    icindeki '10 dilimde 20 sekme birikti' notu). `tab` disaridan verildiyse
    (mevcut/yeniden kullanilan bir sekme) BURADA ACILMADIGI icin hata
    durumunda KAPATILMAZ -- sahibi degiliz."""
    owns_tab = tab is None
    if tab is None:
        tab = open_cdp_tab(debug_address)
    try:
        session = CDPSession(tab["webSocketDebuggerUrl"])
    except Exception:
        if owns_tab:
            close_cdp_tab(debug_address, tab.get("id"))
        raise
    return tab, session


class CDPSession:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._next_id = 0

    def call(self, method, params=None, timeout=30):
        self._next_id += 1
        msg_id = self._next_id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))

        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(deadline - time.time(), 0.1))
            data = json.loads(self.ws.recv())
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP hata ({method}): {data['error']}")
                return data.get("result", {})
        raise TimeoutError(f"CDP yanıtı gelmedi: {method}")

    def eval_json(self, expression):
        result = self.call(
            "Runtime.evaluate",
            {"expression": f"JSON.stringify({expression})", "returnByValue": True},
        )
        value = result.get("result", {}).get("value")
        return json.loads(value) if value is not None else None

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def save_cdp_screenshot(session, asin, clip=None):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    params = {"format": "png"}
    if clip:
        params["clip"] = {
            "x": clip["x"], "y": clip["y"],
            "width": clip["width"], "height": clip["height"],
            "scale": 1,
        }
    result = session.call("Page.captureScreenshot", params)
    screenshot_path = SCREENSHOT_DIR / f"{asin}.png"
    screenshot_path.write_bytes(base64.b64decode(result["data"]))
    return screenshot_path


def save_target_chart_screenshot(session, asin):
    """Sadece taranan (satici sayisi) grafigini kirpip kaydeder; canvas
    bulunamazsa tam sayfa goruntusune duser (erken hata durumlarinda oldugu gibi).

    NOT: Keepa sayfasi zoom!=1 ile render edilebiliyor (dogrulandi: 0.8).
    getBoundingClientRect() (BOTTOM_CANVAS_RECT_EXPR) koordinatlari CSS/zoom'lu
    layout uzayinda donuyor, ama Page.captureScreenshot'in clip parametresi
    fiziksel viewport uzayinda bekleniyor -- ikisi arasindaki fark tam olarak
    zoom orani kadar. Zoom'la carpmadan kullanmak kirpmayi asagi kaydiriyordu
    (grafik yerine altindaki metin/istatistik alani yakalaniyordu)."""
    rect = session.eval_json(BOTTOM_CANVAS_RECT_EXPR)
    if not rect:
        return save_cdp_screenshot(session, asin)
    layout = session.call("Page.getLayoutMetrics")
    zoom = layout.get("cssVisualViewport", {}).get("zoom", 1) or 1

    # Musteri/destek gozle kontrol edebilsin diye kirpma sadece canvas'la
    # sinirli kalmasin -- canvas'in saginda Keepa'nin "Range: Day/Week/Month/
    # 3 Months/Year/All" secim listesi var (1Y'nin gercekten VAR/AKTIF olup
    # olmadigini gosteren yer). Genisligi sayfa sonuna kadar uzatip o listeyi
    # de goruntuye dahil ediyoruz.
    page_width = layout.get("cssLayoutViewport", {}).get("clientWidth")
    if page_width:
        rect = dict(rect)
        rect["width"] = max(rect["width"], page_width - rect["x"] - 10)

    scaled_rect = {key: rect[key] * zoom for key in ("x", "y", "width", "height")}
    return save_cdp_screenshot(session, asin, clip=scaled_rect)


def keepa_check_detailed(
    asin,
    debug_address=None,
    take_screenshot=True,
    require_year=True,
    check_gaps=True,
    check_dead_stock=False,
    check_target_market=False,
    target_market_domain=None,
    api_key=None,
    domain=None,
):
    """Keepa kontrolunu yapar ve status'un yaninda NEDENini de dondurur
    (batch/log ihtiyaci icin -- hangi ASIN neden silindi/yuklendi gorulebilsin diye).

    require_year: 1 yillik veri gecmisi sart mi (yoksa dogrudan delete).
    check_gaps: grafikteki veri kesintisi (moveTo kopmasi) delete sebebi sayilsin mi.
    check_dead_stock: cizgi grafigin sag ucuna (bugune) ulasmiyorsa (telef
        supheli -- satis/stoktan dusmus olabilir) delete sebebi sayilsin mi.
    check_target_market: hedef pazarda (varsayilan Amazon.com) ASIN hic
        bulunamiyorsa delete sebebi sayilsin mi -- bkz. check_exists_on_target_market.
        Chrome'a ihtiyaci yok, ayri bir Keepa API cagrisi (api_key gerekir).
    domain: KAYNAK pazar -- Keepa'nin urun sayfasi hangi ulke icin acilsin
        (varsayilan: API_DOMAIN, yani 5/Japonya). Musteri farkli bir
        pazardan kaynak buluyorsa GUI'den degistirilir.
    """
    domain = domain or API_DOMAIN
    debug_address = debug_address or DEBUG_ADDRESS
    tab, session = open_cdp_session(debug_address)
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Page.addScriptToEvaluateOnNewDocument", {"source": CANVAS_HOOK_JS})
        session.call("Page.navigate", {"url": KEEPA_URL_TEMPLATE.format(domain=domain, asin=asin)})

        time.sleep(3)
        # Once grafik kontrollerinin (herhangi bir legendRange) sayfaya gelmesini
        # bekliyoruz. Coklu (paralel) sekmede Chrome'un CPU'yu paylasmasi yuzunden
        # bu bazen 20sn'den uzun surebiliyor (dogrulandi: B07KGHJGKW, B07KGGDPKL,
        # B002U5MPB0 -- tek basina 20sn icinde sorunsuz yukleniyor, 5 paralel
        # sekme altinda yetismiyordu) -- bu yuzden payi genis tutuyoruz. Kesin
        # "veri hic yok" mesaji (NO_DATA_EXPR) gorursek daha fazla beklemeden
        # erken cikiyoruz, cunku o durumda grafik zaten hic gelmeyecek.
        deadline = time.time() + 40
        state = session.eval_json(YEAR_RANGE_STATE_EXPR)
        no_data = False
        while time.time() < deadline and not state["anyRangeExists"]:
            no_data = bool(session.eval_json(NO_DATA_EXPR))
            if no_data:
                break
            time.sleep(0.5)
            state = session.eval_json(YEAR_RANGE_STATE_EXPR)

        def _mark(suffix):
            if screenshot_path is not None:
                screenshot_path.replace(SCREENSHOT_DIR / f"{asin}{suffix}")

        if not state["anyRangeExists"]:
            if not no_data:
                no_data = bool(session.eval_json(NO_DATA_EXPR))
            screenshot_path = save_cdp_screenshot(session, asin) if take_screenshot else None
            if no_data:
                _mark("_SIL_VERIYOK.png")
                return {
                    "asin": asin, "status": "delete", "reason": "veri_yok",
                    "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": None,
                }
            _mark("_SIL_GRAFIKYOK.png")
            return {
                "asin": asin, "status": "delete", "reason": "grafik_yuklenemedi",
                "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": None,
            }

        if not state["yearExists"]:
            # 6 aralik dugmesi (1G/1H/1A/3A/1Y/Tumu) rendered ama 1Y (8760) yok
            # -- gercekten 1 yildan az takip gecmisi var demek.
            if require_year:
                screenshot_path = save_cdp_screenshot(session, asin) if take_screenshot else None
                _mark("_SIL_YILYOK.png")
                return {
                    "asin": asin, "status": "delete", "reason": "yillik_secenek_yok",
                    "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": None,
                }
            # require_year kapali: 1Y yok ama devam edip mevcut aktif araligi
            # (Keepa'nin gosterdigi varsayilan araligi) analiz ediyoruz.
        elif not state["yearActive"]:
            # 1Y secenegi var ama varsayilan olarak aktif degil (onceki
            # oturumdan hatirlanan farkli bir aralik gosterilebiliyor, dogrulandi:
            # B0DJWYVTZ3). Pasif beklemek yerine dogrudan tiklayip aktif hale
            # getiriyoruz; onceki (yanlis araligin) cizimlerini de temizliyoruz.
            session.call("Runtime.evaluate", {"expression": CLICK_YEAR_RANGE_EXPR})
            session.call("Runtime.evaluate", {"expression": "window.__paths = [];"})
            click_deadline = time.time() + 15
            while time.time() < click_deadline and not session.eval_json(YEAR_ACTIVE_EXPR):
                time.sleep(0.3)

        time.sleep(1.5)  # grafik (yeniden) cizilsin

        screenshot_path = save_target_chart_screenshot(session, asin) if take_screenshot else None

        path_data = session.eval_json(BEST_PATH_OPS_EXPR)
        ops = path_data["ops"] if path_data else None
        plot_max_x = path_data["plotMaxX"] if path_data else None

        gap_count, gaps_px = _count_real_gaps(ops)
        if gap_count is None:
            _mark("_SIL_CIZGIYOK.png")
            return {
                "asin": asin, "status": "delete", "reason": "cizgi_bulunamadi",
                "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": None,
            }

        dead_stock = _is_dead_stock(ops, plot_max_x) if check_dead_stock else False

        triggered = []
        if check_gaps and gap_count:
            triggered.append(f"kesinti_var(gap={gap_count})")
        if check_dead_stock and dead_stock:
            triggered.append("telef_suphesi")

        if triggered:
            _mark("_SIL.png")
            return {
                "asin": asin, "status": "delete", "reason": "+".join(triggered),
                "gap_count": gap_count, "gaps_px": gaps_px, "dead_stock_suspected": dead_stock,
            }

        if check_target_market:
            key = api_key or _load_keepa_api_key()
            kwargs = {}
            if target_market_domain is not None:
                kwargs["target_domain"] = target_market_domain
            try:
                exists = check_exists_on_target_market(asin, key, **kwargs)
            except Exception as error:
                raise RuntimeError(f"Hedef pazar kontrolu hatasi ({asin}): {type(error).__name__}: {error}") from error
            if not exists:
                _mark("_SIL_HEDEFPAZARDAYOK.png")
                return {
                    "asin": asin, "status": "delete", "reason": "hedef_pazarda_yok",
                    "gap_count": gap_count, "gaps_px": gaps_px, "dead_stock_suspected": dead_stock,
                }

        _mark("_YUKLE.png")
        # ABD (hedef pazar) kontrolunden gecerek upload olduysa reason'a
        # isaretliyoruz -- boylece bu ASIN'in bir daha check_us_existence.py
        # gibi ayri bir script ile TEKRAR kontrol edilmesine gerek kalmiyor
        # (sonuclar.csv'de bu bilgi kalici olarak isaretlenmis oluyor).
        reason = "uygun+abd_dogrulandi" if check_target_market else "uygun"
        return {
            "asin": asin, "status": "upload", "reason": reason,
            "gap_count": gap_count, "gaps_px": gaps_px, "dead_stock_suspected": dead_stock,
        }
    finally:
        session.close()
        close_cdp_tab(debug_address, tab.get("id"))


# --- API TABANLI KONTROL (Chrome/tarayici GEREKTIRMEZ) ----------------------
#
# keepa_check_detailed() yukarida Chrome'a bakarak (canvas cizim komutlari)
# kesinti tespiti yapiyordu. Bu, Keepa'nin /product API'siyle AYNI sonucu
# CANLI OLARAK DOGRULANMIS sekilde, tarayici hic acmadan verir:
#   - COUNT_NEW (New Offer Count) serisi csv[11]'de [zaman, deger, ...] olarak gelir
#   - Keepa, veri olmayan noktalari deger=-1 ile isaretler -- bu bizim "kesinti"
#   - 4 gercek ASIN'de (B007SWRBVO, B0BVLZQTCF, B09GK4VNK5, B0CFY6BNYQ) Chrome
#     sonucuyla BIREBIR eslesti (B0BVLZQTCF'de kesinti SAYISI bile tutti: 1)
#
# Maliyet: ~1 token/ASIN (temel /product cagrisi).
import gzip as _gzip  # noqa: E402
import urllib.parse as _urllib_parse  # noqa: E402
from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone  # noqa: E402

from keepa_finder import load_api_key as _load_keepa_api_key  # noqa: E402

API_DOMAIN = 5  # amazon.co.jp
COUNT_NEW_CSV_INDEX = 11
API_WINDOW_DAYS = 365  # Keepa'nin "Year" gorunumuyle eslesen pencere


def _keepa_epoch():
    return _datetime(2011, 1, 1, tzinfo=_timezone.utc)


def _fetch_product(asin, api_key, domain=API_DOMAIN, stop_event=None):
    params = {"key": api_key, "domain": domain, "asin": asin, "stats": 180, "history": 1}
    url = "https://api.keepa.com/product?" + _urllib_parse.urlencode(params)
    request = urllib.request.Request(url)
    # Kota asiminda (429) cagirani hic ugrastirmadan burada bekleyip tekrar
    # deniyoruz -- keepa_finder.py'deki ayni desenle tutarli (artan bekleme,
    # en fazla 10 deneme). Ayrica DNS/baglanti hatalarini (URLError -- ornegin
    # "getaddrinfo failed", "baglanti zorla kapatildi") da yeniden deniyoruz:
    # CANLI dogrulandi (2026-09-16, full_pool_check.py'nin 108K'lik taramasinda),
    # bunlar toplam "hata" satirlarinin %95'ini olusturuyordu -- genelde
    # birkac saniyelik gecici ag/DNS dalgalanmasi, Keepa'nin kendisiyle ilgisi
    # yok, ve eskiden HIC yeniden denenmeden direkt "hata" yazip vazgeciliyordu.
    # 429'dan FARKLI olarak kisa bir bekleme yeterli (kota YENILENMESI degil,
    # ag/DNS'in toparlanmasi bekleniyor).
    for attempt in range(10):
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError(f"Durduruldu ({asin})")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = _gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 9:
                time.sleep(min(30 * (attempt + 1), 300))
                continue
            raise
        except urllib.error.URLError as error:
            if attempt < 9:
                time.sleep(min(3 * (attempt + 1), 30))
                continue
            raise RuntimeError(f"Ag/DNS hatasi devam ediyor ({asin}): {error}") from error
    raise RuntimeError(f"Kota asimi devam ediyor ({asin})")


# JP gumruk/mevzuat riski tasiyan urun basliklarini yakalamak icin -- pil/
# lityum ve kablosuz/telsiz cihazlar Giteki (technical conformity) belgesi
# gerektirir, sivi/kozmetik ve gida takviyeleri Saglik Bakanligi izni
# gerektirir, bicak/agizli aletler ve deri urunler ayrica gumruk/CITES
# denetimine tabi olabilir -- kaynak: baska bir sohbette (Gemini) derlenen
# JP dropshipping gumruk riski listesi. Bu SADECE baslik metnine bakar --
# Keepa alan adi/kategori ID'si GEREKTIRMEZ, bu yuzden dogrulanmamis bir
# API alanina guvenmek zorunda kalmadan hemen kullanilabilir.
#
# JAPONCA ANAHTAR KELIMELER (2026-09-14, ikinci bir Gemini degerlendirmesinden
# gelen GECERLI bir elestiri uzerine eklendi): Amazon.co.jp'deki urun
# basliklari COGUNLUKLA Japonca (Katakana/Kanji) yaziliyor -- SADECE Ingilizce
# anahtar kelimelerle arama yapmak, "バッテリー" (battery) veya "化粧品"
# (kozmetik) gibi Japonca yazilmis riskli urunlerin filtreden TAMAMEN
# KACMASINA neden oluyordu. Bu, projenin "verify before trusting" kuralina
# gerek kalmadan dogrudan uygulanabilecek acik bir mantik hatasiydi (yeni bir
# Keepa API alani gerektirmiyor, sadece regex'e Japonca kelime ekliyor).
CUSTOMS_RISK_KEYWORDS_PATTERN = re.compile(
    r"(?i)(battery|lithium|powerbank|power bank|charger|bluetooth|wireless|wi-fi|wifi|"
    r"liquid|cream|oil|spray|lotion|gel|knife|blade|sword|leather|"
    r"collagen|vitamin|supplement|protein|"
    r"バッテリー|リチウム|充電器|モバイルバッテリー|ワイヤレス|ブルートゥース|無線|"
    r"液体|クリーム|オイル|スプレー|ローション|ジェル|"
    r"ナイフ|包丁|刃物|剣|レザー|皮革|"
    r"コラーゲン|ビタミン|サプリメント|プロテイン|化粧品|"
    r"水筒|食器)"
)


def check_customs_risk_title(title):
    """Baslikta JP gumruk/mevzuat riski tasiyan bir anahtar kelime varsa
    ESLESEN kelimeyi, yoksa None dondurur."""
    if not title:
        return None
    match = CUSTOMS_RISK_KEYWORDS_PATTERN.search(title)
    return match.group(0) if match else None


def compute_priority_score(product):
    """0-100 arasi bir "oncelik puani" -- "upload" cikan ASIN'ler arasinda
    HANGISININ Easy'e ONCE gonderilmesi gerektigini soyler. Kesinti/telef
    kontrolu icin ZATEN cekilmis olan AYNI /product yanitindaki "stats"
    alanindan hesaplanir -- EK TOKEN HARCAMAZ.

    2026-09-18 GUNCELLEME (musteriden geldi): eskiden sadece "risk" odakliydi
    (satistan kalkar mi, rekabet cok mu). Artik musterinin is modeli netlesti
    -- JP fiyatini EasyCentral'in kendisi (US maliyeti + kendi markup kurali
    ile) otomatik ayarliyor, biz kar MARJINI hesaplamiyoruz. Bizim isimiz:
    "zaten kanitlanmis satisi olan VE rekabet edebilecegimiz" ASIN'leri
    ONE cikarmak. Bu yuzden iki YENI bilesen eklendi (monthlySold /
    deltaPercent90_monthlySold) -- Keepa'nin dogrudan sattigi urun adedi
    tahmini, salesRankDrops'tan cok daha net bir "kanitlanmis talep" sinyali
    (VARSA -- her urunde gelmiyor, o yuzden opsiyonel/notr-fallback'li).

    Bilesenler (toplam 100 puan):
    - 25p Stok surekliligi: outOfStockPercentage90 ne kadar dusukse o kadar
      iyi -- "satistan kaldirilmis" riskinin en dogrudan Keepa sinyali.
    - 20p Satis hizi (dolayli): salesRankDrops90 ne kadar yuksekse o kadar
      cok satildigi anlamina gelir (Sales Rank'in ne siklikta iyilestigi).
    - 15p Amazon rekabeti: Buy Box'ta Amazon'un kendisi YOKSA tam puan --
      Amazon kendisi satiyorsa 3. parti saticinin Buy Box kazanmasi zordur.
    - 15p Rekabet yogunlugu: aktif satici sayisi (totalOfferCount) ne kadar
      azsa o kadar iyi -- rekabet edebilme sansimiz o kadar yuksek.
    - 15p YENI -- Kanitlanmis satis hacmi: Keepa'nin monthlySold tahmini
      (dogrudan "bu urun ayda ~X adet satiyor" bilgisi) varsa kullanilir.
    - 10p YENI -- Satis trendi: deltaPercent90_monthlySold (satis hacmi son
      90 gunde artiyor mu azaliyor mu) -- artan trend = daha guvenli bahis.

    Veri eksikse o bilesen icin notr (yarim) puan verilir -- eksik veri
    ASIN'i cezalandirmaz ama avantaj da saglamaz.
    """
    stats = product.get("stats") or {}
    score = 0.0

    # outOfStockPercentage* alanlari CSV_TYPE'a gore indislenmis bir DIZI
    # (canli dogrulandi) -- her indis ayri bir teklif turune (0=Amazon,
    # 1=New, 2=Used, ...) karsilik gelir. Biz 3.parti/dropshipping acisindan
    # NEW (index 1) ile ilgileniyoruz.
    NEW_INDEX = 1
    oos_list = stats.get("outOfStockPercentage90") or stats.get("outOfStockPercentage30")
    oos = oos_list[NEW_INDEX] if isinstance(oos_list, list) and len(oos_list) > NEW_INDEX else None
    if oos is not None and oos >= 0:
        score += 25 * max(0.0, 1 - oos / 100.0)
    else:
        score += 25 * 0.5

    drops = stats.get("salesRankDrops90")
    if drops is not None and drops >= 0:
        score += 20 * min(1.0, drops / 20.0)
    else:
        score += 20 * 0.5

    buy_box_is_amazon = stats.get("buyBoxIsAmazon")
    if buy_box_is_amazon is False:
        score += 15
    elif buy_box_is_amazon is None:
        score += 7.5

    offer_count = stats.get("totalOfferCount")
    if offer_count is not None and offer_count >= 1:
        score += 15 * max(0.0, 1 - (offer_count - 1) / 9.0)
    else:
        score += 15 * 0.5

    # monthlySold COGU URUNDE GELMIYOR (Keepa sadece yeterli veri toplanmis
    # urunler icin dolduruyor) -- geldiginde en guclu sinyal, gelmediginde
    # notre dusuyoruz (cezalandirmiyoruz). Ust sinir 50/ay -- dropshipping
    # olcegi icin makul bir "cok iyi satiyor" esigi (canli veriyle
    # kalibre edilmedi, ileride gercek dagilima gore ayarlanabilir).
    monthly_sold = stats.get("monthlySold")
    if monthly_sold is not None and monthly_sold >= 0:
        score += 15 * min(1.0, monthly_sold / 50.0)
    else:
        score += 15 * 0.5

    # -100 (satis hacmi sifirlandi) ile +100 (satis hacmi ikiye katlandi)
    # arasi normalize edilir. Veri yoksa notr.
    trend = stats.get("deltaPercent90_monthlySold")
    if trend is not None:
        score += 10 * max(0.0, min(1.0, (trend + 100) / 200.0))
    else:
        score += 10 * 0.5

    return round(score, 1)


TARGET_MARKET_DOMAIN = 1  # amazon.com -- EasyCentral'in "MyHouse" magazasinin
# capraz-listeledigi hedef pazar. Bu SABIT: musteri baska bir hedef magaza
# (MyHouseAU/MX, Amazon.ca/sg) kullanirsa buraya bakip domain ID'sini
# degistirmesi gerekir (canli dogrulanmis tek eslesme su an bu).


def check_exists_on_target_market(asin, api_key, target_domain=TARGET_MARKET_DOMAIN, stop_event=None):
    """EasyCentral'in "Satistan kaldirilmis" dedigi seyin JP kaynagiyla degil,
    HEDEF pazarda (varsayilan: Amazon.com) bu ASIN'in hic bulunmamasiyla
    ilgili oldugu -- 99 gercek EasyCentral ornegiyle %100 dogrulandi (bkz.
    proje konusma gecmisi) -- kesfedildikten sonra eklendi. JP tarafinda
    her sey saglikli gorunse bile (gecerli Buy Box, guncel veri) bu kontrol
    olmadan EasyCentral'a gonderilen "temiz" ASIN'lerin cogu (%68) daha
    ilk elemede bosa gidiyordu."""
    data = _fetch_product(asin, api_key, domain=target_domain, stop_event=stop_event)
    products = data.get("products") or []
    return bool(products and products[0] is not None and products[0].get("title"))


def keepa_check_detailed_api(
    asin,
    api_key=None,
    require_year=True,
    check_gaps=True,
    check_dead_stock=False,
    check_customs_risk=False,
    check_target_market=False,
    target_market_domain=None,
    domain=API_DOMAIN,
    stop_event=None,
):
    """keepa_check_detailed() ile AYNI sekil sonuc dondurur (asin/status/
    reason/gap_count/gaps_px/dead_stock_suspected) ama Chrome hic acmadan,
    dogrudan Keepa /product API'sinden. Drop-in alternatif."""
    api_key = api_key or _load_keepa_api_key()

    try:
        data = _fetch_product(asin, api_key, domain=domain, stop_event=stop_event)
    except Exception as error:
        raise RuntimeError(f"Keepa API hatasi ({asin}): {type(error).__name__}: {error}") from error

    products = data.get("products") or []
    if not products or products[0] is None:
        return {
            "asin": asin, "status": "delete", "reason": "veri_yok",
            "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": None,
        }

    product = products[0]
    score = compute_priority_score(product)

    if check_customs_risk:
        matched_keyword = check_customs_risk_title(product.get("title"))
        if matched_keyword:
            return {
                "asin": asin, "status": "delete", "reason": f"gumruk_riski({matched_keyword})",
                "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": score,
            }

    csv_data = product.get("csv") or []
    series = csv_data[COUNT_NEW_CSV_INDEX] if len(csv_data) > COUNT_NEW_CSV_INDEX else None
    if not series:
        return {
            "asin": asin, "status": "delete", "reason": "cizgi_bulunamadi",
            "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": score,
        }

    pairs = list(zip(series[0::2], series[1::2]))
    if not pairs:
        # Tek elemanli/tek sayida uzunlukta bir seri (eslesmemis son zaman
        # damgasi) -- diger bozuk-veri yollariyla TUTARLI sekilde zarifce
        # 'cizgi_bulunamadi' donuyoruz, IndexError firlatmiyoruz.
        return {
            "asin": asin, "status": "delete", "reason": "cizgi_bulunamadi",
            "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": score,
        }
    first_ts = pairs[0][0]
    now_minutes = int((_datetime.now(_timezone.utc) - _keepa_epoch()).total_seconds() / 60)
    has_full_year = (now_minutes - first_ts) >= API_WINDOW_DAYS * 1440

    if require_year and not has_full_year:
        return {
            "asin": asin, "status": "delete", "reason": "yillik_secenek_yok",
            "gap_count": None, "gaps_px": [], "dead_stock_suspected": None, "score": score,
        }

    # Son 1 yillik pencere (+ pencereden hemen once gelen, o an gecerli olan
    # son nokta -- pencere basindaki degeri bilmek icin).
    window_start = now_minutes - API_WINDOW_DAYS * 1440
    in_window = [p for p in pairs if p[0] >= window_start]
    before_window = [p for p in pairs if p[0] < window_start]
    if before_window:
        in_window = [before_window[-1]] + in_window

    gaps = [
        (t, v) for i, (t, v) in enumerate(in_window)
        if v == -1 and 0 < i < len(in_window) - 1
    ]
    gap_count = len(gaps) if check_gaps else 0
    gaps_ts = [t for t, _ in gaps]

    last_val = in_window[-1][1] if in_window else None
    dead_stock = bool(check_dead_stock and last_val == -1)

    triggered = []
    if check_gaps and gap_count:
        triggered.append(f"kesinti_var(gap={gap_count})")
    if check_dead_stock and dead_stock:
        triggered.append("telef_suphesi")

    if triggered:
        return {
            "asin": asin, "status": "delete", "reason": "+".join(triggered),
            "gap_count": gap_count, "gaps_px": gaps_ts, "dead_stock_suspected": dead_stock, "score": score,
        }

    # EN SONA konuldu (bilerek): bu ekstra bir Keepa API cagrisi (ekstra
    # token) -- JP tarafinda zaten elenecek bir ASIN icin bu tokeni
    # harcamamak icin sadece BURAYA kadar gelen (diger tum kontrolleri
    # gecmis) adaylarda calistiriyoruz.
    if check_target_market:
        kwargs = {}
        if target_market_domain is not None:
            kwargs["target_domain"] = target_market_domain
        try:
            exists = check_exists_on_target_market(asin, api_key, stop_event=stop_event, **kwargs)
        except Exception as error:
            raise RuntimeError(f"Hedef pazar kontrolu hatasi ({asin}): {type(error).__name__}: {error}") from error
        if not exists:
            return {
                "asin": asin, "status": "delete", "reason": "hedef_pazarda_yok",
                "gap_count": gap_count, "gaps_px": gaps_ts, "dead_stock_suspected": dead_stock, "score": score,
            }

    # ABD (hedef pazar) kontrolunden gecerek upload olduysa reason'a
    # isaretliyoruz -- boylece bu ASIN'in bir daha check_us_existence.py
    # gibi ayri bir script ile TEKRAR kontrol edilmesine gerek kalmiyor
    # (sonuclar.csv'de bu bilgi kalici olarak isaretlenmis oluyor).
    reason = "uygun+abd_dogrulandi" if check_target_market else "uygun"
    return {
        "asin": asin, "status": "upload", "reason": reason,
        "gap_count": gap_count, "gaps_px": gaps_ts, "dead_stock_suspected": dead_stock, "score": score,
    }


def keepa_status(asin, debug_address=None, take_screenshot=True, **check_options):
    result = keepa_check_detailed(
        asin, debug_address=debug_address, take_screenshot=take_screenshot, **check_options
    )
    detail = result["reason"]
    if result["gap_count"]:
        detail += f", kopmalar(px)={result['gaps_px']}"
    print(f"{asin}: {result['status']} ({detail})")
    return result["status"]
