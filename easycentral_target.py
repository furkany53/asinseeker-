"""EasyCentral'a ASIN listesi gondermek icin CDP tabanli yari-otomasyon.

Ileride baska stok takip platformlari eklenecekse (musteriler EasyCentral
disinda baska programlar da kullanabilir), her platform bu modulle AYNI
kucuk sozlesmeyi izleyen kendi dosyasini alir:

    NAME = "Gorunen isim"
    def open_target(debug_address, stop_event=None): ...        # sayfayi ac
    def paste_asins(debug_address, asins, stop_event=None): ...  # metni yapistir
    def get_status(debug_address): ...                            # (opsiyonel) kota/paket bilgisi

keepa_gui.py'deki "Easy'e Gonder" sekmesi bir TARGETS sozlugunden okur;
yeni bir platform eklemek = yeni bir <platform>_target.py dosyasi yazip
TARGETS'e bir satir eklemek demektir -- keepa_gui.py'nin geri kalanina
dokunmaya gerek kalmaz.

ONEMLI - guvenlik/kota tercihi: Bu modul ASIN'leri sayfadaki arama kutusuna
YAPISTIRIR ama tarama/onay butonuna KENDI BASINA BASMAZ. EasyCentral'in
gunluk "uygun bulma" kotasi sinirli oldugu icin (canli dogrulandi: 25.000
ASIN'lik bir paket ~5 saat suruyor), yanlis/eksik bir listeyi yanlislikla
baslatip kota harcamak cok pahaliya mal olur -- son "Taramayi Baslat"
tikini kullaniciya birakiyoruz, o listeyi gozden gecirip kendi tiklar.
"""

import json
import time
import urllib.request

from keepa_check import open_cdp_session, open_cdp_tab

NAME = "EasyCentral"
EASY_SEARCH_URL = "https://app.easycentral.com/product-search/new"


def _find_existing_tab(debug_address):
    """Zaten acik olan (baska bir cagrinin actigi) EasyCentral sekmesini
    bulur -- her fonksiyon kendi YENI bos sekmesini acarsa, bir onceki
    cagrinin yukledigi gercek sayfa 'kaybolmus' gibi gorunur (canli
    dogrulandi, kullanicidan geldi). ASIN yapistirma gibi ADIMLAR AYNI
    sekmeyi paylasmali."""
    try:
        with urllib.request.urlopen(f"http://{debug_address}/json/list", timeout=5) as response:
            tabs = json.loads(response.read())
    except Exception:
        return None
    for tab in tabs:
        if tab.get("type") == "page" and "easycentral.com" in (tab.get("url") or ""):
            return tab
    return None

# EasyCentral'in tam DOM yapisi (id/class isimleri) daha once CANLI
# CDP ile kesfedilmisti ama o oturumda bir dosyaya kaydedilmemisti. Bu
# yuzden burada -- Keepa giris formunda oldugu gibi sabit bir #id'ye degil
# -- sayfadaki metin/placeholder ipuclarina gore ESNEK bir arama yapiyoruz.
# Bu, tam kesin id'lerden daha az kirilgan ama BIR KERE gercek sayfada
# calisan Chrome ile denenip dogrulanmasi gerekir (bkz. dosya basi not).
FIND_ASIN_BOX_EXPR = """
(function () {
    function visible(el) {
        return !!(el && (el.offsetWidth || el.offsetHeight));
    }
    var candidates = Array.from(document.querySelectorAll('textarea, input[type="text"]'));
    var scored = candidates.filter(visible).map(function (el) {
        var hay = (
            (el.placeholder || '') + ' ' +
            (el.name || '') + ' ' +
            (el.id || '') + ' ' +
            (el.getAttribute('aria-label') || '')
        ).toLowerCase();
        var score = 0;
        if (hay.indexOf('asin') !== -1) score += 3;
        if (el.tagName === 'TEXTAREA') score += 2;
        if (hay.indexOf('search') !== -1 || hay.indexOf('ara') !== -1) score += 1;
        return { el: el, score: score };
    });
    scored.sort(function (a, b) { return b.score - a.score; });
    if (!scored.length || scored[0].score === 0) return { found: false };
    var target = scored[0].el;
    target.setAttribute('data-asinseeker-target', '1');
    return { found: true, tag: target.tagName, id: target.id || null };
})()
"""

PASTE_INTO_MARKED_EXPR = """
(function (text) {
    var el = document.querySelector('[data-asinseeker-target="1"]');
    if (!el) return false;
    var proto = Object.getPrototypeOf(el);
    var descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    var setter = descriptor ? descriptor.set : null;
    if (setter) {
        setter.call(el, text);
    } else {
        el.value = text;
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.scrollIntoView({ block: 'center' });
    return true;
})(%(text)r)
"""

