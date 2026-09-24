"""EasyCentral'daki 'inventory-list.csv' envanter raporunu (Envanter ->
Envanter Yönetimi -> Özel Filtre -> Ürünleri İndir) otomatik indirmek icin.

CANLI DOGRULANDI (2026-09-22): Gercek hesapla uctan uca test edildi --
19.627 satir, 59 kolon (KAYNAK-ULKE, ASIN, SKU, ...), elle indirilen
inventory-list.csv ile BIREBIR ayni format. Akis:
  1) /inventory sayfasina git
  2) Ozel Filtre panelini ac (huni ikonu)
  3) 'Aktif urunler' kutusunu isaretle (checkbox GORSEL OLARAK ozel
     stillendirilmis degil, gercek <input type=checkbox> ama Vue v-model'i
     fare tiklamasiyla degil native setter + change/input event dispatch'iyle
     tetikleniyor -- easycentral_target.py'deki AUTO_UPLOAD_TO_STORE_CHECK_EXPR
     ile ayni teknik)
  4) 'Urunleri Filtrele' butonuna tikla (bu, ayni zamanda indirme
     dropdown'unu ETKINLESTIRIR -- checkbox isaretlenmeden/filtrelenmeden
     once dropdown disabled kaliyor, canli dogrulandi)
  5) Dropdown okuna tikla, acilan menuden 'Urunleri Indir'e tikla
  6) Chrome dosyayi kendi VARSAYILAN Indirilenler klasorune kaydediyor
     (Page.setDownloadBehavior ile baska klasore yonlendirme denendi,
     canli dogrulandi ETKISIZ kaldi) -- bu yuzden burada dosyayi
     Indirilenler'de ADI 'inventory-list*.csv' ile baslayanlar arasinda
     EN YENI olani bularak, boyutu stabilize olana kadar bekleyip hedef
     yola KOPYALIYORUZ (tasimiyoruz -- kullanicinin kendi Indirilenler
     klasorundeki dosyaya dokunmamak icin).

ONEMLI -- koordinat tabanli tiklama yerine JS el.click() kullanildi: bu
hesapta sayfa %66.7 zoom'lu acildigi icin getBoundingClientRect (CSS piksel)
ile Input.dispatchMouseEvent'in bekledigi fiziksel piksel uzayi birbirinden
FARKLI cikiyor (canli dogrulandi: visualViewport.clientWidth=1920 vs
cssVisualViewport.clientWidth=2304). Bu, genis butonlarda fark etmese de
kucuk ikonlarda tiklamayi kacirtiyordu. el.click() bu sorunu tamamen
ortadan kaldiriyor (canli dogrulandi, guvenilir calisiyor).

ONEMLI -- native JS alert() diyaloglari AYRI BIR ARKA PLAN BAGLANTISIYLA
otomatik kapatiliyor: 'Urunleri Indir' bazen (canli dogrulandi -- ayni
filtreyle art arda istek yapilinca) anlik indirme yerine "Talebiniz alindi,
listeniz hazirlaniyor. Liste, bir sure sonra mail adresine gonderilecektir."
diyen bir alert() aciyor -- VE BU ALERT TIKLAMANIN HEMEN ARDINDAN DEGIL,
BIRKAC SANIYE SONRA (asenkron sunucu yaniti geldikten sonra) cikabiliyor
(canli dogrulandi/yasandi). JS alert() TUM sayfa JS'ini bloke ettigi icin,
eger o anda hicbir CDP komutu 'bekliyor' durumda degilse (ornegin dosya
indirmesini pollarken), gelen 'Page.javascriptDialogOpening' event'ini
duyacak kimse olmuyor ve sekme SESSIZCE kilitleniyor -- insan mudahalesi
gerekmeden asla acilmiyordu. Cozum: _DialogWatcher, ANA CDP oturumundan
TAMAMEN BAGIMSIZ ikinci bir websocket baglantisi acip fetch_inventory'nin
BASINDAN SONUNA KADAR arka planda surekli dinliyor, hangi an cikarsa
ciksin diyalogu ANINDA kabul ediyor. (Chrome ayni sekmeye birden fazla
DevTools istemcisinin baglanmasina izin veriyor, canli dogrulandi.)

EasyCentral kimlik bilgileri (email/parola) -- Windows Credential Manager'da
(keyring) SIFRELI saklanir (2026-09-24, musteriden geldi: proje klasoru
OneDrive'a senkronize oldugu icin duz metin JSON gercek bir risk --
amazon_sp_api.py ile AYNI goc deseni). Musteri exe'sine gitmez (frozen'da
GUI sekmesi zaten gorunmuyor).
"""

import glob
import json
import os
import sys
import threading
import time
from pathlib import Path

