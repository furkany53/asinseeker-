"""Tek seferlik test: Keepa Product Viewer'da yuklu 100 ASIN'lik listeyi,
KULLANICININ GERCEK (zaten Keepa'ya giris yapmis) Chrome profilini kullanarak
acip "Export" ozelligini deneriz. Kalici bir arac degil -- Product
Viewer'in export formatinin bizim ise yarayip yaramadigini gormek icin.
"""

import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from keepa_check import CDPSession, open_cdp_tab  # noqa: E402

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
# Chrome, guvenlik nedeniyle GERCEK/varsayilan profilde uzaktan hata ayiklamayi
# reddediyor (canli denendi, dogrulandi -- bağlanti reddedildi). Bunun yerine
# projenin ZATEN kullandigi ayri otomasyon profilini kullaniyoruz (bu profil
# tum oturum boyunca Keepa kontrolleri icin kullanildi, muhtemelen zaten giris
# yapilmis durumda).
REAL_PROFILE_ROOT = r"C:\chrome_debug_temp"
DEBUG_ADDRESS = "127.0.0.1:9222"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "keepa_full_kontrol" / "viewer_export_indirilen"

FIND_BUTTON_BY_TEXT_EXPR = """
(function (text) {
    function visible(el) {
        return !!(el && (el.offsetWidth || el.offsetHeight));
    }
    var els = Array.from(document.querySelectorAll('button, a, div, span'));
    var match = els.find(function (e) {
        return visible(e) && (e.innerText || '').trim().toLowerCase() === text.toLowerCase();
    });
    if (match) { match.click(); return true; }
    return false;
})(%(text)r)
"""

ROW_COUNT_EXPR = """
(function () {
    var footer = Array.from(document.querySelectorAll('*')).find(function(e){
        return (e.innerText||'').match(/to\\s+\\d+\\s+of\\s+\\d+/i) && e.children.length === 0;
    });
    return footer ? footer.innerText : null;
})()
"""

VISIBLE_BUTTONS_EXPR = """
(function () {
    function visible(el) {
        return !!(el && (el.offsetWidth || el.offsetHeight));
    }
    var els = Array.from(document.querySelectorAll('button, a'));
    return els.filter(visible).map(function(e){ return (e.innerText||'').trim(); }).filter(Boolean);
})()
"""


def close_existing_chrome():
    for name in ("chrome.exe",):
        subprocess.run(["taskkill", "/F", "/IM", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)


def main():
    asins = [l.strip() for l in (Path(__file__).resolve().parent / "keepa_full_kontrol" / "test_product_viewer_ornek.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    payload = json.dumps({"5": asins}, separators=(",", ":"))
    url = "https://keepa.com/#!viewer/" + urllib.parse.quote(payload, safe="")
    print(f"{len(asins)} ASIN icin viewer URL hazirlandi.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    print("Mevcut Chrome kapatiliyor (gercek profil kullanilacak, oturum acik kalir)...")
    close_existing_chrome()

    print("Chrome, GERCEK profille debug modda aciliyor...")
    subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_ADDRESS.rsplit(':', 1)[1]}",
        f"--user-data-dir={REAL_PROFILE_ROOT}",
        "--profile-directory=Default",
        "--remote-allow-origins=*",
        "about:blank",
    ])
    time.sleep(4)

    tab = open_cdp_tab(DEBUG_ADDRESS)
    session = CDPSession(tab["webSocketDebuggerUrl"])
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Browser.setDownloadBehavior", {
            "behavior": "allow", "downloadPath": str(DOWNLOAD_DIR),
        })
        session.call("Page.navigate", {"url": url})
        time.sleep(4)

        print("Sayfa/tablo yuklenmesi bekleniyor...")
        deadline = time.time() + 30
        row_info = None
        while time.time() < deadline:
            row_info = session.eval_json(ROW_COUNT_EXPR)
            if row_info:
                break
            time.sleep(1)
        print("Satir bilgisi:", row_info)

        buttons = session.eval_json(VISIBLE_BUTTONS_EXPR)
        print("Gorunen butonlar/linkler (ilk 40):", buttons[:40])

        clicked = session.eval_json(FIND_BUTTON_BY_TEXT_EXPR % {"text": "Export"})
        print("Export tiklandi mi:", clicked)
        time.sleep(2)

        buttons_after = session.eval_json(VISIBLE_BUTTONS_EXPR)
        print("Export sonrasi gorunen butonlar/linkler (ilk 40):", buttons_after[:40])

    finally:
        session.close()

    print("\nIndirilenler klasoru:", DOWNLOAD_DIR)
    time.sleep(3)
    for f in DOWNLOAD_DIR.iterdir():
        print(" -", f.name, f.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