# "1 Mağaza Seçildi" / "N Mağaza Seçildi" (secilmisse) ya da
# "Lütfen Mağazanı Seç" (secilmemisse) -- canli dogrulandi. Hangi
# magazanin secili oldugunu KONTROL ETMIYORUZ (yanlis magazaya gondermek
# cok daha kotu bir hata olur) -- sadece BIR SEYIN secili olup olmadigina
# bakiyoruz. Secili degilse otomasyon DURUR, kullaniciya birakilir.
STORE_SELECTED_EXPR = """
(function () {
    var all = Array.from(document.querySelectorAll('*'));
    var el = all.find(function (e) {
        return e.children.length === 0 && /Mağaza Seçildi/.test((e.textContent || '').trim());
    });
    return !!el;
})()
"""

ASIN_BOX_READY_EXPR = """
document.querySelectorAll('textarea, input[type="text"]').length > 0
"""

FIND_START_BUTTON_AND_SCROLL_EXPR = """
(function () {
    var all = Array.from(document.querySelectorAll('button'));
    var el = all.find(function (e) { return (e.textContent || '').trim().indexOf('Taramayı Başlat') !== -1; });
    if (!el) return null;
    el.scrollIntoView({ block: 'center' });
    var r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
})()
"""

# Tarama formunda, "Taramayı Başlat"ın hemen ustunde iki secenek var:
# "Arama Sonuçlarını Amazon Mağazama Otomatik Yükle" (EasyCentral'in kendi
# ifadesiyle "Onerilmez") ve "...Yükleme Havuzuna Otomatik Yükle". Bunlardan
# ilkini isaretlemek, tarama bitince UYGUN cikan urunleri INSAN MUDAHALESI
# OLMADAN dogrudan magazaya gonderiyor -- yani "mağazaya gönder" adimini,
# bizim disaridan buton tiklayip onay dialogunu atlatmaya calismamiz yerine,
# EasyCentral'in KENDI resmi otomasyon ozelligiyle hallediyoruz. Bunu
# ISARETLEMEK = magazaya otomatik gonderimi ONAYLAMAK demek oldugu icin
# cagiran taraf BILEREK ve ACIKCA istemeli (varsayilan KAPALI).
AUTO_UPLOAD_TO_STORE_CHECK_EXPR = """
(function () {
    // Gercek <input id="auto_upload"> display:none ile GORSEL OLARAK
    // GIZLENMIS (canli dogrulandi: getBoundingClientRect 0x0) -- fare
    // koordinatiyla (label'a bile) tiklamak Vue'nun v-model'ini TETIKLEMEDI
    // (canli dogrulandi: checked hep false kaldi). Cozum: ASIN kutusuna
    // metin yapistirirken kullandigimiz AYNI teknik -- native setter +
    // 'click'/'change'/'input' event'lerini ELLE dispatch etmek. Bu, Vue'nun
    // dinledigi event'leri tetikleyip iceride checked=true olarak isliyor
    // (canli dogrulandi: hem input.checked hem sayfadaki gorsel isaret
    // degisti).
    var input = document.getElementById('auto_upload');
    if (!input) return { found: false };
    var proto = Object.getPrototypeOf(input);
    var descriptor = Object.getOwnPropertyDescriptor(proto, 'checked');
    var setter = descriptor ? descriptor.set : null;
    if (setter) { setter.call(input, true); } else { input.checked = true; }
    input.dispatchEvent(new Event('click', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return { found: true, checked: input.checked };
})()
"""


# Stok Kodu alani (<input id="sku_code">, "JP-" onekinden SONRAKI 5
# haneli kismi) -- her partiye HANGI arama yonteminden (S1-S5, kategori,
# genel tarama) geldigini yansitan bir kod yazmak icin (musteriden geldi,
# 2026-09-17: "yarin satis olan urunlere baktigimda SKU'su bize hangi
# arama yontemiyle geldigini soyler, o yontemi kullaniriz"). Kod, cagiran
# tarafin (keepa_gui.py veya build_asin_kaynak_yontemi.py'nin uretttigi
# asin_kaynak_yontemi.csv) belirledigi bir string -- burada sadece
# sayfaya YAZILIYOR, anlamini disaridaki kod belirliyor.
SKU_CODE_SET_EXPR = """
(function (code) {
    var el = document.getElementById('sku_code');
    if (!el) return { found: false };
    var proto = Object.getPrototypeOf(el);
    var descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    var setter = descriptor ? descriptor.set : null;
    if (setter) { setter.call(el, code); } else { el.value = code; }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { found: true, value: el.value };
})(%(code)r)
"""