import websocket

try:
    import keyring
except ImportError:
    keyring = None

from keepa_check import open_cdp_session
from easycentral_target import _find_existing_tab

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "easycentral_credentials.json"  # ESKI duz-metin yol -- sadece tek seferlik goc icin
KEYRING_SERVICE = "AsinSeekerEasyCentral_Internal"
KEYRING_ACCOUNT = "easycentral_credentials"
INVENTORY_URL = "https://app.easycentral.com/inventory"

DEFAULT_DOWNLOADS_DIR = Path(os.path.expanduser("~")) / "Downloads"


class _DialogWatcher:
    """Ana CDP oturumundan bagimsiz, kendi websocket baglantisiyla surekli
    dinleyip cikan HER native JS diyalogunu (alert/confirm/prompt) aninda
    kabul eden arka plan izleyici. Ana oturum baska bir seyle (ornegin
    dosya indirmesini pollamak) mesgulken cikan GECIKMELI diyaloglari
    yakalamak icin gerekli -- bkz. dosya basi not."""

    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=5)
        self._next_id = 0
        self.messages = []
        self._stop = False
        self._send("Page.enable")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _send(self, method, params=None):
        self._next_id += 1
        self.ws.send(json.dumps({"id": self._next_id, "method": method, "params": params or {}}))

    def _run(self):
        while not self._stop:
            self.ws.settimeout(1.0)
            try:
                raw = self.ws.recv()
            except Exception:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get("method") == "Page.javascriptDialogOpening":
                self.messages.append((data.get("params") or {}).get("message"))
                try:
                    self._send("Page.handleJavaScriptDialog", {"accept": True})
                except Exception:
                    pass

    def stop(self):
        self._stop = True
        try:
            self.ws.close()
        except Exception:
            pass


def save_credentials(email, password):
    """Windows Credential Manager'a (keyring) SIFRELI yazar."""
    if keyring is None:
        raise RuntimeError("keyring kutuphanesi yok -- kimlik bilgisi guvenli sekilde kaydedilemedi.")
    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, json.dumps({"email": email, "password": password}))


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
    """ESKI easycentral_credentials.json (duz metin) bulunursa keyring'e
    tasir ve dosyayi SILER -- bkz. amazon_sp_api.py'deki AYNI goc mantigi."""
    if not CREDENTIALS_FILE.exists():
        return
    try:
        with CREDENTIALS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("email") and data.get("password"):
            save_credentials(data["email"], data["password"])
        CREDENTIALS_FILE.unlink()
    except Exception:
        pass


def load_credentials():
    email = os.getenv("EASYCENTRAL_EMAIL")
    password = os.getenv("EASYCENTRAL_PASSWORD")
    if email and password:
        return {"email": email, "password": password}

    _migrate_legacy_file_if_needed()

    data = _load_from_keyring()
    if data and data.get("email") and data.get("password"):
        return data

    if CREDENTIALS_FILE.exists():
        with CREDENTIALS_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("email") and data.get("password"):
            return data
    return None


def _login_if_needed(session, credentials):
    if "/login" not in (session.eval_json("location.href") or ""):
        return True
    if not credentials:
        return False

    fill_expr = """
    (function(){
      function setNativeValue(el, value) {
        var proto = Object.getPrototypeOf(el);
        var desc = Object.getOwnPropertyDescriptor(proto, 'value');
        var setter = desc ? desc.set : null;
        if (setter) setter.call(el, value); else el.value = value;
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
      }
      var emailEl = document.querySelector('input[type=email], input[name*=email i], input[id*=email i]');
      var passEl = document.querySelector('input[type=password]');
      if (!emailEl || !passEl) return {found:false};
      setNativeValue(emailEl, %(email)r);
      setNativeValue(passEl, %(password)r);
      return {found:true};
    })()
    """ % {"email": credentials["email"], "password": credentials["password"]}
    if not (session.eval_json(fill_expr) or {}).get("found"):
        return False
    time.sleep(0.3)

    click_login_expr = """
    (function(){
      var btn = Array.from(document.querySelectorAll('button, input[type=submit]')).find(function(b){
        var t = (b.textContent||b.value||'').trim().toLowerCase();
        return t.indexOf('giri') !== -1 || t.indexOf('login') !== -1 || t.indexOf('sign in') !== -1 || b.type==='submit';
      });
      if (!btn) return false;
      btn.click();
      return true;
    })()
    """
    if not session.eval_json(click_login_expr):
        return False
    time.sleep(3)
    return "/login" not in (session.eval_json("location.href") or "")


_INVENTORY_PAGE_READY_EXPR = """
!!document.querySelector('a.btn-preview[data-tooltip="Özel Filtre"]')
"""

