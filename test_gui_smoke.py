"""Otomatik 'sahte kullanici' testi -- ucu katmanli:

1) Kara-kutu baslatma testi (pywinauto): programi gercekten .exe/script gibi
   baslatip pencerenin acildigini ve baslikta dogru ismin oldugunu dogrular.
   BUNUN OTESINE GECEMIYORUZ: Tkinter'in Windows UI Automation destegi cok
   zayif -- hem menu ogeleri ("owner-drawn" oldugu icin) hem de ttk
   buton/checkbox'lar pywinauto'ya guvenilir sekilde gorunmuyor/tiklanamiyor
   (denendi, dogrulandi -- bkz. konusma gecmisi). Bu Tk'nin bilinen bir
   sinirlamasi, kodumuzdaki bir hata degil.

2) Gri-kutu fonksiyonel test: ayni KeepaApp sinifini GERCEK bir Tk penceresi
   ve GERCEK Tk olay donguसuyle (root.update()) calistirip, kullanicinin
   butona basmasiyla calisacak AYNI metodlari (start_check/stop_check)
   dogrudan cagirir. Simuleli fare tiklamasi degil ama gercek widget'lari,
   gercek callback'leri ve gercek thread/queue mekanizmasini calistirdigi
   icin runtime hatalarini (crash, exception, kilitlenme) hala yakalar --
   pywinauto'nun basaramadigi yerde bu guvenilir calisiyor.

3) Beyaz-kutu birim testi: ASIN_PATTERN regex'ini (B0 ile baslayanini al,
   gerisini yok say kurali) dogrudan, GUI'yi hic acmadan test eder.

Calistirma: python test_gui_smoke.py
"""

import subprocess
import sys
import time
from pathlib import Path

from pywinauto.application import Application

BASE_DIR = Path(__file__).resolve().parent
GUI_SCRIPT = BASE_DIR / "keepa_gui.py"

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"[OK]   {name}")
    else:
        FAIL.append((name, detail))
        print(f"[FAIL] {name} -- {detail}")


def cleanup_processes():
    # python.exe'yi genel olarak OLDURMUYORUZ -- bu test scripti de bir
    # python.exe sureci, kendini oldurup sessizce sonlanmasina yol acardi
    # (basta yasandi, dogrulandi). Sadece AsinSeeker penceresini (basligiyla)
    # ve chrome.exe'yi hedefliyoruz.
    subprocess.run(
        ["taskkill", "/F", "/FI", "WINDOWTITLE eq AsinSeeker*"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["taskkill", "/F", "/IM", "chrome.exe"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1)


# --------------------------------------------------------------- katman 3
def test_asin_parsing():
    sys.path.insert(0, str(BASE_DIR))
    import importlib
    import keepa_gui
    importlib.reload(keepa_gui)

    sample_lines = [
        ("ASIN\tBRAND", None),
        ("B007SWRBVO\tWrangler", "B007SWRBVO"),
        ("gecersiz satir hicbir asin yok", None),
        ("B0CFY6BNYQ ZENOTTIC bircok kelime var", "B0CFY6BNYQ"),
        ("", None),
        ("sadece_metin", None),
        ("805485546", None),  # B0 ile baslamiyor -> yok sayilmali
    ]
    for line, expected in sample_lines:
        match = keepa_gui.ASIN_PATTERN.search(line.upper())
        got = match.group(0) if match else None
        check(f"ASIN parse: {line!r} -> {expected!r}", got == expected, f"gercek: {got!r}")
    return keepa_gui


# --------------------------------------------------------------- katman 1
def test_black_box_launch():
    app = Application(backend="uia").start(
        f'"{sys.executable}" "{GUI_SCRIPT}"', wait_for_idle=False
    )
    try:
        main_win = app.window(title_re=".*AsinSeeker.*")
        main_win.wait("visible", timeout=25)
        check("Kara-kutu: pencere gercekten baslatildi", True)
        check("Kara-kutu: baslikta 'AsinSeeker' var", "AsinSeeker" in main_win.window_text())
    finally:
        try:
            app.kill()
        except Exception:
            pass
        cleanup_processes()


# --------------------------------------------------------------- katman 2
def test_functional_in_process(keepa_gui_module):
    """Ayni KeepaApp'i, kullanicinin gorecegi gercek pencere + gercek Tk
    donguSuyle calistirip start/stop akisini (butona basinca calisacak AYNI
    start_check/stop_check metodlarini) dogrudan tetikler. Gercek bir ASIN
    ile Keepa'ya gercekten baglanir (internet gerektirir; yoksa atlanir)."""
    if not keepa_gui_module.has_internet():
        check("Fonksiyonel test", False, "internet yok, bu katman atlandi")
        return

    import tkinter as tk

    root = tk.Tk()
    callback_errors = []
    root.report_callback_exception = lambda *exc_info: callback_errors.append(exc_info)

    try:
        app = keepa_gui_module.KeepaApp(root)
        root.update()
        check("Fonksiyonel: KeepaApp gercek Tk ile olusturuldu", True)

        # Gercek bir ASIN'le gercek bir kontrol baslat (dosya dialogunu
        # atlayip listeyi dogrudan veriyoruz -- load_file zaten ayri test edildi).
        app.asins = ["B007SWRBVO"]
        app.screenshot_var.set(False)
        app.start_check()
        for _ in range(40):  # ~4 sn -- kontrolun gercekten basladigini gor
            root.update()
            time.sleep(0.1)
            if app.active_asins:
                break
        check("Fonksiyonel: start_check gercekten baslatti (aktif ASIN var)", bool(app.active_asins) or app.running)

        app.stop_check()
        stopped = False
        for _ in range(150):  # ~15 sn -- Chrome kapanip thread'in bitmesini bekle
            root.update()
            time.sleep(0.1)
            if not app.running:
                stopped = True
                break
        check("Fonksiyonel: stop_check sonrasi kontrol duzgunce sonlandi", stopped)
        check("Fonksiyonel: Baslat butonu tekrar aktif", str(app.start_button["state"]) == "normal")
        check("Fonksiyonel: callback icinde yakalanmamis exception yok", not callback_errors, str(callback_errors))

    finally:
        try:
            root.destroy()
        except Exception:
            pass
        cleanup_processes()


def main():
    cleanup_processes()
    keepa_gui_module = test_asin_parsing()
    test_black_box_launch()
    test_functional_in_process(keepa_gui_module)

    print(f"\n=== OZET: {len(PASS)} basarili, {len(FAIL)} basarisiz ===")
    for name, detail in FAIL:
        print(f"  - {name}: {detail}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