def _wait_for(session, expr, timeout=15, interval=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session.eval_json(expr):
            return True
        time.sleep(interval)
    return False


def submit_and_start_scan(debug_address, asins, wait_for_store=10, auto_upload_to_store=False, sku_code=None):
    """Tek cagrida UCTAN UCA gonderim: sekmeyi ac/yeniden kullan -> sayfa
    hazir olana kadar bekle (screenshot degil, DOM durumunu POLLAYARAK --
    cok daha hizli) -> magaza secili mi kontrol et -> ASIN'leri yapistir ->
    'Taramayı Başlat'i GERCEK fare tiklamasiyla (scrollIntoView SONRASI
    hesaplanan koordinatla) tikla -> URL'nin /product-search/history'e
    gectigini dogrula.

    ONEMLI DEGISIKLIK (kullanicidan geldi, bilerek): bu fonksiyon -- eski
    paste_asins'in aksine -- son tiklamayi da OTOMATIK yapar. Kullanici
    surecin tamamini canli izleyip onayladiktan sonra bunu istedi. Yine de
    TEK guvenlik firenimiz kaldi: magaza secili degilse (canli dogrulandi,
    bos birakilirsa 'Lütfen mağaza seçimi yapınız' diye bloklayan bir
    tarayici dialogu cikiyor ve otomasyonu tamamen kilitliyordu) durur,
    kullaniciya birakir -- YANLIS magazaya gondermek, kota harcamaktan
    cok daha pahaliya mal olur.

    auto_upload_to_store=True: formdaki 'Arama Sonuçlarını Amazon Mağazama
    Otomatik Yükle' kutusunu ISARETLER -- boylece tarama bitince UYGUN cikan
    urunler INSAN MUDAHALESI OLMADAN dogrudan magazaya gider (bkz. pipeline'in
    son asamasi). Bu, disaridan bir 'Mağazana Yükle' butonuna tiklayip
    tarayicinin native onay dialogunu programatik olarak atlatmaya calismak
    YERINE, EasyCentral'in KENDI resmi otomasyon ozelligini kullanir --
    hicbir onay dialogunu bypass etmiyoruz, tek onay burada, taramayi
    baslatirken bilerek verilmis oluyor. EasyCentral bu kutuyu 'Onerilmez'
    olarak etiketliyor (insan gozden gecirmesini atladigi icin), o yuzden
    varsayilan KAPALI -- cagiran taraf BILEREK acmali."""
    tab, session = open_cdp_session(debug_address, tab=_find_existing_tab(debug_address))
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Page.navigate", {"url": EASY_SEARCH_URL})

        if not _wait_for(session, ASIN_BOX_READY_EXPR, timeout=15):
            return {"ok": False, "message": "Sayfa zaman asiminda yuklenmedi (ASIN kutusu gorunmedi)."}

        if wait_for_store:
            _wait_for(session, STORE_SELECTED_EXPR, timeout=wait_for_store)
        if not session.eval_json(STORE_SELECTED_EXPR):
            return {
                "ok": False,
                "message": (
                    "Magaza secili degil ('Lütfen Mağazanı Seç' hala goruluyor). "
                    "Otomasyon burada DURDU -- yanlis magazaya gondermemek icin. "
                    "Sayfada magazani sectikten sonra tekrar dene."
                ),
            }

        found = session.eval_json(FIND_ASIN_BOX_EXPR)
        if not found or not found.get("found"):
            return {"ok": False, "message": "ASIN kutusu otomatik bulunamadi."}
        text = "\n".join(asins)
        if not session.eval_json(PASTE_INTO_MARKED_EXPR % {"text": text}):
            return {"ok": False, "message": "Kutu bulundu ama metin yapistirilamadi."}

        sku_set = None
        if sku_code:
            sku_result = session.eval_json(SKU_CODE_SET_EXPR % {"code": sku_code})
            sku_set = bool(sku_result and sku_result.get("found"))

        auto_upload_checked = False
        if auto_upload_to_store:
            cb_result = session.eval_json(AUTO_UPLOAD_TO_STORE_CHECK_EXPR)
            auto_upload_checked = bool(cb_result and cb_result.get("checked"))
            if not auto_upload_checked:
                return {
                    "ok": False,
                    "message": (
                        f"{len(asins)} ASIN yapistirildi ama 'Amazon Mağazama Otomatik Yükle' kutusu "
                        "isaretlenemedi -- tarama BASLATILMADI (yanlislikla insan-onaysiz gonderim yapmamak icin)."
                    ),
                }

        pt = session.eval_json(FIND_START_BUTTON_AND_SCROLL_EXPR)
        if not pt:
            return {"ok": False, "message": f"{len(asins)} ASIN yapistirildi ama 'Taramayı Başlat' butonu bulunamadi."}
        time.sleep(0.3)  # scrollIntoView'in oturmasi icin kisa bir bekleme
        pt = session.eval_json(FIND_START_BUTTON_AND_SCROLL_EXPR)  # kaydirma SONRASI gercek koordinat
        session.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": pt["x"], "y": pt["y"]})
        session.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": pt["x"], "y": pt["y"], "button": "left", "clickCount": 1})
        session.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pt["x"], "y": pt["y"], "button": "left", "clickCount": 1})

        started = _wait_for(session, "location.href.indexOf('product-search/history') !== -1", timeout=10)
        if not started:
            return {
                "ok": False,
                "message": f"{len(asins)} ASIN yapistirildi, butona tiklandi ama sayfa gecisi dogrulanamadi -- elle kontrol et.",
            }
        upload_note = " Magazaya otomatik yukleme ISARETLENDI (uygun cikanlar tarama bitince otomatik gonderilecek)." if auto_upload_checked else ""
        sku_note = ""
        if sku_code:
            sku_note = f" Stok Kodu '{sku_code}' olarak ayarlandi." if sku_set else f" UYARI: Stok Kodu alani bulunamadi, '{sku_code}' yazilamadi."
        return {
            "ok": True,
            "message": f"{len(asins)} ASIN gonderildi ve tarama baslatildi (URL: product-search/history).{upload_note}{sku_note}",
            "auto_upload_to_store": auto_upload_checked,
            "sku_code_set": sku_set,
        }
    finally:
        session.close()