_CLICK_FILTER_ICON_EXPR = """
(function(){
  var el = document.querySelector('a.btn-preview[data-tooltip="Özel Filtre"]');
  if (!el) return false;
  el.click();
  return true;
})()
"""

_ACTIVE_LABEL_PRESENT_EXPR = """
(function(){
  var label = Array.from(document.querySelectorAll('label')).find(function(e){
    return (e.textContent||'').trim() === 'Aktif ürünler';
  });
  return !!label;
})()
"""

_CHECK_ACTIVE_EXPR = """
(function(){
  var label = Array.from(document.querySelectorAll('label')).find(function(e){
    return (e.textContent||'').trim() === 'Aktif ürünler';
  });
  if (!label) return {found:false, reason:'no label'};
  var cb = label.parentElement.querySelector('input[type=checkbox]');
  if (!cb) return {found:false, reason:'no checkbox'};
  var proto = Object.getPrototypeOf(cb);
  var desc = Object.getOwnPropertyDescriptor(proto, 'checked');
  var setter = desc ? desc.set : null;
  if (setter) setter.call(cb, true); else cb.checked = true;
  cb.dispatchEvent(new Event('click', {bubbles:true}));
  cb.dispatchEvent(new Event('change', {bubbles:true}));
  cb.dispatchEvent(new Event('input', {bubbles:true}));
  return {found:true, checked: cb.checked};
})()
"""

_CLICK_MAIN_FILTER_BTN_EXPR = """
(function(){
  var group = document.querySelector('.multiple-button.bg-primary');
  if (!group) return false;
  var btn = group.querySelector('.btn');
  if (!btn) return false;
  btn.click();
  return true;
})()
"""

_CLICK_DROPDOWN_CARET_EXPR = """
(function(){
  var group = document.querySelector('.multiple-button.bg-primary');
  if (!group) return false;
  var dropdown = group.querySelector('.other-buttons');
  if (!dropdown) return false;
  dropdown.click();
  return true;
})()
"""

_INDIR_MENU_ITEM_READY_EXPR = """
(function(){
  var group = document.querySelector('.multiple-button.bg-primary');
  if (!group) return false;
  var li = Array.from(group.querySelectorAll('ul li')).find(function(l){
    return (l.textContent||'').trim() === 'Ürünleri İndir';
  });
  return !!(li && li.className.indexOf('disabled') === -1);
})()
"""

_CLICK_INDIR_MENU_ITEM_EXPR = """
(function(){
  var group = document.querySelector('.multiple-button.bg-primary');
  if (!group) return false;
  var li = Array.from(group.querySelectorAll('ul li')).find(function(l){
    return (l.textContent||'').trim() === 'Ürünleri İndir';
  });
  if (!li || li.className.indexOf('disabled') !== -1) return false;
  li.click();
  return true;
})()
"""