def open_target(debug_address, stop_event=None):
    """EasyCentral'in urun-arama sayfasini gorunur pencerede acar -- zaten
    acik bir EasyCentral sekmesi varsa YENISINI ACMAZ, onu yeniden kullanir
    (birden fazla bos sekme birikmesin diye)."""
    tab, session = open_cdp_session(debug_address, tab=_find_existing_tab(debug_address))
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Page.navigate", {"url": EASY_SEARCH_URL})
        time.sleep(3)
    finally:
        session.close()


def paste_asins(debug_address, asins, stop_event=None, sku_code=None):
    """Aktif (ONCEDEN ACILMIS) EasyCentral sekmesinde ASIN kutusunu bulup
    listeyi yapistirmaya calisir -- open_target'in actigi AYNI sekmeyi
    kullanir, kendi basina yeni bir sekme ACMAZ (canli dogrulandi: her
    cagri kendi sekmesini acarsa, bir onceki cagrinin yukledigi sayfa
    'kaybolmus' gibi gorunuyordu). Basari/basarisizlik bilgisini dondurur;
    SON tiklama (taramayi baslat) kasitli olarak kullaniciya birakilir --
    bkz. dosya basi not.

    sku_code verilirse, 'Stok Kodu' alanina (id=sku_code) da yazilir --
    ASIN'lerin hangi arama yontemiyle bulundugunu (bkz.
    build_asin_kaynak_yontemi.py) SKU uzerinden ileride izleyebilmek icin
    (musteriden geldi, 2026-09-17)."""
    tab = _find_existing_tab(debug_address)
    if tab is None:
        return {
            "ok": False,
            "message": "Acik bir EasyCentral sekmesi bulunamadi -- once 'Chrome'u Ac' adimini calistir.",
        }
    tab, session = open_cdp_session(debug_address, tab=tab)
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        found = session.eval_json(FIND_ASIN_BOX_EXPR)
        if not found or not found.get("found"):
            return {
                "ok": False,
                "message": (
                    "ASIN kutusu sayfada otomatik bulunamadi (EasyCentral sayfa "
                    "yapisi degismis olabilir). Listeyi elle yapistirman gerekecek."
                ),
            }
        text = "\n".join(asins)
        pasted = session.eval_json(PASTE_INTO_MARKED_EXPR % {"text": text})
        if not pasted:
            return {"ok": False, "message": "Kutu bulundu ama metin yapistirilamadi."}

        sku_set = None
        if sku_code:
            sku_result = session.eval_json(SKU_CODE_SET_EXPR % {"code": sku_code})
            sku_set = bool(sku_result and sku_result.get("found"))
        sku_note = ""
        if sku_code:
            sku_note = f" Stok Kodu '{sku_code}' olarak ayarlandi." if sku_set else f" UYARI: Stok Kodu alani bulunamadi, '{sku_code}' yazilamadi."

        return {
            "ok": True,
            "message": (
                f"{len(asins)} ASIN sayfaya yapistirildi.{sku_note} Listeyi gozden gecirip "
                "EasyCentral uzerinde 'Taramayi Baslat' tusuna KENDIN tikla "
                "(kota harcayan adim oldugu icin bilerek otomatiklestirilmedi)."
            ),
            "sku_code_set": sku_set,
        }
    finally:
        session.close()


TARGETS = {
    NAME: {
        "open_target": open_target,
        "paste_asins": paste_asins,
        "submit_and_start_scan": submit_and_start_scan,
    },
}