def _wait_for(session, expr, timeout=15, interval=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = session.eval_json(expr)
        if result:
            return result
        time.sleep(interval)
    return None


def _existing_inventory_files(downloads_dir):
    return set(glob.glob(str(downloads_dir / "inventory-list*.csv")))


def _wait_for_new_download(downloads_dir, before_files, timeout, dialog_watcher):
    """Yeni dosyayi bekler; bu sirada dialog_watcher.messages'a bir seyler
    duserse (asenkron 'mail'e gonderildi' uyarisi geldiyse) hemen doner --
    dosya zaten gelmeyecektir, beklemenin anlami yok."""
    deadline = time.time() + timeout
    new_file = None
    while time.time() < deadline:
        if dialog_watcher.messages:
            return None
        current = _existing_inventory_files(downloads_dir)
        candidates = current - before_files
        if candidates:
            new_file = max(candidates, key=lambda p: Path(p).stat().st_mtime)
            break
        time.sleep(1)
    if not new_file:
        return None

    # Boyutu stabillesene kadar bekle (buyuk dosya hala indiriliyor olabilir).
    path = Path(new_file)
    last_size = -1
    stable_checks = 0
    while stable_checks < 3 and time.time() < deadline:
        size = path.stat().st_size if path.exists() else -1
        if size == last_size and size > 0:
            stable_checks += 1
        else:
            stable_checks = 0
        last_size = size
        time.sleep(1)
    return path


def fetch_inventory(out_path, debug_address="127.0.0.1:9222", downloads_dir=None, progress=None):
    """Uctan uca: EasyCentral'a giris yapili bir Chrome debug oturumu
    uzerinden /inventory sayfasina gidip, 'Aktif urunler' filtresiyle
    inventory-list.csv'yi indirir ve `out_path`'e KOPYALAR. Diger
    otomasyonlarla (easycentral_target.py, amazon_sp_api.py) AYNI
    sozlesme: dict doner, exception firlatmaz."""
    def report(msg):
        if progress:
            progress(msg)

    downloads_dir = Path(downloads_dir) if downloads_dir else DEFAULT_DOWNLOADS_DIR

    try:
        report("EasyCentral sekmesi aranıyor...")
        tab = _find_existing_tab(debug_address)
        if tab is None:
            return {"ok": False, "message": "Açık bir EasyCentral sekmesi bulunamadı -- önce Chrome'u debug modda açıp EasyCentral'a giriş yap."}

        dialog_watcher = _DialogWatcher(tab["webSocketDebuggerUrl"])
        try:
            tab, session = open_cdp_session(debug_address, tab=tab)
            try:
                session.call("Page.enable")
                session.call("Runtime.enable")

                url = session.eval_json("location.href") or ""
                if "/login" in url:
                    report("EasyCentral girişi yapılıyor...")
                    credentials = load_credentials()
                    if not _login_if_needed(session, credentials):
                        return {"ok": False, "message": "EasyCentral login sayfasında -- otomatik giriş başarısız oldu, elle giriş yapıp tekrar dene."}

                report("Envanter sayfasına gidiliyor...")
                session.call("Page.navigate", {"url": INVENTORY_URL})
                if not _wait_for(session, _INVENTORY_PAGE_READY_EXPR, timeout=20):
                    return {"ok": False, "message": "Envanter sayfası zamanında yüklenmedi (Özel Filtre butonu görünmedi)."}

                report("Özel Filtre paneli açılıyor...")
                session.eval_json(_CLICK_FILTER_ICON_EXPR)
                if not _wait_for(session, _ACTIVE_LABEL_PRESENT_EXPR, timeout=10):
                    return {"ok": False, "message": "Özel Filtre paneli açılmadı (zaman aşımı)."}

                report("'Aktif ürünler' filtresi işaretleniyor...")
                check_result = session.eval_json(_CHECK_ACTIVE_EXPR)
                if not (check_result or {}).get("found"):
                    return {"ok": False, "message": f"'Aktif ürünler' kutusu işaretlenemedi: {check_result}"}

                report("Ürünler filtreleniyor...")
                if not session.eval_json(_CLICK_MAIN_FILTER_BTN_EXPR):
                    return {"ok": False, "message": "'Ürünleri Filtrele' butonu bulunamadı."}
                if not _wait_for(session, _INDIR_MENU_ITEM_READY_EXPR, timeout=10):
                    return {"ok": False, "message": "Filtre uygulandıktan sonra indirme seçeneği etkinleşmedi (zaman aşımı)."}

                report("İndirme menüsü açılıyor...")
                if not session.eval_json(_CLICK_DROPDOWN_CARET_EXPR):
                    return {"ok": False, "message": "İndirme dropdown'u bulunamadı."}
                time.sleep(0.5)

                before = _existing_inventory_files(downloads_dir)
                report("'Ürünleri İndir' tıklanıyor...")
                if not session.eval_json(_CLICK_INDIR_MENU_ITEM_EXPR):
                    return {"ok": False, "message": "'Ürünleri İndir' seçeneği tıklanamadı (devre dışı olabilir)."}

                report("Dosya indiriliyor, bekleniyor (büyük envanterlerde biraz sürebilir)...")
                downloaded = _wait_for_new_download(downloads_dir, before, timeout=180, dialog_watcher=dialog_watcher)

                if dialog_watcher.messages:
                    joined = " | ".join(m for m in dialog_watcher.messages if m)
                    return {
                        "ok": False,
                        "queued_for_email": True,
                        "message": (
                            f"EasyCentral bu isteği anında indirmek yerine kuyruğa aldı: "
                            f"\"{joined}\" -- dosya bir süre sonra hesabın e-postasına gelecek, "
                            f"oradan elle indirip {out_path} konumuna koymak gerekecek."
                        ),
                    }

                if not downloaded:
                    return {
                        "ok": False,
                        "message": f"İndirme tıklandı ama {downloads_dir} içinde yeni bir inventory-list*.csv dosyası görülmedi (zaman aşımı).",
                    }

                out_path_p = Path(out_path)
                out_path_p.parent.mkdir(parents=True, exist_ok=True)
                out_path_p.write_bytes(downloaded.read_bytes())

                return {
                    "ok": True,
                    "message": f"Envanter indirildi: {downloaded.name} -> {out_path_p}",
                    "path": str(out_path_p),
                    "source_download": str(downloaded),
                }
            finally:
                session.close()
        finally:
            dialog_watcher.stop()
    except Exception as e:
        return {"ok": False, "message": f"Beklenmeyen hata: {e}"}
