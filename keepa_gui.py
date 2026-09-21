import importlib
import subprocess
import sys

# --- Ilk calistirmada eksik kutuphaneleri kendiliginden kur ------------------
# Bu betik baska bir bilgisayarda ilk kez calistirildiginda selenium /
# websocket-client / keyring kurulu olmayabilir. Proje modullerini import
# etmeden once kontrol edip eksikse (kucuk bir uyari penceresi gostererek)
# pip ile kuruyoruz.
REQUIRED_PACKAGES = {
    "selenium": "selenium",
    "websocket": "websocket-client",
    "keyring": "keyring",
}


def _ensure_dependencies():
    missing = []
    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return

    import tkinter as tk

    splash = tk.Tk()
    splash.title("Kurulum")
    splash.geometry("440x130")
    splash.resizable(False, False)
    tk.Label(
        splash,
        text=(
            "Ilk calistirma: gerekli Python kutuphaneleri kuruluyor...\n\n"
            f"({', '.join(missing)})\n\nLutfen bekleyin, bu pencere kendiliginden kapanacak."
        ),
        padx=20,
        pady=20,
        justify="left",
    ).pack(expand=True, fill="both")
    splash.update()

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", *missing],
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except subprocess.CalledProcessError as error:
        splash.destroy()
        raise RuntimeError(
            f"Gerekli kutuphaneler kurulamadi ({', '.join(missing)}): {error}\n"
            f"Elle kurmak icin: {sys.executable} -m pip install {' '.join(missing)}"
        ) from error

    splash.destroy()


_ensure_dependencies()

import csv  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import tkinter as tk  # noqa: E402
import traceback  # noqa: E402
import winsound  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402
from pathlib import Path  # noqa: E402
from tkinter import filedialog, messagebox, scrolledtext, ttk  # noqa: E402

# PyInstaller onefile ile paketlenince __file__ GERCEK exe konumunu degil,
# her calistirmada silinen GECICI bir cikarma klasorunu gosterir --
# varsayilan "Kayit Konumu" boylece hicbir zaman kalici olmayan bir yere
# yazardi (denendi, dogrulandi). Frozen halde sys.executable'in bulundugu
# GERCEK klasoru kullaniyoruz.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import ctypes  # noqa: E402

import harvest_seller_asins as hs  # noqa: E402
import keepa_api_settings  # noqa: E402
import keepa_credentials  # noqa: E402
import keepa_finder as kf  # noqa: E402
import license_guard  # noqa: E402
import scan_stats  # noqa: E402
from easycentral_target import TARGETS as UPLOAD_TARGETS  # noqa: E402

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def prevent_sleep():
    # Uzun (saatlerce surebilen) taramalar sirasinda Windows uykuya gecip
    # aglantiyi/Chrome'u kesmesin diye -- gece boyu 9000 ASIN'lik bir tarama
    # uykuya dalinca yarim kalip sonuclarin kaybolmasina yol acabiliyordu.
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass


def allow_sleep():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass
from easycentral_keepa_bot import (  # noqa: E402
    DEBUG_ADDRESS,
    close_existing_chrome_processes,
    start_chrome_for_attachment,
)
from keepa_check import keepa_check_detailed, keepa_check_detailed_api  # noqa: E402
from keepa_login import attempt_keepa_login  # noqa: E402

SCREENSHOT_DIR = BASE_DIR / "keepa_screenshots"
RESULTS_CSV_NAME = "keepa_recheck_sonuclari.csv"
UPLOAD_TXT_NAME = "yukleme_listesi.txt"
ASIN_PATTERN = re.compile(r"B0[A-Z0-9]{8}")
MAX_RETRIES = 2  # gecici (ag/Keepa) hatalarinda ASIN basina fazladan deneme sayisi
DEFINITIVE_STATUSES = {"upload", "delete"}  # "hata"/"durduruldu" kesin sonuc sayilmaz, tekrar denenmeli

# Bu liste 2026-09-16'da CANLI dogrulandi (Keepa'nin /product ucuna her
# domain ID icin gercek bir cagri yapilip 1-6 ve 8-14'un gecerli, 7/15/16'nin
# gecersiz oldugu teyit edildi -- proje kurali: Keepa parametresini
# dogrulamadan koda yazma). 13/14'un TAM ulke adi Keepa'nin API yanitindan
# okunamiyor (sadece ID gecerli/gecersiz diye donuyor); bu ikisi icin ad,
# bilinen/genel yayinlanmis Keepa tablosundan alindi -- SUPHELIYSE
# musterinin kendi ulkesi icin ID'yi Keepa sitesinden (Product Finder
# ekrani, ulke secici) teyit etmesi onerilir. Hem KAYNAK (nereden ASIN
# aranip okunacak) hem HEDEF (dropshipping'in siparisi nereden karsilayacagi)
# pazar secimlerinde AYNI liste kullanilir.
KEEPA_MARKET_DOMAINS = [
    ("Amazon.com (ABD)", 1),
    ("Amazon.co.uk (İngiltere)", 2),
    ("Amazon.de (Almanya)", 3),
    ("Amazon.fr (Fransa)", 4),
    ("Amazon.co.jp (Japonya)", 5),
    ("Amazon.ca (Kanada)", 6),
    ("Amazon.it (İtalya)", 8),
    ("Amazon.es (İspanya)", 9),
    ("Amazon.in (Hindistan)", 10),
    ("Amazon.com.mx (Meksika)", 11),
    ("Amazon.com.br (Brezilya)", 12),
    ("Amazon.com.au (Avustralya)", 13),
    ("Amazon.nl (Hollanda)", 14),
]
# Bu pazarlar icin S1-S5 hazir stratejiler VE kategori-plani girdileri
# JPY (yen) fiyat araliklariyla ayarlanmis -- baska bir kaynak pazarda
# kullanilirsa anlamsiz sonuc verir. Sadece Japonya (5) seciliyken
# gosterilir; digerlerinde musteri manuel filtreleri KENDI para biriminde
# ayarlamak zorunda (zaten duzenlenebilir alanlar).
JPY_TUNED_SOURCE_DOMAIN = 5

# Keepa domain ID -> o pazarin kendi para birimi. Fiyat filtreleri
# (LISTPRICE_MIN/MAX) HER ZAMAN secili kaynak pazarin kendi para biriminde
# girilir (Keepa boyle bekliyor) -- musteriden geldi: kutuya "500" yazinca
# hangi para biriminde oldugu belli degildi, kafa karistiriyordu. Simdi
# "Fiyat min/max" etiketleri secili pazara gore dinamik guncelleniyor
# (bkz. _update_price_currency_labels).
KEEPA_DOMAIN_CURRENCY = {
    1: "USD", 2: "GBP", 3: "EUR", 4: "EUR", 5: "JPY", 6: "CAD",
    8: "EUR", 9: "EUR", 10: "INR", 11: "MXN", 12: "BRL", 13: "AUD", 14: "EUR",
}

# 1 USD'nin YAKLASIK karsiligi (sadece "30-500 dolar araligi" gibi anlasilir
# bir varsayilan Fiyat min/max bandi secili pazarin kendi para birimine
# cevrilsin diye -- musteriden geldi. Kesin/canli doviz kuru DEGIL, gunluk
# dalgalanir; sadece mantikli bir baslangic degeri sunmak icin.
KEEPA_DOMAIN_USD_RATE = {
    1: 1, 2: 0.79, 3: 0.92, 4: 0.92, 5: 150, 6: 1.37,
    8: 0.92, 9: 0.92, 10: 83, 11: 18, 12: 5.4, 13: 1.52, 14: 0.92,
}
DEFAULT_PRICE_MIN_USD = 30
DEFAULT_PRICE_MAX_USD = 500


def _round_nice(value):
    """Doviz cevriminden cikan sayiyi okunakli/yuvarlak bir degere ceker."""
    if value >= 1000:
        return int(round(value / 50.0)) * 50
    if value >= 100:
        return int(round(value / 10.0)) * 10
    return int(round(value))


def _default_price_for_domain(domain, usd_amount):
    rate = KEEPA_DOMAIN_USD_RATE.get(domain, 1)
    return _round_nice(usd_amount * rate)


def find_previous_results(output_dir):
    """Devam edilebilirlik (resume) icin: bu klasordeki gecmis
    'tarama_*.csv' oturum dosyalarini TARIH SIRASINA gore tarayip her ASIN
    icin en SON kesin (upload/delete) sonucu dondurur. 'hata'/'durduruldu'
    olanlar kesin sayilmaz, tekrar taranmalari icin listede birakilir."""
    previous = {}
    if not output_dir.exists():
        return previous
    for csv_path in sorted(output_dir.glob("tarama_*.csv")):
        try:
            with csv_path.open("r", newline="", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    if row.get("status") in DEFINITIVE_STATUSES:
                        previous[row["asin"]] = row
        except Exception:
            continue
    return previous


def has_internet(timeout=3):
    for host, port in (("keepa.com", 443), ("8.8.8.8", 53)):
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False

HELP_TEXT = """NE İŞE YARAR?

Bu program, EasyCentral ürün kuyruğundaki ASIN'leri Keepa üzerinden tek tek
kontrol eder ve hangi ürünlerin mağazaya yüklenmeye (Amazon'a gönderilmeye)
uygun, hangilerinin havuzdan silinmesi gerektiğine karar verir.

NASIL ÇALIŞIR?
1) Yüklediğin .txt listesindeki her ASIN için Keepa'nın ürün sayfası açılır.
2) Aşağıdaki üç kontrolden hangileri işaretliyse onlar uygulanır. Bu inceleme
   ekran görüntüsüne bakarak değil, Keepa'nın grafiği ÇİZERKEN kullandığı
   komutları doğrudan yakalayarak yapılır -- yani zoom, ekran çözünürlüğü gibi
   şeylerden etkilenmez, kesin bir sonuç verir.
3) Hiçbir kontrol "silinmeli" demiyorsa ürün "upload" (temiz) sayılır ve
   otomatik olarak "yukleme_listesi.txt" dosyasına yazılır -- bu dosya
   EasyCentral'a aktarılıp oradan Amazon'a gönderilir.

KONTROL SEÇENEKLERİ:

• 1 Yıllık Veri Şartı: Keepa'da ürünün en az 1 yıllık takip geçmişi
  (1Y aralığı) yoksa ürün doğrudan silinir. Kapatılırsa, 1 yıllık geçmişi
  olmayan ürünler de -- mevcut aralıklarıyla -- değerlendirmeye alınır.

• Kesinti Kontrolü: Keepa'nın "New Offer Count" (satıcı sayısı) grafiğinde,
  geçmişte bir noktada verinin kesilip sonra tekrar başladığı (yani grafikte
  ortada bir kopukluk olduğu) durumları yakalar. Böyle bir kopukluk varsa
  ürün silinir.

• Telef Şüphesi Kontrolü (satıcı düşüşü): Bu, kesinti kontrolünden FARKLI
  bir sorunu yakalamak için var. Bazı ürünlerde satıcı sayısı grafiği geçmişte
  düzgün gitmiş olabilir ama GÜNÜMÜZE YAKLAŞTIKÇA çizgi tamamen kesilip bir
  daha hiç başlamaz -- yani ürünü satan hiçbir satıcı kalmamıştır. Bu durum
  normal "kesinti"den farklıdır çünkü grafik ortasında değil, SONUNDA biter;
  yani ürün büyük ihtimalle artık satışta/stokta değildir (satıcılar tamamen
  çekilmiştir). Bu kontrol açıksa böyle ürünler "silinmeli" sayılır. Bu daha
  yeni ve deneysel bir kontrol olduğu için önce birkaç sonucu gözle
  doğrulaman önerilir.

ÖNEMLİ NOT -- KEEPA HESABI:
Keepa'nın 1 yıllık grafik geçmişini tam olarak görebilmek genellikle Keepa'nın
ÜCRETLİ (abonelikli) hesabını gerektirir. Program bir Keepa hesabına GİRİŞ
YAPMADAN çalışırsa, bazı ürünlerde veri eksik/yanlış görünebilir ve kontrol
güvenilir olmaz. Bu yüzden Ayarlar > Keepa Hesabı bölümünden hesabına giriş
yapman önerilir -- bu işlem sadece ilk kurulumda (ya da oturum düşerse)
gerekir; bir kere giriş yaptıktan sonra oturum bilgisayarında saklanır.
"""

PERSONAL_FILTER_HELP_TEXT = """KENDİ KEEPA FİLTRENİ NASIL ALIRSIN?

Yukarıdaki hazır filtre alanları (fiyat, offer count, vb.) sana YETMİYORSA
ve Keepa'nın kendi web sitesindeki "Product Finder" ekranında zaten daha
detaylı/kişisel bir arama kurduysan, o aramayı burada AYNEN kullanabilirsin.

ADIM ADIM:
1) keepa.com adresine git, hesabına giriş yap.
2) Üst menüden "Product Finder" (Ürün Bulucu) ekranını aç.
3) İstediğin TÜM filtreleri (kategori, marka, fiyat, satış hızı, vb.)
   normal şekilde, sürükle-bırak/seçim kutularıyla kur -- aramayı henüz
   çalıştırmana gerek yok.
4) Finder ekranının üstünde/yanında genelde bir "</>" ya da "API" simgesi ya
   da "Sorguyu Göster / Export Query" gibi bir bağlantı bulunur -- bu, o an
   ekranda kurduğun filtrenin TAM API karşılığını (bir JSON metni) gösterir.
   Bu ikonu tıkla.
5) Açılan JSON metninin TAMAMINI kopyala (büyük süslü parantez { ile başlar,
   } ile biter).
6) Buraya, aşağıdaki kutuya YAPIŞTIR ve "JSON'u Doğrula" tuşuna bas -- geçerli
   ise yeşil bir onay mesajı görürsün.
7) Üstteki kutucuğu işaretleyip "Başlat"a bas -- program artık YUKARIDAKİ
   hazır alanları değil, SENİN kurduğun bu filtreyi kullanarak arayacak.
   (Sales Rank aralığı/dilim ayarı, taramanın teknik olarak çalışabilmesi
   için HER ZAMAN bizim tarafımızdan eklenir -- bunun dışındaki her şey
   senin JSON'unda ne yazıyorsa odur.)

NOT: Keepa zaman zaman site tasarımını güncelleyebilir, bu yüzden ilgili
ikonun tam adı/yeri değişmiş olabilir -- "Query", "API", "Export" gibi
kelimeleri arayan bir düğme/simge ara. Bulamazsan bize bir ekran görüntüsü
gönder, birlikte bakalım.
"""


class HelpTooltip:
    """Bir '?' etiketinin uzerine gelince aciklama balonu gosterir --
    Tkinter'da hazir bir tooltip widget'i olmadigi icin kucuk bir Toplevel
    ile kendimiz yapiyoruz. ONEMLI: balon pencerenin/ekranin disina TASMASIN
    diye konum, ekran genisligine/yuksekligine gore SIKISTIRILIYOR
    (kullanicidan geldi -- "None" yazan alanlar kafa karistirdigi icin
    eklenen aciklama balonlarinin kendisi de tasip yeni bir kafa karisikligi
    yaratmasin diye)."""

    def __init__(self, parent, text, wraplength=320):
        self.text = text
        self.wraplength = wraplength
        self.tip_window = None
        label = ttk.Label(
            parent, text=" ? ", foreground="#fff", background="#5a7fb5",
            font=("Segoe UI", 7, "bold"), cursor="question_arrow",
        )
        label.bind("<Enter>", self._show)
        label.bind("<Leave>", self._hide)
        self.widget = label

    def _show(self, _event=None):
        if self.tip_window is not None:
            return
        widget = self.widget
        x = widget.winfo_rootx() + 12
        y = widget.winfo_rooty() + widget.winfo_height() + 4

        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        frame = ttk.Frame(tw, relief="solid", borderwidth=1)
        frame.pack()
        ttk.Label(
            frame, text=self.text, background="#ffffe0", foreground="#000",
            wraplength=self.wraplength, justify="left", padding=(6, 4),
        ).pack()
        tw.update_idletasks()

        # Ekranin (pencerenin degil, TUM ekranin -- balon pencere disina da
        # tasabilir cunku Toplevel) sagindan/altindan tasarsa konumu
        # ICERI dogru kaydir.
        screen_w = widget.winfo_screenwidth()
        screen_h = widget.winfo_screenheight()
        tip_w = tw.winfo_reqwidth()
        tip_h = tw.winfo_reqheight()
        if x + tip_w > screen_w:
            x = max(0, screen_w - tip_w - 8)
        if y + tip_h > screen_h:
            y = max(0, widget.winfo_rooty() - tip_h - 4)
        tw.wm_geometry(f"+{x}+{y}")
        self.tip_window = tw

    def _hide(self, _event=None):
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


class KeepaApp:
    def __init__(self, root):
        self.root = root
        root.title(f"{license_guard.PROGRAM_NAME} v{license_guard.VERSION} — Lisanslı Yazılım")
        self.asins = []
        self.event_queue = queue.Queue()
        self.running = False
        self.active_asins = set()
        self.output_dir = BASE_DIR
        self._apply_style()
        self._build_menu()
        self._build_source_market_bar()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

        finder_tab = ttk.Frame(self.notebook)
        check_tab = ttk.Frame(self.notebook)
        send_tab = ttk.Frame(self.notebook)
        rakip_tab = ttk.Frame(self.notebook)
        self.notebook.add(finder_tab, text="ASIN Bul")
        self.notebook.add(check_tab, text="Temizle")
        self.notebook.add(send_tab, text="Easy'e Gönder")
        self.notebook.add(rakip_tab, text="Rakip Kopyala")

        self._build_finder_tab(finder_tab)
        self._build_ui(check_tab)
        self._build_send_tab(send_tab)
        self._build_rakip_tab(rakip_tab)
        self._refresh_strategy_options()

        # Sekmeler farkli genislikte icerik tasiyabiliyor (orn. Temizle
        # sekmesine sonradan eklenen JP gumruk riski onay kutusu satiri
        # genisletti). Eskiden pencere HER sekme degisiminde "dogal" boyuta
        # sifirlaniyordu -- bu da sekmeler arasi gecince pencerenin gozle
        # gorulur sekilde kuculup buyumesine ("zipleme") yol aciyordu
        # (kullanicidan geldi, dogrulandi). Bunun yerine acilista TUM
        # sekmeleri tek tek gezip en genis olanina gore pencereyi BIR KEZ
        # boyutlandiriyoruz; sekme degistirmek artik pencere boyutunu
        # etkilemiyor.
        def _fit_to_widest_tab():
            self.root.update_idletasks()
            current = self.notebook.index("current")
            max_w = max_h = 0
            for i in range(len(self.notebook.tabs())):
                self.notebook.select(i)
                self.root.update_idletasks()
                max_w = max(max_w, self.root.winfo_reqwidth())
                max_h = max(max_h, self.root.winfo_reqheight())
            self.notebook.select(current)

            # Ekranin TAMAMINI kaplamasin diye (kullanicidan geldi, dogrulandi
            # -- pencere ekrani kapliyordu) gorev cubugu/baslik cubugu icin
            # pay birakarak ekran boyutuna gore ustten SINIRLIYORUZ. Sekme
            # icerikleri zaten kaydirilabilir (ScrolledText) oldugu icin bu
            # sinirlama veri kaybina yol acmaz.
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            w = min(max_w, screen_w - 80)
            h = min(max_h, screen_h - 120)
            x = max(0, (screen_w - w) // 2)
            y = max(0, (screen_h - h) // 3)
            self.root.geometry(f"{w}x{h}+{x}+{y}")

        self.root.after(50, _fit_to_widest_tab)

        # Pencere boyutu acilista BIR KEZ hesaplanip sabitleniyor (yukarida) --
        # kullanicidan geldi, dogrulandi: fare ile kenardan cekip kucultmek
        # ic layout'u (grid genislikleri, ScrolledText'ler) bozuyordu. Bu
        # yuzden serbest yeniden boyutlandirma KAPALI.
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_queue)

    # ---------------------------------------------------------------- stil
    def _apply_style(self):
        # Varsayilan Windows temasinda (vista/winnative) hem butonlar hem de
        # Notebook sekmeleri neredeyse duz/renksiz cikiyor -- kullanicidan
        # "butonlar belli olsun, sekme orada oldugu belli olmuyor" geri
        # bildirimi geldi. "clam" temasi bevel renklerini (lightcolor/
        # darkcolor/bordercolor) elle kontrol etmemize izin veriyor, bu
        # yuzden butonlara kabartma (raised bevel) ve sekmelere secili/
        # secili-olmayan arasinda net bir renk farki veriyoruz.
        style = ttk.Style(self.root)
        style.theme_use("clam")

        # Duz beyaz zemin yerine yumusak gri bir arka plan -- butonlarin ve
        # sekmelerin gri tonlariyla uyumlu, gozu yormayan bir bütünlük veriyor.
        BG = "#dcdcdc"
        self.root.configure(background=BG)
        style.configure("TFrame", background=BG)
        style.configure("TLabelframe", background=BG, bordercolor="#9a9a9a")
        style.configure("TLabelframe.Label", background=BG)
        style.configure("TLabel", background=BG)
        style.configure("TCheckbutton", background=BG)
        style.configure("TRadiobutton", background=BG)

        style.configure(
            "TButton",
            padding=(12, 7),
            relief="raised",
            borderwidth=2,
            background="#e8e8e8",
            lightcolor="#ffffff",
            darkcolor="#9a9a9a",
            bordercolor="#7a7a7a",
        )
        style.map(
            "TButton",
            background=[("disabled", "#e8e8e8"), ("pressed", "#c9c9c9"), ("active", "#f2f2f2")],
            relief=[("pressed", "sunken"), ("!pressed", "raised")],
        )

        style.configure("TNotebook", background="#c9c9c9", borderwidth=1, tabmargins=(4, 5, 4, 0))
        style.configure(
            "TNotebook.Tab",
            padding=(16, 8),
            background="#b8b8b8",
            lightcolor="#b8b8b8",
            bordercolor="#7a7a7a",
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", BG)],
            lightcolor=[("selected", "#eeeeee")],
            expand=[("selected", (2, 2, 2, 0))],
        )

    # ------------------------------------------------------------------ menu
    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="ASIN Listesi Yükle (.txt)...", command=self.load_file)
        file_menu.add_command(label="Kayıt Konumu Seç...", command=self.choose_output_dir)
        file_menu.add_separator()
        file_menu.add_command(label="Temiz Listeyi Aç", command=lambda: self.open_path(self.upload_txt_path()))
        file_menu.add_command(label="Tüm Sonuçları Aç (CSV)", command=lambda: self.open_path(self.results_csv_path()))
        file_menu.add_command(label="Ekran Görüntüleri Klasörünü Aç", command=lambda: self.open_path(SCREENSHOT_DIR))
        file_menu.add_command(
            label="Kayıt Klasörünü Aç (tüm geçmiş taramalar)",
            command=lambda: self.open_path(self.output_dir),
        )
        file_menu.add_separator()
        file_menu.add_command(label="Çıkış", command=self.on_close)
        menubar.add_cascade(label="Dosya", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Keepa Hesabı...", command=self.show_account_dialog)
        settings_menu.add_command(label="Keepa API Ayarları...", command=self.show_api_settings_dialog)
        settings_menu.add_command(label="Lisans Anahtarı Gir...", command=self.show_license_dialog)
        settings_menu.add_separator()
        settings_menu.add_command(label="ASIN Bul - Kayıt Klasörü...", command=self.show_finder_output_dir_dialog)
        menubar.add_cascade(label="Ayarlar", menu=settings_menu)

        self.view_mode_var = tk.StringVar(value="normal")
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_radiobutton(
            label="Normal Görünüm", variable=self.view_mode_var, value="normal",
            command=self.apply_view_mode,
        )
        view_menu.add_radiobutton(
            label="Küçük Ekran (Light)", variable=self.view_mode_var, value="compact",
            command=self.apply_view_mode,
        )
        menubar.add_cascade(label="Görünüm", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Bu Program Ne İşe Yarar?", command=self.show_help)
        help_menu.add_command(label="İstatistikler", command=self.show_stats)
        help_menu.add_command(label="Hakkında / Lisans", command=self.show_about)
        help_menu.add_command(label="İletişim", command=self.show_contact)
        menubar.add_cascade(label="Yardım", menu=help_menu)

        self.root.config(menu=menubar)

    # ------------------------------------------------------- kaynak pazar
    def _build_source_market_bar(self):
        """TUM sekmelerden GORUNEN, bariz bir kaynak-pazar secici --
        kullanicidan geldi: program eskiden HER YERDE (ASIN Bul'un aradigi
        pazar VE Temizle'nin okudugu grafik) sessizce JP'ye (domain=5) sabitti,
        degistirmenin tek yolu Finder sekmesine gomulu, kolay kacan bir
        "Domain" sayi kutusuydu (o kutu artik KALDIRILDI, yerini bu aldı).
        Burada secilen deger notebook'un HANGI sekmesi acik olursa olsun
        gecerlidir -- bu yuzden bilerek notebook'un DISINDA, ustunde."""
        bar = ttk.Frame(self.root, padding=(10, 6))
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(bar, text="Kaynak Pazar:", font=("Segoe UI", 9, "bold")).pack(side="left")

        self._source_market_label_to_id = {label: str(dom) for label, dom in KEEPA_MARKET_DOMAINS}
        default_label = next(
            (label for label, dom in KEEPA_MARKET_DOMAINS if dom == JPY_TUNED_SOURCE_DOMAIN),
            KEEPA_MARKET_DOMAINS[0][0],
        )
        self.source_market_domain_var = tk.StringVar(value=str(JPY_TUNED_SOURCE_DOMAIN))
        self.source_market_display_var = tk.StringVar(value=default_label)
        self.source_market_combo = ttk.Combobox(
            bar, textvariable=self.source_market_display_var,
            values=[label for label, _ in KEEPA_MARKET_DOMAINS], width=26, state="readonly",
        )
        self.source_market_combo.pack(side="left", padx=(6, 0))
        self.source_market_combo.bind("<<ComboboxSelected>>", self._on_source_market_change)
        HelpTooltip(
            bar,
            "ASIN Bul sekmesinin hangi ülkede arama yapacağını VE Temizle sekmesinin hangi "
            "ülkenin grafiğini okuyacağını belirler -- yani kaynağını bulduğun pazar. "
            "Dropshipping'de \"hangi ülkeden ürün buluyorum\" sorusunun cevabı burası.\n\n"
            "ÖNEMLİ: Sadece Japonya (Amazon.co.jp) için hazırlanmış S1-S5 hazır stratejiler ve "
            "kategori şablonları, başka bir ülke seçince Fiyat aralığı Japon yeni (¥) cinsinden "
            "olduğu için anlamsız sonuç verir -- bu yüzden başka bir ülke seçtiğinde bu hazır "
            "stratejiler listeden kaldırılır, sadece aşağıdaki manuel filtreleri (kendi para "
            "biriminle ayarlayarak) kullanabilirsin.",
        ).widget.pack(side="left", padx=(6, 0))
        self.source_market_status_label = ttk.Label(bar, text="", foreground="#666", font=("Segoe UI", 8))
        self.source_market_status_label.pack(side="left", padx=(10, 0))
        kf.DOMAIN = JPY_TUNED_SOURCE_DOMAIN

    def _on_source_market_change(self, _event=None):
        label = self.source_market_display_var.get()
        domain_str = self._source_market_label_to_id.get(label)
        if domain_str is None:
            return
        self.source_market_domain_var.set(domain_str)
        kf.DOMAIN = int(domain_str)
        self._refresh_strategy_options()
        self._update_price_currency_labels()
        # Kayit klasorunu de (auto modda) yeni ulkeye gore yeniden hesapla --
        # _refresh_strategy_options SADECE strateji secimi ARTIK GECERSIZ
        # olunca klasoru guncelliyordu (orn. JP'den cikinca "Yok"a donunce);
        # zaten "Yok" secili haldeyken ulke degistirince klasor HIC
        # guncellenmiyordu -- musteriden geldi.
        if hasattr(self, "finder_strategy_var"):
            self._finder_on_strategy_change()
        if hasattr(self, "rakip_domain_info_label"):
            self._rakip_update_domain_label()

    def _update_price_currency_labels(self):
        """Fiyat min/max etiketlerine secili kaynak pazarin para birimini
        ekler (orn. 'Fiyat min (JPY):') VE kutulara o para biriminde,
        yaklasik 30-500 USD araligina denk gelen bir varsayilan deger
        yazar -- musteriden geldi: kutuya "500" yazinca hangi para
        biriminde oldugu belli degildi, ayrica her pazar icin makul bir
        varsayilan bant olsun istendi."""
        if not hasattr(self, "finder_price_min_label"):
            return
        domain = int(self.source_market_domain_var.get())
        currency = KEEPA_DOMAIN_CURRENCY.get(domain, "")
        suffix = f" ({currency})" if currency else ""
        self.finder_price_min_label.config(text=f"Fiyat min{suffix}:")
        self.finder_price_max_label.config(text=f"Fiyat max{suffix}:")
        self.finder_filter_vars["LISTPRICE_MIN"].set(
            str(_default_price_for_domain(domain, DEFAULT_PRICE_MIN_USD))
        )
        self.finder_filter_vars["LISTPRICE_MAX"].set(
            str(_default_price_for_domain(domain, DEFAULT_PRICE_MAX_USD))
        )

    def _refresh_strategy_options(self):
        """Kaynak pazar Japonya disinda ise, JPY fiyat araligiyla ayarlanmis
        S1-S5 hazir stratejileri ve kategori-plani girdilerini listeden
        CIKARIR -- secili haldeyken kaynak degisirse "Yok (manuel filtreler)"
        secenegine geri doner, boylece yanlislikla yanlis para biriminde
        arama yapilmaz."""
        if not hasattr(self, "finder_strategy_var"):
            return
        is_jp = self.source_market_domain_var.get() == str(JPY_TUNED_SOURCE_DOMAIN)
        none_label = "Yok (aşağıdaki manuel/kişisel filtreleri kullan)"
        if is_jp:
            values = [none_label] + [kf.STRATEGY_LABELS[key] for key in kf.STRATEGIES]
            if self._finder_category_label_to_entry:
                values += sorted(self._finder_category_label_to_entry.keys())
            self.source_market_status_label.config(text="")
        else:
            values = [none_label]
            self.source_market_status_label.config(
                text="(Hazır stratejiler sadece Japonya'da kullanılabilir -- manuel filtreleri kendi para biriminde ayarla)"
            )
        self.finder_strategy_combo.config(values=values)
        if self.finder_strategy_var.get() not in values:
            self.finder_strategy_var.set(none_label)
            self._finder_on_strategy_change()

    # ------------------------------------------------------------ finder tab
    # keepa_finder_gui.py'nin (Sales Rank taramasi ile aday ASIN toplama)
    # ayni mantigi -- artik ayri bir program degil, ilk sekme. Kendi
    # kuyrugunu/poll donguсunu kullanir (Temizle sekmesiyle "log"/"done"
    # gibi ayni event isimleri karismasin diye).
    def _build_finder_tab(self, parent):
        self.finder_event_queue = queue.Queue()
        self.finder_running = False
        self.finder_stop_event = None
        self.finder_total_asins = 0

        frm = ttk.Frame(parent, padding=7)
        frm.grid(row=0, column=0, sticky="nsew")
        row = 0

        # Kayit klasoru artik bu sekmede GORUNMUYOR -- musteriden geldi:
        # arama kriterleri arasinda "Kayit Klasoru" alani kafa karistiriyordu
        # (neredeyse hic elle degistirilmiyor, cunku strateji secimine gore
        # OTOMATIK yonetiliyor). Degistirmek isteyen Ayarlar > ASIN Bul
        # Kayit Klasoru... menusunu kullanir (bkz. show_finder_output_dir_dialog).
        self.finder_output_dir_var = tk.StringVar(value=str(BASE_DIR))
        self._finder_output_dir_is_auto = True

        strategy_frame = ttk.LabelFrame(frm, text="Hazır Strateji (Buy Box / dropshipping odaklı, opsiyonel)")
        strategy_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 3))
        self.finder_strategy_var = tk.StringVar(value="Yok (aşağıdaki manuel/kişisel filtreleri kullan)")
        strategy_values = ["Yok (aşağıdaki manuel/kişisel filtreleri kullan)"] + [
            kf.STRATEGY_LABELS[key] for key in kf.STRATEGIES
        ]
        self._finder_strategy_label_to_key = {
            kf.STRATEGY_LABELS[key]: key for key in kf.STRATEGIES
        }
        # Kategori bazli hazir sablonlar (2026-09-14: Japonya kategori agaci +
        # her kategorinin KENDI highestRank'ine oranli dinamik Sales Rank
        # bandi) -- keepa_kategori_plani.json'dan yuklenir. Bu dosya
        # gelistiricinin KENDI Keepa anahtariyla ONCEDEN cikarilmis bir plan
        # (hangi kategori/bant kombinasyonlarinin "harcamaya deger" oldugu) --
        # musteri bunu SEÇTIGINDE gercek ASIN cekimi YINE musterinin KENDI
        # API anahtariyla yapilir (S1-S5 stratejileriyle AYNI model).
        self._finder_category_label_to_entry = {}
        category_plan = kf.load_category_plan()
        if category_plan:
            for entry in category_plan:
                label = (
                    f"[Kategori] {entry['path']} -- bant {entry['band']} "
                    f"(Sales Rank {entry['sales_gte']}-{entry['sales_lte']}, ~{entry['total_results']} ürün)"
                )
                self._finder_category_label_to_entry[label] = entry
            strategy_values += sorted(self._finder_category_label_to_entry.keys())
        self.finder_strategy_combo = ttk.Combobox(
            strategy_frame, textvariable=self.finder_strategy_var, values=strategy_values,
            state="readonly", width=48,
        )
        self.finder_strategy_combo.grid(row=0, column=0, padx=8, pady=5, sticky="w")
        self.finder_strategy_combo.bind("<<ComboboxSelected>>", self._finder_on_strategy_change)
        ttk.Button(
            strategy_frame, text="Kişisel Filtre...", command=self.show_personal_filter_dialog
        ).grid(row=0, column=1, padx=(0, 8), pady=5, sticky="w")
        self.finder_personal_status_label = ttk.Label(strategy_frame, text="", foreground="#555")
        self.finder_personal_status_label.grid(row=0, column=2, padx=(0, 8), pady=5, sticky="w")
        row += 1

        self.finder_manual_frame = ttk.Frame(frm)
        self.finder_manual_frame.grid(row=row, column=0, columnspan=4, sticky="ew")
        self.finder_manual_frame.columnconfigure(0, weight=1)
        manual_row = 0

        params = ttk.LabelFrame(self.finder_manual_frame, text="Sales Rank Aralığı")
        params.grid(row=manual_row, column=0, columnspan=4, sticky="ew", pady=(0, 3))
        ttk.Label(params, text="Başlangıç:").grid(row=0, column=0, padx=5, pady=5)
        self.finder_start_var = tk.IntVar(value=kf.SALES_RANK_START)
        ttk.Entry(params, textvariable=self.finder_start_var, width=10).grid(row=0, column=1, padx=5)
        ttk.Label(params, text="Bitiş:").grid(row=0, column=2, padx=5)
        self.finder_end_var = tk.IntVar(value=kf.SALES_RANK_END)
        ttk.Entry(params, textvariable=self.finder_end_var, width=10).grid(row=0, column=3, padx=5)
        ttk.Label(params, text="Dilim genişliği:").grid(row=0, column=4, padx=5)
        self.finder_step_var = tk.IntVar(value=kf.SALES_RANK_STEP)
        ttk.Entry(params, textvariable=self.finder_step_var, width=8).grid(row=0, column=5, padx=5)
        manual_row += 1

        filters = ttk.LabelFrame(self.finder_manual_frame, text="Filtreler (dropshipping için sık kullanılanlar)")
        filters.grid(row=manual_row, column=0, columnspan=4, sticky="ew", pady=(0, 3))
        self.finder_filter_vars = {}

        # "None" (Python'daki bos-deger yazisi) eskiden alanlarda AYNEN
        # goruniyordu -- Turkce kullanan bir musteri icin anlamsiz/kafa
        # karistirici (kullanicidan geldi, dogrulandi). Simdi bos deger
        # gercekten BOS gosteriliyor + her alanin yanina ne ise yaradigini
        # ve bos birakilirsa ne olacagini anlatan bir "?" balonu eklendi.
        def add_filter(label_text, attr_name, kf_attr, col, r, width=8, help_text=None):
            cell = ttk.Frame(filters)
            cell.grid(row=r, column=col, padx=5, pady=3, sticky="w")
            label_widget = ttk.Label(cell, text=label_text)
            label_widget.pack(side="left")
            if help_text:
                HelpTooltip(cell, help_text).widget.pack(side="left", padx=(3, 0))
            raw_value = getattr(kf, kf_attr, None)
            var = tk.StringVar(value="" if raw_value is None else str(raw_value))
            ttk.Entry(filters, textvariable=var, width=width).grid(row=r, column=col + 1, padx=5, pady=3, sticky="w")
            self.finder_filter_vars[kf_attr] = var
            return label_widget

        # ESKIDEN burada ayrica gomulu bir "Domain" sayi kutusu vardi --
        # kaldirildi, yerini ustteki (notebook'un DISINDA, tum sekmelerden
        # gorunen) "Kaynak Pazar" seçici aldi (bkz. _build_source_market_bar).
        # kf.DOMAIN artik ORADAN yonetiliyor, burada AYRICA sormaya gerek yok.
        # Fiyat etiketlerinin referansini tutuyoruz ki secili pazar
        # degisince (_update_price_currency_labels) para birimini
        # etikette gosterebilelim -- musteriden geldi: kutuya "500"
        # yazinca hangi para biriminde oldugu belli degildi.
        self.finder_price_min_label = add_filter(
            "Fiyat min:", "price_min", "LISTPRICE_MIN", 0, 0,
            help_text="Ürünün liste fiyatı (seçili kaynak pazarın kendi para biriminde) için alt "
            "sınır. Bunun altındaki ürünler aramaya hiç dahil edilmez. Boş bırakılamaz. "
            "Varsayılan olarak yaklaşık 30 dolar karşılığı ile doldurulur, dilersen değiştir.",
        )
        self.finder_price_max_label = add_filter(
            "Fiyat max:", "price_max", "LISTPRICE_MAX", 2, 0,
            help_text="Ürünün liste fiyatı (seçili kaynak pazarın kendi para biriminde) için üst "
            "sınır. Bunun üstündeki ürünler aramaya hiç dahil edilmez. Boş bırakılamaz. "
            "Varsayılan olarak yaklaşık 500 dolar karşılığı ile doldurulur, dilersen değiştir.",
        )
        self._update_price_currency_labels()
        add_filter(
            "Offer count min:", "offer_min", "OFFER_COUNT_MIN", 0, 1, width=6,
            help_text="Ürünü satan satıcı sayısı için alt sınır (rekabet çok azsa/hiç yoksa "
            "genelde talep de düşüktür). Boş bırakılamaz.",
        )
        add_filter(
            "Offer count max:", "offer_max", "OFFER_COUNT_MAX", 2, 1, width=6,
            help_text="Ürünü satan satıcı sayısı için üst sınır (çok fazla satıcı = çok yüksek "
            "rekabet, kâr marjı düşer). Boş bırakılamaz.",
        )
        add_filter(
            "Son X gün güncellenmiş:", "recent_days", "RECENT_OFFERS_UPDATE_DAYS", 4, 1, width=6,
            help_text="Ürünün teklif/fiyat bilgisi son kaç gün içinde güncellenmiş olmalı. Eski/"
            "güncellenmemiş verili ürünleri eler. Boş bırakılamaz, 0 da girilemez -- 0 girersen "
            "hiçbir sonuç bulunmaz (ürünün tam şu an güncellenmiş olmasını ister). En az 1 kullan.",
        )
        add_filter(
            "30 günlük SR düşüş min:", "sr_drop_30", "SALES_RANK_DROPS_30_MIN", 0, 2, width=6,
            help_text="Son 30 günde Satış Sıralaması (Sales Rank) en az kaç kez iyileşti/düştü "
            "-- bu bir satış sinyalidir, yüksek olması daha çok satıldığını gösterir. "
            "BOŞ BIRAKILIRSA bu filtre hiç uygulanmaz (tüm ürünler kabul edilir).",
        )
        add_filter(
            "90 günlük SR düşüş min:", "sr_drop_90", "SALES_RANK_DROPS_90_MIN", 2, 2, width=6,
            help_text="Aynı şey ama son 90 gün için. BOŞ BIRAKILIRSA bu filtre hiç uygulanmaz "
            "(tüm ürünler kabul edilir).",
        )
        add_filter(
            "Aylık satış (tahmini) min:", "monthly_sold_min", "MONTHLY_SOLD_MIN", 4, 2, width=6,
            help_text="Amazon'un tahmini \"geçen ay X adet satıldı\" verisi için alt sınır. Her "
            "üründe bu veri yoktur. BOŞ BIRAKILIRSA bu filtre hiç uygulanmaz.",
        )
        add_filter(
            "Amazon'un kendisi satıyorsa hariç tut:", "no_amazon", "AVAILABILITY_AMAZON_EXCLUDE", 0, 3, width=6,
            help_text="Amazon.co.jp'nin kendisinin sattığı ürünleri elemek için 1 yaz (Amazon ile "
            "rekabet etmek zordur). BOŞ BIRAKIRSAN bu filtre uygulanmaz, Amazon'un sattığı "
            "ürünler de listeye girebilir.",
        )
        add_filter(
            "Paket ağırlığı max (gram):", "package_weight_max", "PACKAGE_WEIGHT_GRAMS_MAX", 2, 3, width=8,
            help_text="Kargo/gönderim maliyetini sınırlamak için paket ağırlığı üst sınırı (gram). "
            "BOŞ BIRAKILIRSA ağırlık hiç dikkate alınmaz.",
        )
        add_filter(
            "Kategori (Root Category ID):", "root_category", "ROOT_CATEGORY", 4, 3, width=10,
            help_text="Sadece belirli bir Amazon kategorisinde ara (Keepa'nın kategori kimlik "
            "numarası). Bilmiyorsan BOŞ BIRAK -- tüm kategoriler taranır.",
        )
        ttk.Label(
            filters, foreground="#666", font=("Segoe UI", 8), wraplength=760, justify="left",
            text="Boş bırakılan alanlar Keepa Finder'a hiç gönderilmez (o filtre uygulanmaz). Her alanın "
            "yanındaki \"?\" işaretine fare ile gelerek ne işe yaradığını okuyabilirsin.",
        ).grid(row=4, column=0, columnspan=6, sticky="w", padx=5, pady=(2, 4))
        row += 1

        # Kisisel Keepa Filtresi (JSON) artik yukaridaki "Kişisel Filtre..."
        # butonuyle acilan ayri bir pencerede -- her zaman gorunen buyuk bir
        # blok olarak degil (ekran boyu tasmasin diye). Deger, dialog kapansa
        # bile self.finder_personal_json_value icinde kalici olarak saklanir.
        self.finder_use_personal_var = tk.BooleanVar(value=False)
        self.finder_personal_json_value = ""

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=row, column=0, columnspan=4, sticky="w", pady=5)
        self.finder_start_button = ttk.Button(btn_row, text="Başlat", command=self.finder_start)
        self.finder_start_button.pack(side="left")
        self.finder_stop_button = ttk.Button(btn_row, text="Durdur", command=self.finder_stop, state="disabled")
        self.finder_stop_button.pack(side="left", padx=(5, 0))
        self.finder_send_button = ttk.Button(
            btn_row, text="Bulunan ASIN'leri Temizle Sekmesine Gönder →", command=self.finder_send_to_check
        )
        self.finder_send_button.pack(side="left", padx=(15, 0))
        clear_cache_button = ttk.Button(
            btn_row, text="Sıfırla", command=lambda: self._clear_finder_slice_cache()
        )
        clear_cache_button.pack(side="left", padx=(15, 0))
        HelpTooltip(
            btn_row,
            "Bu klasördeki 'tamamlandı' Sales Rank dilim işaretlerini siler. Yanlış bir "
            "filtreyle taranmış (örn. hep 0 sonuç dönmüş) bir aralığı düzelttikten sonra "
            "yeniden taramak için kullan -- normalde gerekmez, sadece hatalı bir taramayı "
            "düzeltirken işine yarar.",
        ).widget.pack(side="left", padx=(3, 0))
        row += 1

        status_frame = ttk.LabelFrame(frm, text="Durum")
        status_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 3))
        self.finder_position_label = ttk.Label(status_frame, text="Şu an taranan: -", font=("Segoe UI", 10, "bold"))
        self.finder_position_label.grid(row=0, column=0, sticky="w", padx=8, pady=3, columnspan=2)
        self.finder_total_label = ttk.Label(status_frame, text="Toplam ASIN: 0", font=("Segoe UI", 10, "bold"))
        self.finder_total_label.grid(row=0, column=2, sticky="w", padx=8, pady=3)
        self.finder_token_label = ttk.Label(status_frame, text="Token: -")
        self.finder_token_label.grid(row=1, column=0, sticky="w", padx=8, pady=3, columnspan=2)
        self.finder_progress_pct_label = ttk.Label(status_frame, text="İlerleme: -")
        self.finder_progress_pct_label.grid(row=1, column=2, sticky="w", padx=8, pady=3)
        self.finder_cost_label = ttk.Label(status_frame, text="Token maliyeti: -")
        self.finder_cost_label.grid(row=2, column=0, sticky="w", padx=8, pady=3, columnspan=3)
        self.finder_progress = ttk.Progressbar(status_frame, length=400, mode="determinate")
        self.finder_progress.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 3))
        row += 1

        ttk.Label(frm, text="Log:").grid(row=row, column=0, sticky="w")
        row += 1
        self.finder_log_text = scrolledtext.ScrolledText(frm, height=5, width=95, state="disabled")
        self.finder_log_text.grid(row=row, column=0, columnspan=4, pady=(0, 3))
        row += 1

        ttk.Label(
            frm,
            text=(
                "Not: Bu sekme sadece Keepa API anahtarını kullanır (Ayarlar > Keepa API Ayarları) -- "
                "tarayıcı/Keepa oturum girişi gerekmez."
            ),
            foreground="#666", font=("Segoe UI", 8), wraplength=760, justify="left",
        ).grid(row=row, column=0, columnspan=4, sticky="w")

        self.root.after(150, self._poll_finder)

    def finder_choose_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.finder_output_dir_var.get())
        if chosen:
            self.finder_output_dir_var.set(chosen)
            self._finder_output_dir_is_auto = False

    def show_finder_output_dir_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("ASIN Bul - Kayıt Klasörü")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=(
                "ASIN Bul sonuçları normalde her strateji/kategori/kaynak "
                "pazar için kendi klasörüne otomatik kaydedilir -- farklı "
                "ülkelerin/taramaların dosyaları birbirine karışmasın diye. "
                "Sadece kendi seçtiğin sabit bir klasöre kaydetmek istersen "
                "aşağıdan değiştir."
            ),
            wraplength=440, justify="left",
        ).pack(padx=20, pady=(15, 10))

        path_label = ttk.Label(dialog, text="", wraplength=440, justify="left", foreground="#333")
        path_label.pack(padx=20, pady=(0, 10))

        def refresh_path_label():
            mode = "otomatik" if self._finder_output_dir_is_auto else "elle seçilmiş"
            path_label.config(text=f"Şu anki klasör ({mode}):\n{self.finder_output_dir_var.get()}")

        refresh_path_label()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(padx=20, pady=(0, 15))

        def do_choose():
            self.finder_choose_output_dir()
            refresh_path_label()

        def do_reset_auto():
            self._finder_output_dir_is_auto = True
            self._finder_on_strategy_change()
            refresh_path_label()

        ttk.Button(btn_frame, text="Klasör Seç...", command=do_choose).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Otomatik Yönetime Dön", command=do_reset_auto).pack(side="left", padx=5)
        ttk.Button(
            btn_frame, text="Bu Klasördeki Taramayı Sıfırla",
            command=lambda: self._clear_finder_slice_cache(parent=dialog),
        ).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Kapat", command=dialog.destroy).pack(side="left", padx=5)

    def _clear_finder_slice_cache(self, parent=None):
        """Her Sales Rank dilimi kendi "sales_BBBBB-EEEEE.txt" dosyasina
        yazilir -- hem "bu dilim taranmis" isareti HEM de o dilimde bulunan
        ASIN'lerin kendisi bu dosyada. Bir filtre hatasi (orn. "Son X gun
        guncellenmis" 0 iken hep 0 sonuc donmesi) yuzunden yanlislikla "0
        ASIN bulundu" olarak isaretlenmis bir dilim, duzeltmeden SONRA bile
        "zaten tarandi" diye atlaniyordu -- musteriden geldi. Bu dosyalari
        silmek dilimi GERCEKTEN sifirlar, bir sonraki taramada yeniden
        (dogru filtrelerle) sorgulanir. tum_asinler.txt / *_temiz.txt gibi
        BIRLESTIRILMIS cikti dosyalarina dokunulmaz (onlar ayrica, "Gonder"
        ile yeniden olusturulur). Hem Ayarlar penceresinden hem de ana
        sekmedeki "Sıfırla" butonundan cagrilir (musteriden geldi: sik
        kullaniyor, ana sekmede olsun istedi)."""
        parent = parent or self.root
        folder = Path(self.finder_output_dir_var.get())
        markers = list(folder.glob("sales_*-*.txt")) if folder.exists() else []
        if not markers:
            messagebox.showinfo(
                "Önbellek yok", "Bu klasörde temizlenecek 'tamamlandı' işareti bulunamadı.",
                parent=parent,
            )
            return
        if not messagebox.askyesno(
            "Emin misin?",
            f"{len(markers)} adet Sales Rank dilimi için 'tamamlandı' işareti silinecek. "
            "Bir sonraki taramada bu dilimler BAŞTAN (yeni filtrelerle) yeniden sorgulanır "
            "-- token tekrar harcanır. Bunu genelde önceki bir taramada YANLIŞ bir filtre "
            "kullanıldığını fark edip düzelttikten sonra yaparsın.\n\nDevam edilsin mi?",
            parent=parent,
        ):
            return
        for marker in markers:
            marker.unlink()
        messagebox.showinfo(
            "Temizlendi", f"{len(markers)} dilim işareti silindi. Bir sonraki taramada baştan taranacak.",
            parent=parent,
        )

    def finder_log(self, message):
        self.finder_log_text.configure(state="normal")
        self.finder_log_text.insert("end", message + "\n")
        self.finder_log_text.see("end")
        self.finder_log_text.configure(state="disabled")

    def _finder_on_strategy_change(self, _event=None):
        """Bir strateji secilince manuel Sales Rank/Filtre bolumlerini
        gizler (zaten yok sayilacaklari icin ekranda yer kaplamasinlar);
        'Yok' secilince geri gosterir. Ekran boyu tasmasin diye.

        Kayit klasorunu de -- kullanici kendi elleriyle "Klasor Sec..."
        yapmadigi surece -- secilen stratejiye ozel bir alt klasore
        otomatik tasir. Boylece farkli stratejilerin/kriterlerin dilim
        dosyalari yanlislikla ayni klasorde karismaz."""
        label = self.finder_strategy_var.get()
        strategy_key = self._finder_strategy_label_to_key.get(label)
        category_entry = self._finder_category_label_to_entry.get(label)
        if strategy_key is not None or category_entry is not None:
            self.finder_manual_frame.grid_remove()
        else:
            self.finder_manual_frame.grid()

        if self._finder_output_dir_is_auto:
            if strategy_key is not None:
                self.finder_output_dir_var.set(str(BASE_DIR / f"keepa_arama_{strategy_key}"))
            elif category_entry is not None:
                self.finder_output_dir_var.set(
                    str(BASE_DIR / f"keepa_arama_kategori_{category_entry['catId']}_{category_entry['band']}")
                )
            else:
                # Manuel filtre modu HER ulkede kullanilabilir (strateji/
                # kategori modlarinin aksine, sadece JP'ye ozel degil) --
                # klasoru kaynak pazara gore de ayirmazsak, orn. Avustralya'da
                # taranmis bir Sales Rank dilimi, Hollanda'da AYNI dilimi
                # tekrar taramaya calisinca "zaten tarandi" diye yanlislikla
                # atlanir (musteriden geldi: ulke degistirince log/sonuc
                # degismiyordu -- sebebi buydu, tum ulkeler ayni klasoru
                # paylasiyordu).
                domain = int(self.source_market_domain_var.get())
                self.finder_output_dir_var.set(str(BASE_DIR / f"keepa_arama_manuel_domain_{domain}"))

        # ONEMLI: burada artik pencereyi kucultmuyoruz (eskiden geometry("")
        # ile o an gorunen icerige gore kuculuyordu). Pencere boyutu acilista
        # TUM sekmelerin en genisine gore BIR KEZ sabitleniyor (bkz.
        # _fit_to_widest_tab, resizable(False) ile birlikte) -- burada
        # kucultmek, sonra baska bir sekmeye gecince (artik yeniden
        # buyutulemeyen bir pencerede) icerigin kirpilmasina yol acardi.

    def show_personal_filter_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Kişisel Keepa Filtresi")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Checkbutton(
            dialog,
            text="Hazır Strateji 'Yok' iken, manuel alanlar yerine bu JSON'u kullan",
            variable=self.finder_use_personal_var,
        ).pack(padx=15, pady=(15, 5), anchor="w")

        info_text = scrolledtext.ScrolledText(dialog, width=78, height=10, wrap="word")
        info_text.pack(padx=15, pady=(0, 8))
        info_text.insert("1.0", PERSONAL_FILTER_HELP_TEXT)
        info_text.configure(state="disabled")

        json_text = scrolledtext.ScrolledText(dialog, width=78, height=6)
        json_text.pack(padx=15, pady=(0, 8))
        json_text.insert("1.0", self.finder_personal_json_value)

        status_label = ttk.Label(dialog, text="", foreground="#555")
        status_label.pack(padx=15, pady=(0, 5), anchor="w")

        def do_validate():
            raw = json_text.get("1.0", "end").strip()
            if not raw:
                status_label.config(text="Kutu boş.", foreground="#b00")
                return
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as error:
                status_label.config(text=f"Geçersiz JSON: {error}", foreground="#b00")
                return
            if not isinstance(data, dict):
                status_label.config(text="JSON bir obje ({...}) olmalı.", foreground="#b00")
                return
            status_label.config(text=f"Geçerli JSON -- {len(data)} filtre alanı bulundu.", foreground="#0a5")

        def do_close():
            self.finder_personal_json_value = json_text.get("1.0", "end").strip()
            has_value = bool(self.finder_personal_json_value)
            self.finder_personal_status_label.config(
                text="Kişisel filtre kayıtlı" if (self.finder_use_personal_var.get() and has_value) else "",
                foreground="#0a5",
            )
            dialog.destroy()

        btn_row = ttk.Frame(dialog)
        btn_row.pack(pady=(0, 15))
        ttk.Button(btn_row, text="JSON'u Doğrula", command=do_validate).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Kaydet ve Kapat", command=do_close).pack(side="left", padx=5)
        dialog.protocol("WM_DELETE_WINDOW", do_close)

    # Bunlar sorgunun HER ZAMAN icerdigi temel filtreler -- bos birakilamaz
    # (digerlerinin aksine, build_selection bunlari kosulsuz kullanir).
    FINDER_REQUIRED_FILTERS = {
        "LISTPRICE_MIN", "LISTPRICE_MAX",
        "OFFER_COUNT_MIN", "OFFER_COUNT_MAX", "RECENT_OFFERS_UPDATE_DAYS",
    }

    def _finder_apply_filters(self):
        """GUI alanlarindaki degerleri kf modulunun sabitlerine yazar --
        opsiyonel bir alan bos birakilirsa o filtre sorguya hic eklenmez
        (kf.build_selection None degerleri atlar); temel filtreler ise
        bos birakilamaz."""
        for kf_attr, var in self.finder_filter_vars.items():
            raw = var.get().strip()
            if raw == "":
                if kf_attr in self.FINDER_REQUIRED_FILTERS:
                    raise ValueError(f"'{kf_attr}' alanı boş bırakılamaz.")
                setattr(kf, kf_attr, None)
                continue
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f"'{raw}' sayı değil (alan: {kf_attr})")
            # "Son X gun guncellenmis" alaninda 0 (veya negatif), Keepa'ya
            # "teklif TAM SU AN (gecmis degil, gelecek an) guncellenmis
            # olmali" diye gidiyor -- hicbir gecmis urun bunu karsilayamaz,
            # sonuc HER ZAMAN sifir cikar (herhangi bir ulke/pazarda,
            # bununla alakasi yok). Musteriden geldi: AU'da 0 ASIN
            # bulununca sanki ulkeyle ilgili bir sorun var sanildi.
            if kf_attr == "RECENT_OFFERS_UPDATE_DAYS" and value <= 0:
                raise ValueError(
                    "'Son X gün güncellenmiş' alanı 0 veya negatif olamaz -- "
                    "bu durumda Keepa hiçbir zaman sonuç döndürmez (ürünün tam "
                    "şu an güncellenmiş olmasını ister). En az 1 gir."
                )
            setattr(kf, kf_attr, value)

    def finder_start(self):
        if self.finder_running:
            return
        try:
            output_dir = Path(self.finder_output_dir_var.get())
        except tk.TclError:
            messagebox.showwarning("Geçersiz değer", "Kayıt klasörü geçersiz.")
            return
        api_key = keepa_api_settings.load_api_key()
        if not api_key:
            messagebox.showwarning(
                "Keepa API anahtarı eksik",
                "ASIN Bul için önce Ayarlar > Keepa API Ayarları... menüsünden kendi Keepa API anahtarını gir.",
            )
            return

        strategy_label = self.finder_strategy_var.get()
        strategy_key = self._finder_strategy_label_to_key.get(strategy_label)
        category_entry = self._finder_category_label_to_entry.get(strategy_label)

        step = 0  # strateji/kategori modunda kullanilmiyor, sadece manuel modda gercek deger alir
        if strategy_key is not None:
            # Hazir strateji modu: manuel Sales Rank/filtre alanlari YOK
            # SAYILIR -- strateji kendi bandini/kriterlerini tasir.
            strategy = kf.STRATEGIES[strategy_key]
            start, end = strategy["sales_gte"], strategy["sales_lte"]
            using_personal_filter = False
        elif category_entry is not None:
            # Kategori sablonu modu: kategori + bant KENDI Sales Rank
            # araligini tasir, manuel alanlar yok sayilir.
            start, end = category_entry["sales_gte"], category_entry["sales_lte"]
            using_personal_filter = False
        else:
            try:
                start = int(self.finder_start_var.get())
                end = int(self.finder_end_var.get())
                step = int(self.finder_step_var.get())
            except (ValueError, tk.TclError):
                messagebox.showwarning("Geçersiz değer", "Sales Rank alanları sayı olmalı.")
                return
            if step <= 0 or end <= start:
                messagebox.showwarning("Geçersiz aralık", "Bitiş, başlangıçtan büyük ve dilim genişliği pozitif olmalı.")
                return
            try:
                self._finder_apply_filters()
            except ValueError as error:
                messagebox.showwarning("Geçersiz filtre değeri", str(error))
                return

            if self.finder_use_personal_var.get():
                raw_json = self.finder_personal_json_value.strip()
                if not raw_json:
                    messagebox.showwarning(
                        "Kişisel filtre boş",
                        "Kişisel Keepa Filtresi işaretli ama boş. Ayarlar için "
                        "'Kişisel Filtre...' butonunu kullan ya da işareti kaldır.",
                    )
                    return
                try:
                    personal_selection = json.loads(raw_json)
                except json.JSONDecodeError as error:
                    messagebox.showerror("Geçersiz JSON", f"Kişisel filtre JSON'u okunamadı: {error}")
                    return
                if not isinstance(personal_selection, dict):
                    messagebox.showerror("Geçersiz JSON", "JSON bir obje ({...}) olmalı.")
                    return
                kf.PERSONAL_SELECTION_BASE = personal_selection
                using_personal_filter = True
            else:
                kf.PERSONAL_SELECTION_BASE = None
                using_personal_filter = False

        self.finder_active_start = start
        self.finder_active_end = end
        self.finder_running = True
        self.finder_stop_event = threading.Event()
        self.finder_start_button.config(state="disabled")
        self.finder_stop_button.config(state="normal")
        self.finder_progress.config(maximum=max(1, end - start), value=0)
        self.finder_log_text.configure(state="normal")
        self.finder_log_text.delete("1.0", "end")
        self.finder_log_text.configure(state="disabled")
        if strategy_key is not None:
            self.finder_log(f">>> Hazır strateji kullanılıyor: {strategy_label} (Sales Rank {start}-{end}).")
        elif category_entry is not None:
            self.finder_log(f">>> Kategori şablonu kullanılıyor: {strategy_label}")
        elif using_personal_filter:
            self.finder_log(">>> Kişisel Keepa filtresi kullanılıyor (yukarıdaki hazır alanlar yok sayıldı).")

        self.finder_current_output_dir = output_dir
        # ONEMLI (2026-09-14, canli dogrulandi): output_dir strateji/kategori
        # modunda BASE_DIR'in DOGRUDAN altinda ("keepa_arama_S1_..." gibi),
        # keepa_sync/'in ICINDE DEGIL -- eski "output_dir'in kardesi" formulu
        # bu yuzden YANLIS konuma (BASE_DIR/ortak_asin_havuzu.txt) yazardi --
        # bu HATA fetch_strategy_asins/fetch_category_plan_asins icinde 2 KERE
        # canli yasanip duzeltildi (bkz. keepa_finder.py notlari), GUI'nin
        # kendisi de AYNI hataya sahipti, burada da duzeltiliyor.
        keepa_sync_pool = BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt"
        if keepa_sync_pool.parent.exists():
            self.finder_current_shared_pool_path = keepa_sync_pool
        else:
            self.finder_current_shared_pool_path = output_dir.resolve().parent / "ortak_asin_havuzu.txt"
        thread = threading.Thread(
            target=self._finder_run,
            args=(output_dir, start, end, step, api_key, strategy_key, category_entry),
            daemon=True,
        )
        thread.start()

    def finder_send_to_check(self):
        output_dir = getattr(self, "finder_current_output_dir", None)
        if output_dir is None:
            try:
                output_dir = Path(self.finder_output_dir_var.get())
            except tk.TclError:
                messagebox.showwarning("Klasör yok", "Önce bir tarama klasörü seç/başlat.")
                return
        combined_path = output_dir / "tum_asinler.txt"
        if not combined_path.exists():
            messagebox.showwarning(
                "ASIN bulunamadı", f"{combined_path} henüz oluşmamış -- önce ASIN Bul sekmesinde bir tarama çalıştır."
            )
            return
        text = combined_path.read_text(encoding="utf-8")
        asins = [line.strip().upper() for line in text.splitlines() if line.strip()]
        self.asins = asins
        self.asin_count_label.config(text=f"{len(self.asins)} ASIN yüklendi ({combined_path.name})")
        self.notebook.select(1)
        messagebox.showinfo("Gönderildi", f"{len(asins)} ASIN 'Temizle' sekmesine yüklendi.")

    def _finder_notify_complete(self, total):
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        try:
            ctypes.windll.user32.FlashWindow(self.root.winfo_id(), True)
        except Exception:
            pass
        try:
            marker = self.finder_current_output_dir / f"TAMAMLANDI_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            marker.write_text(
                f"Tarama tamamlandi.\n"
                f"Zaman: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Sales Rank araligi: {self.finder_active_start} - {self.finder_active_end}\n"
                f"Toplam ASIN (bu klasor): {total}\n"
                f"Ortak havuz dosyasi: {self.finder_current_shared_pool_path}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def finder_stop(self):
        if not self.finder_running or self.finder_stop_event is None:
            return
        self.finder_stop_event.set()
        self.finder_stop_button.config(state="disabled")
        self.finder_log(">>> Durdurma istendi -- şu an beklenen bir aralık varsa bitince güvenli şekilde duracak.")

    def _finder_run(self, output_dir, start, end, step, api_key, strategy_key=None, category_entry=None):
        def progress_callback(kind, payload):
            self.finder_event_queue.put((kind, payload))

        try:
            if strategy_key is not None:
                kf.fetch_strategy_asins(
                    strategy_key, output_dir,
                    progress=progress_callback, stop_event=self.finder_stop_event,
                    shared_pool_path=self.finder_current_shared_pool_path,
                    api_key=api_key,
                )
            elif category_entry is not None:
                kf.fetch_category_plan_asins(
                    category_entry, output_dir,
                    progress=progress_callback, stop_event=self.finder_stop_event,
                    shared_pool_path=self.finder_current_shared_pool_path,
                    api_key=api_key,
                )
            else:
                kf.fetch_all_asins(
                    output_dir, sales_rank_start=start, sales_rank_end=end, sales_rank_step=step,
                    progress=progress_callback, stop_event=self.finder_stop_event,
                    shared_pool_path=self.finder_current_shared_pool_path,
                    api_key=api_key,
                )
        except Exception as error:
            self.finder_event_queue.put(("fatal", {"message": f"{type(error).__name__}: {error}"}))
        self.finder_event_queue.put(("done", {}))

    def _poll_finder(self):
        try:
            while True:
                kind, payload = self.finder_event_queue.get_nowait()
                message = payload.get("message", "")
                if kind in ("log", "slice_start", "page"):
                    if message:
                        self.finder_log(message)
                if kind in ("slice_start", "page"):
                    position = payload.get("slice_end") if kind == "slice_start" else payload.get("position")
                    if position is not None:
                        self.finder_position_label.config(text=f"Şu an taranan Sales Rank: {position}")
                        try:
                            start = self.finder_active_start
                            end = self.finder_active_end
                            done = max(0, position - start)
                            self.finder_progress.config(value=done)
                            pct = min(100, done / max(1, end - start) * 100)
                            self.finder_progress_pct_label.config(text=f"İlerleme: %{pct:.2f}")
                        except (ValueError, tk.TclError):
                            pass
                if kind == "page":
                    total = payload.get("total")
                    if total is not None:
                        self.finder_total_asins = total
                        self.finder_total_label.config(text=f"Toplam ASIN: {total}")
                    tokens_left = payload.get("tokens_left")
                    refill_rate = payload.get("refill_rate")
                    if tokens_left is not None:
                        self.finder_token_label.config(
                            text=f"Token: {tokens_left} kalan, {refill_rate or '?'} /dk yenileniyor"
                        )
                    tokens_total = payload.get("tokens_consumed_total")
                    tokens_per_asin = payload.get("tokens_per_asin")
                    if tokens_total is not None:
                        self.finder_cost_label.config(
                            text=f"Token maliyeti: bu oturumda {tokens_total} token kullanıldı "
                                 f"(ASIN başına ortalama {tokens_per_asin:.2f} token)"
                        )
                elif kind == "complete":
                    self.finder_log(f">>> TAMAMLANDI: {message}")
                    self._finder_notify_complete(payload.get("total", self.finder_total_asins))
                elif kind == "stopped":
                    self.finder_log(f">>> {message}")
                elif kind == "fatal":
                    messagebox.showerror("Hata", message)
                elif kind == "done":
                    self.finder_running = False
                    self.finder_start_button.config(state="normal")
                    self.finder_stop_button.config(state="disabled")
                    self.finder_log(">>> Durdu / tamamlandı.")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_finder)

    # -------------------------------------------------------------- rakip tab
    def _build_rakip_tab(self, parent):
        """Bilinen Turk rakip saticilarin Keepa storefront'unu (TUM aktif
        ASIN listesi) cekip ortak havuza ekler -- harvest_seller_asins.py'nin
        AYNI mantigi, GUI icinden calistirilabilir + satici listesi burada
        elle yonetilebilir (kod degistirmeye/exe yeniden derlemeye gerek yok).
        Finder sekmesiyle AYNI thread+queue+poll kalibini kullanir."""
        self.rakip_event_queue = queue.Queue()
        self.rakip_running = False
        self.rakip_stop_event = None

        # Bu sekme digerlerinden daha uzun olabiliyor (satici tablosu +
        # ekleme formu + kesif ayarlari + log). Pencere yuksekligi ekrana
        # gore SABIT (bkz. _fit_to_widest_tab, resizable(False,False)) --
        # icerik tasinca alt kisim (butonlar/log) hic GORUNMUYORDU (canli
        # dogrulandi, 2026-09-20). Bu yuzden SADECE bu sekmeye dikey
        # kaydirma ekliyoruz -- digerlerine dokunulmadi.
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        canvas = tk.Canvas(parent, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        frm = ttk.Frame(canvas, padding=7)
        frm_window = canvas.create_window((0, 0), window=frm, anchor="nw")

        def _on_frm_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(frm_window, width=event.width)

        frm.bind("<Configure>", _on_frm_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        row = 0

        ttk.Label(
            frm,
            text=(
                "Bilinen rakip satıcıların Keepa'daki TÜM aktif ASIN listesini (storefront) çeker "
                "ve ortak havuza ekler -- bir rakip zaten satıyorsa, o ürünün gerçekten satılabilir "
                "olduğunun güçlü bir kanıtıdır."
            ),
            wraplength=780, justify="left", foreground="#444",
        ).grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 5))
        row += 1

        list_frame = ttk.LabelFrame(frm, text="Satıcı Listesi")
        list_frame.grid(row=row, column=0, columnspan=4, sticky="nsew", pady=(0, 5))
        frm.rowconfigure(row, weight=1)
        row += 1

        columns = ("isim", "seller_id", "domain", "durum")
        self.rakip_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=8)
        self.rakip_tree.heading("isim", text="İsim")
        self.rakip_tree.heading("seller_id", text="Seller ID")
        self.rakip_tree.heading("domain", text="Domain")
        self.rakip_tree.heading("durum", text="Durum")
        self.rakip_tree.column("isim", width=180)
        self.rakip_tree.column("seller_id", width=140)
        self.rakip_tree.column("domain", width=60, anchor="center")
        self.rakip_tree.column("durum", width=100, anchor="center")
        self.rakip_tree.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        list_frame.columnconfigure(0, weight=1)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.rakip_tree.yview)
        self.rakip_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=5)

        tree_btn_row = ttk.Frame(list_frame)
        tree_btn_row.grid(row=1, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 5))
        ttk.Button(tree_btn_row, text="Seçiliyi Sil", command=self._rakip_remove_selected).pack(side="left")
        ttk.Button(tree_btn_row, text="Yenile", command=self._rakip_refresh_tree).pack(side="left", padx=(6, 0))

        # 2026-09-20 GUNCELLEME (musteriden geldi): eskiden tum alanlar tek
        # satira sikistirilmisti -- hem anlam karmasasi hem yatay tasma
        # vardi. Artik HER alan kendi satirinda, kisa etiketle -- detay
        # aciklamalar '?' ipucuna tasindi, pencere asla yatayda tasmiyor.
        LABEL_WIDTH = 20

        add_frame = ttk.LabelFrame(frm, text="Yeni Rakip Satıcı Ekle")
        add_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        row += 1

        # 2026-09-20: "Isim" alani kaldirildi (musteriden geldi) -- artik
        # elle yazilmiyor, Ekle'ye basinca Keepa'dan (businessName/
        # sellerName) OTOMATIK cekiliyor (bkz. hs.fetch_seller_name).
        # 2026-09-20: Domain ayri bir satir DEGIL -- sadece bilgi amacli
        # (ust bardaki "Kaynak Pazar" secicisinden geliyor, burada elle
        # girilmiyor), Seller ID ile AYNI satirda gosteriliyor (musteriden
        # geldi -- kendi satirini hak edecek kadar onemli/etkilesimli degil).
        ttk.Label(add_frame, text="Seller ID:", width=LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, padx=5, pady=(5, 6), sticky="w"
        )
        self.rakip_new_seller_id_var = tk.StringVar()
        seller_id_row = ttk.Frame(add_frame)
        seller_id_row.grid(row=0, column=1, padx=5, pady=(5, 6), sticky="w")
        ttk.Entry(seller_id_row, textvariable=self.rakip_new_seller_id_var, width=18).pack(side="left")
        self.rakip_domain_info_label = ttk.Label(seller_id_row, text="", foreground="#555")
        self.rakip_domain_info_label.pack(side="left", padx=(10, 0))
        HelpTooltip(
            seller_id_row,
            "Seller ID'yi bulmak için: Amazon ürün sayfasında 'Other sellers on Amazon' / "
            "'Satan ve Gönderen' -> satıcının adına tıkla -> storefront sayfasında adres ülkesi "
            "TR ise Türk satıcıdır. Adres çubuğundaki '?seller=XXXXXXXXXXX' kısmı Seller ID'dir. "
            "İsim otomatik Keepa'dan çekilir, elle girmene gerek yok. Domain, sayfanın en "
            "üstündeki 'Kaynak Pazar' seçiciden gelir -- o pazarı değiştirirsen burası da "
            "otomatik güncellenir.",
        ).widget.pack(side="left", padx=(6, 0))

        ttk.Button(add_frame, text="Ekle", command=self._rakip_add_seller).grid(
            row=1, column=1, padx=5, pady=(0, 6), sticky="w"
        )

        discover_frame = ttk.LabelFrame(frm, text="Otomatik Rakip Keşfi (satıcı sayısına göre)")
        discover_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        row += 1

        ttk.Label(discover_frame, text="Satıcı sayısı:", width=LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, padx=5, pady=(5, 2), sticky="w"
        )
        range_row = ttk.Frame(discover_frame)
        range_row.grid(row=0, column=1, padx=5, pady=(5, 2), sticky="w")
        self.rakip_offer_min_var = tk.StringVar(value=str(hs.DISCOVERY_OFFER_COUNT_MIN))
        ttk.Entry(range_row, textvariable=self.rakip_offer_min_var, width=6).pack(side="left")
        ttk.Label(range_row, text=" - ").pack(side="left")
        self.rakip_offer_max_var = tk.StringVar(value=str(hs.DISCOVERY_OFFER_COUNT_MAX))
        ttk.Entry(range_row, textvariable=self.rakip_offer_max_var, width=6).pack(side="left")
        HelpTooltip(
            range_row,
            "'İşlenmemiş Satıcıları Çek' önce bu satıcı-sayısı aralığındaki ürünleri bulur -- ne çok "
            "rekabetsiz (talep şüphesi), ne çok kalabalık.",
        ).widget.pack(side="left", padx=(6, 0))

        ttk.Label(discover_frame, text="Hedef ülke kodu:", width=LABEL_WIDTH, anchor="w").grid(
            row=1, column=0, padx=5, pady=2, sticky="w"
        )
        country_row = ttk.Frame(discover_frame)
        country_row.grid(row=1, column=1, padx=5, pady=2, sticky="w")
        self.rakip_target_country_var = tk.StringVar(value=hs.DISCOVERY_TARGET_COUNTRY)
        ttk.Entry(country_row, textvariable=self.rakip_target_country_var, width=5).pack(side="left")
        HelpTooltip(
            country_row,
            "Bu ASIN'lerin GERÇEK canlı satıcılarına bakıp adres ülkesi bu kodla eşleşenleri YENİ "
            "rakip olarak listeye ekler -- bilinen bir satıcı listesine ihtiyaç duymadan kendi "
            "rakiplerini sıfırdan bulur.",
        ).widget.pack(side="left", padx=(6, 0))

        ttk.Label(discover_frame, text="Max yeni satıcı:", width=LABEL_WIDTH, anchor="w").grid(
            row=2, column=0, padx=5, pady=2, sticky="w"
        )
        max_sellers_row = ttk.Frame(discover_frame)
        max_sellers_row.grid(row=2, column=1, padx=5, pady=2, sticky="w")
        self.rakip_max_new_sellers_var = tk.StringVar(value="")
        ttk.Entry(max_sellers_row, textvariable=self.rakip_max_new_sellers_var, width=6).pack(side="left")
        ttk.Label(max_sellers_row, text="  (boş = sınırsız)", foreground="#888").pack(side="left")

        ttk.Label(discover_frame, text="ASIN/satıcı limiti:", width=LABEL_WIDTH, anchor="w").grid(
            row=3, column=0, padx=5, pady=(2, 5), sticky="w"
        )
        per_seller_row = ttk.Frame(discover_frame)
        per_seller_row.grid(row=3, column=1, padx=5, pady=(2, 5), sticky="w")
        self.rakip_per_seller_limit_var = tk.StringVar(value="")
        ttk.Entry(per_seller_row, textvariable=self.rakip_per_seller_limit_var, width=6).pack(side="left")
        ttk.Label(per_seller_row, text="  (boş = sınırsız)", foreground="#888").pack(side="left")
        HelpTooltip(
            per_seller_row,
            "İkisi de BOŞ bırakılırsa sınırsız (eski davranış). 'Max yeni satıcı': bir keşif turunda "
            "en fazla bu kadar yeni rakip eklenir (örn. 5). 'ASIN/satıcı limiti': bir satıcının TÜM "
            "listesi yerine (Sales Rank'e göre) en çok satan ilk N ASIN'i alınır -- bu, storefront "
            "listesini sıralamak için EK token harcar (~1 token/ASIN), ama havuza baştan zayıf ASIN "
            "doldurmaz.",
        ).widget.pack(side="left", padx=(6, 0))

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 5))
        row += 1
        self.rakip_start_button = ttk.Button(btn_row, text="İşlenmemiş Satıcıları Çek", command=self.rakip_start)
        self.rakip_start_button.pack(side="left")
        self.rakip_stop_button = ttk.Button(btn_row, text="Durdur", command=self.rakip_stop, state="disabled")
        self.rakip_stop_button.pack(side="left", padx=(6, 0))

        status_frame = ttk.LabelFrame(frm, text="Durum")
        status_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 3))
        row += 1
        self.rakip_status_label = ttk.Label(status_frame, text="Hazır.", font=("Segoe UI", 10, "bold"))
        self.rakip_status_label.grid(row=0, column=0, sticky="w", padx=8, pady=3, columnspan=3)
        self.rakip_progress = ttk.Progressbar(status_frame, length=400, mode="determinate")
        self.rakip_progress.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 3))

        ttk.Label(frm, text="Log:").grid(row=row, column=0, sticky="w")
        row += 1
        self.rakip_log_text = scrolledtext.ScrolledText(frm, height=5, width=95, state="disabled")
        self.rakip_log_text.grid(row=row, column=0, columnspan=4, pady=(0, 3))
        row += 1

        self._rakip_refresh_tree()
        self._rakip_update_domain_label()
        self.root.after(150, self._poll_rakip)

    def _rakip_update_domain_label(self):
        domain_str = self.source_market_domain_var.get()
        id_to_label = {str(dom): label for label, dom in KEEPA_MARKET_DOMAINS}
        label = id_to_label.get(domain_str, domain_str)
        self.rakip_domain_info_label.config(text=f"Domain: {domain_str} ({label}, Kaynak Pazar'dan)")

    def _rakip_refresh_tree(self):
        for item in self.rakip_tree.get_children():
            self.rakip_tree.delete(item)
        sellers = hs.load_sellers()
        already = hs.load_already_processed()
        for s in sellers:
            durum = "İşlendi" if s["seller_id"] in already else "Bekliyor"
            self.rakip_tree.insert("", "end", iid=s["seller_id"], values=(s["name"], s["seller_id"], s["domain"], durum))
        bekleyen = sum(1 for s in sellers if s["seller_id"] not in already)
        self.rakip_status_label.config(
            text=f"Toplam {len(sellers)} satıcı -- {len(sellers) - bekleyen} işlendi, {bekleyen} bekliyor."
        )

    def _rakip_add_seller(self):
        seller_id = self.rakip_new_seller_id_var.get().strip()
        if not seller_id:
            messagebox.showwarning("Eksik bilgi", "Seller ID boş bırakılamaz.")
            return
        api_key = keepa_api_settings.load_api_key()
        if not api_key:
            messagebox.showwarning(
                "Keepa API anahtarı eksik",
                "Satıcı eklemek için önce Ayarlar > Keepa API Ayarları... menüsünden kendi Keepa API anahtarını gir.",
            )
            return
        domain = int(self.source_market_domain_var.get())
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            name = hs.fetch_seller_name(api_key, seller_id, domain)
        except Exception as error:
            messagebox.showerror("Hata", f"Satıcı ismi Keepa'dan alınamadı: {error}")
            return
        finally:
            self.root.config(cursor="")
        if not name:
            messagebox.showwarning(
                "Satıcı bulunamadı",
                f"Keepa'da '{seller_id}' ID'li bir satıcı bulunamadı -- ID'yi ve seçili Kaynak "
                "Pazar'ı kontrol et.",
            )
            return
        _sellers, added = hs.add_seller(name, seller_id, domain)
        if not added:
            messagebox.showinfo("Zaten var", f"Bu Seller ID ({seller_id}) listede zaten kayıtlı.")
            return
        self.rakip_new_seller_id_var.set("")
        self._rakip_refresh_tree()

    def _rakip_remove_selected(self):
        selected = self.rakip_tree.selection()
        if not selected:
            return
        seller_id = selected[0]
        if not messagebox.askyesno("Satıcıyı sil", f"'{seller_id}' listeden silinsin mi?"):
            return
        hs.remove_seller(seller_id)
        self._rakip_refresh_tree()

    def rakip_log(self, message):
        self.rakip_log_text.configure(state="normal")
        self.rakip_log_text.insert("end", message + "\n")
        self.rakip_log_text.see("end")
        self.rakip_log_text.configure(state="disabled")

    def rakip_start(self):
        if self.rakip_running:
            return
        api_key = keepa_api_settings.load_api_key()
        if not api_key:
            messagebox.showwarning(
                "Keepa API anahtarı eksik",
                "Rakip Kopyala için önce Ayarlar > Keepa API Ayarları... menüsünden kendi Keepa API anahtarını gir.",
            )
            return
        try:
            offer_min = int(self.rakip_offer_min_var.get())
            offer_max = int(self.rakip_offer_max_var.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("Geçersiz değer", "Satıcı sayısı min/max alanları sayı olmalı.")
            return
        if offer_min <= 0 or offer_max < offer_min:
            messagebox.showwarning("Geçersiz aralık", "Satıcı sayısı max, min'den küçük olamaz.")
            return
        target_country = self.rakip_target_country_var.get().strip().upper()
        if len(target_country) != 2:
            messagebox.showwarning("Geçersiz ülke kodu", "Hedef ülke kodu 2 harfli olmalı (örn. TR).")
            return
        domain = int(self.source_market_domain_var.get())

        def parse_optional_int(var, field_label):
            raw = var.get().strip()
            if not raw:
                return None, True
            try:
                value = int(raw)
            except ValueError:
                messagebox.showwarning("Geçersiz değer", f"{field_label} boş ya da sayı olmalı.")
                return None, False
            if value <= 0:
                messagebox.showwarning("Geçersiz değer", f"{field_label} pozitif bir sayı olmalı.")
                return None, False
            return value, True

        max_new_sellers, ok1 = parse_optional_int(self.rakip_max_new_sellers_var, "Max yeni satıcı")
        if not ok1:
            return
        per_seller_limit, ok2 = parse_optional_int(self.rakip_per_seller_limit_var, "Satıcı başına ASIN limiti")
        if not ok2:
            return

        self.rakip_running = True
        self.rakip_stop_event = threading.Event()
        self.rakip_start_button.config(state="disabled")
        self.rakip_stop_button.config(state="normal")
        self.rakip_log_text.configure(state="normal")
        self.rakip_log_text.delete("1.0", "end")
        self.rakip_log_text.configure(state="disabled")

        keepa_sync_pool = BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt"
        shared_pool_path = keepa_sync_pool if keepa_sync_pool.parent.exists() else None

        thread = threading.Thread(
            target=self._rakip_run,
            args=(
                api_key, shared_pool_path, offer_min, offer_max, target_country, domain,
                max_new_sellers, per_seller_limit,
            ),
            daemon=True,
        )
        thread.start()

    def _rakip_run(
        self, api_key, shared_pool_path, offer_min, offer_max, target_country, domain,
        max_new_sellers, per_seller_limit,
    ):
        def progress_callback(kind, payload):
            self.rakip_event_queue.put((kind, payload))

        try:
            hs.run_discovery_and_harvest(
                api_key, offer_count_min=offer_min, offer_count_max=offer_max,
                target_country=target_country, domain=domain,
                max_new_sellers=max_new_sellers, per_seller_asin_limit=per_seller_limit,
                progress=progress_callback, stop_event=self.rakip_stop_event,
                shared_pool_path=shared_pool_path,
            )
        except Exception as error:
            self.rakip_event_queue.put(("fatal", {"message": f"{type(error).__name__}: {error}"}))
        self.rakip_event_queue.put(("done", {}))

    def rakip_stop(self):
        if not self.rakip_running or self.rakip_stop_event is None:
            return
        self.rakip_stop_event.set()
        self.rakip_stop_button.config(state="disabled")
        self.rakip_log(">>> Durdurma istendi -- şu an işlenen satıcı bitince güvenli şekilde duracak.")

    def _poll_rakip(self):
        try:
            while True:
                kind, payload = self.rakip_event_queue.get_nowait()
                message = payload.get("message", "")
                if kind in ("log", "start"):
                    if message:
                        self.rakip_log(message)
                        # Durum etiketi SADECE isin sonunda/aralarinda degil,
                        # HER ADIMDA guncellensin (musteriden geldi -- "ne
                        # islem yapiliyorsa" gorulsun): tek satirlik ozet.
                        self.rakip_status_label.config(text=message.strip().lstrip(">").strip())
                    if kind == "start":
                        total = payload.get("total") or 0
                        self.rakip_progress.config(maximum=max(1, total), value=0)
                elif kind == "seller_done":
                    islenen = payload.get("islenen", 0)
                    total = payload.get("total", 0)
                    self.rakip_progress.config(value=islenen)
                    self.rakip_status_label.config(
                        text=f"{islenen}/{total} satıcı işlendi -- ortak havuz: {payload.get('toplam_havuz')} "
                             f"ASIN (bu oturumda +{payload.get('yeni_asin_toplam')})"
                    )
                elif kind == "discover_complete":
                    self.rakip_log(message)
                    self.rakip_status_label.config(text=message.strip().lstrip(">").strip())
                    self._rakip_refresh_tree()
                elif kind in ("complete", "stopped"):
                    self.rakip_log(f">>> {message}")
                    self._rakip_refresh_tree()
                elif kind == "fatal":
                    messagebox.showerror("Hata", message)
                elif kind == "done":
                    self.rakip_running = False
                    self.rakip_start_button.config(state="normal")
                    self.rakip_stop_button.config(state="disabled")
                    self.rakip_log(">>> Durdu / tamamlandı.")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_rakip)

    # -------------------------------------------------------------- send tab
    def _build_send_tab(self, parent):
        frm = ttk.Frame(parent, padding=7)
        frm.grid(row=0, column=0, sticky="nsew")
        row = 0

        ttk.Label(
            frm,
            text=(
                "Bu sekme, 'Temizle' sekmesinde upload (temiz) çıkan ASIN'leri hedef platforma "
                "(EasyCentral) gönderir. Kota harcayan son 'Taramayı Başlat' tıkını bilerek sana bırakıyoruz."
            ),
            foreground="#555", wraplength=760, justify="left",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 6))
        row += 1

        ttk.Label(frm, text="Hedef Platform:").grid(row=row, column=0, sticky="w")
        self.send_target_var = tk.StringVar(value=next(iter(UPLOAD_TARGETS)))
        ttk.Combobox(
            frm, textvariable=self.send_target_var, values=list(UPLOAD_TARGETS.keys()),
            state="readonly", width=25,
        ).grid(row=row, column=1, sticky="w", padx=5)
        row += 1

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Button(btn_row, text="Temizden Yükle (upload/temiz liste)", command=self.send_load_from_check).pack(
            side="left"
        )
        ttk.Button(btn_row, text="Dosyadan Yükle...", command=self.send_load_from_file).pack(
            side="left", padx=(8, 0)
        )
        row += 1

        self.send_asin_text = scrolledtext.ScrolledText(frm, height=8, width=95)
        self.send_asin_text.grid(row=row, column=0, columnspan=3, pady=(0, 3))
        row += 1

        self.send_count_label = ttk.Label(frm, text="0 ASIN")
        self.send_count_label.grid(row=row, column=0, sticky="w")
        row += 1

        sku_row = ttk.Frame(frm)
        sku_row.grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(sku_row, text="Stok Kodu (JP-):").pack(side="left")
        self.send_sku_code_var = tk.StringVar(value="")
        ttk.Entry(sku_row, textvariable=self.send_sku_code_var, width=10).pack(side="left", padx=(4, 0))
        HelpTooltip(
            sku_row,
            "EasyCentral'daki 'Stok Kodu' alanına yazılır (ör. S1HOT, KATEG, 00003). "
            "Bu partinin hangi arama yönteminden geldiğini SKU üzerinden işaretlemek için "
            "kullanılır -- ileride hangi SKU'nun daha çok sattığına bakıp aynı yöntemi tekrar "
            "kullanabilirsin. Boş bırakılırsa EasyCentral kendi rastgele kodunu kullanır.",
        ).widget.pack(side="left", padx=(4, 0))
        row += 1

        self.send_auto_upload_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm,
            text="Taramada uygun çıkanları mağazaya otomatik yükle (Önerilmez)",
            variable=self.send_auto_upload_var,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 0))
        row += 1
        ttk.Label(
            frm,
            text=(
                "Bu işaretlenirse, tarama bitince UYGUN çıkan ürünler SENIN gözden geçirmen "
                "beklenmeden doğrudan Amazon mağazana gönderilir (EasyCentral'ın kendi \"Amazon "
                "Mağazama Otomatik Yükle\" seçeneği) -- geri alınamaz. İşaretlemezsen tarama sadece "
                "başlar, mağazaya gönderimi EasyCentral sayfasında elle onaylarsın."
            ),
            foreground="#b70", wraplength=760, justify="left",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 4))
        row += 1

        action_row = ttk.Frame(frm)
        action_row.grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Button(action_row, text="1) Chrome'u Aç ve Hedef Sayfaya Git", command=self.send_open_target).pack(
            side="left"
        )
        ttk.Button(action_row, text="2) ASIN'leri Sayfaya Yapıştır", command=self.send_paste_asins).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            action_row, text="3) Gönder ve Taramayı Başlat (tek adım)", command=self.send_and_start_scan
        ).pack(side="left", padx=(8, 0))
        row += 1

        self.send_status_label = ttk.Label(frm, text="", foreground="#555", wraplength=760, justify="left")
        self.send_status_label.grid(row=row, column=0, columnspan=3, sticky="w")

    def send_load_from_check(self):
        path = self.upload_txt_path()
        if not path.exists():
            messagebox.showwarning(
                "Bulunamadı", f"{path} henüz yok -- önce 'Temizle' sekmesinde bir kontrol tamamla."
            )
            return
        text = path.read_text(encoding="utf-8")
        self.send_asin_text.delete("1.0", "end")
        self.send_asin_text.insert("1.0", text)
        self._send_update_count()
        self.send_status_label.config(text="Temizle sekmesinin kontrolden geçmiş listesi yüklendi.", foreground="#0a5")

    def send_load_from_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8")
        self.send_asin_text.delete("1.0", "end")
        self.send_asin_text.insert("1.0", text)
        self._send_update_count()
        # Bu liste Temizle sekmesinden (yani Keepa kesinti/telef/yil kontrolunden)
        # gecmemis olabilir -- dogrudan ham bir dosyadan yuklendigi icin
        # kullaniciyi uyariyoruz. Ham (kontrolsuz) ASIN'leri EasyCentral'a
        # gondermek, "satistan kaldirilmis" gibi Keepa'nin zaten yakalayabilecegi
        # olu urunler yuzunden kotayi bosuna harcatir.
        self.send_status_label.config(
            text=(
                "Uyarı: Bu liste dosyadan yüklendi -- 'Temizle' sekmesinden geçmemiş olabilir. "
                "Önce Temizle sekmesinde kontrol etmen, EasyCentral'ın kotasını gereksiz yere "
                "harcamamak için önerilir."
            ),
            foreground="#b70",
        )

    def _send_update_count(self):
        asins = self._send_get_asins()
        self.send_count_label.config(text=f"{len(asins)} ASIN")

    def _send_get_asins(self):
        text = self.send_asin_text.get("1.0", "end")
        asins = []
        for raw_line in text.splitlines():
            match = ASIN_PATTERN.search(raw_line.upper())
            if match:
                asins.append(match.group(0))
        return asins

    def send_open_target(self):
        target = UPLOAD_TARGETS.get(self.send_target_var.get())
        if target is None:
            return

        def do_open():
            try:
                start_chrome_for_attachment(start_url="about:blank", headless=False)
                target["open_target"](DEBUG_ADDRESS)
                self.event_queue.put(("send_status", "Hedef sayfa açıldı. Giriş yapılı değilsen giriş yap."))
            except Exception as error:
                self.event_queue.put(("send_status", f"Sayfa açılamadı: {error}"))

        threading.Thread(target=do_open, daemon=True).start()

    def send_paste_asins(self):
        asins = self._send_get_asins()
        if not asins:
            messagebox.showwarning("Liste boş", "Önce ASIN listesini yükle/yapıştır.")
            return
        target = UPLOAD_TARGETS.get(self.send_target_var.get())
        if target is None:
            return

        sku_code = self.send_sku_code_var.get().strip() or None

        def do_paste():
            try:
                result = target["paste_asins"](DEBUG_ADDRESS, asins, sku_code=sku_code)
                self.event_queue.put(("send_status", result.get("message", "")))
            except Exception as error:
                self.event_queue.put(("send_status", f"Yapıştırma başarısız: {error}"))

        threading.Thread(target=do_paste, daemon=True).start()

    def send_and_start_scan(self):
        asins = self._send_get_asins()
        if not asins:
            messagebox.showwarning("Liste boş", "Önce ASIN listesini yükle/yapıştır.")
            return
        target = UPLOAD_TARGETS.get(self.send_target_var.get())
        if target is None or "submit_and_start_scan" not in target:
            messagebox.showerror("Desteklenmiyor", "Bu hedef platform tek-adım gönderimi desteklemiyor.")
            return
        auto_upload = self.send_auto_upload_var.get()
        # Bu adim EasyCentral'in gunluk kotasini harcar ve GERI ALINAMAZ --
        # otomatik akis hizli olsun diye kurulsa da, yanlislikla tikla(n)ip
        # kota bosa gitmesin diye burada TEK bir onay birakiyoruz. Magazaya
        # otomatik yukleme ISARETLIYSE ekstra/daha sert bir uyari veriyoruz --
        # bu, gozden gecirme adimini TAMAMEN atlayip urunleri dogrudan canli
        # magazaya gonderen, GERCEKTEN geri alinamaz bir islem.
        if auto_upload:
            confirmed = messagebox.askyesno(
                "MAĞAZAYA OTOMATİK GÖNDERİM -- ONAYLA",
                f"{len(asins)} ASIN taranacak VE tarama bitince UYGUN çıkanlar SENİN gözden "
                f"geçirmen beklenmeden DOĞRUDAN Amazon mağazana gönderilecek.\n\n"
                f"Bu adım GERİ ALINAMAZ ve EasyCentral'ın kendisi bu seçeneği \"Önerilmez\" "
                f"olarak işaretliyor.\n\nYine de devam edilsin mi?",
                icon="warning",
            )
        else:
            confirmed = messagebox.askyesno(
                "Onayla",
                f"{len(asins)} ASIN, Chrome'da sayfa açılıp mağaza kontrolü yapıldıktan hemen sonra "
                f"OTOMATİK olarak gönderilip taramayı başlatacak (EasyCentral kotasını harcar, geri alınamaz).\n\n"
                f"Devam edilsin mi?",
            )
        if not confirmed:
            return

        self.send_status_label.config(text="Chrome açılıyor, sayfa yükleniyor, gönderiliyor...", foreground="#555")

        sku_code = self.send_sku_code_var.get().strip() or None

        def do_submit():
            try:
                start_chrome_for_attachment(start_url="about:blank", headless=False)
                result = target["submit_and_start_scan"](
                    DEBUG_ADDRESS, asins, auto_upload_to_store=auto_upload, sku_code=sku_code
                )
                self.event_queue.put(("send_status", result.get("message", "")))
            except Exception as error:
                self.event_queue.put(("send_status", f"Gönderim başarısız: {error}"))

        threading.Thread(target=do_submit, daemon=True).start()

    # ------------------------------------------------------------------- ui
    def _build_ui(self, parent):
        frm = ttk.Frame(parent, padding=7)
        frm.grid(row=0, column=0, sticky="nsew")
        row = 0

        self.asin_count_label = ttk.Label(frm, text="0 ASIN yüklendi (Dosya menüsünden listeyi yükle)")
        self.asin_count_label.grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1

        options_frame = ttk.LabelFrame(frm, text="Kontrol Seçenekleri")
        options_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(5, 5))
        self.require_year_var = tk.BooleanVar(value=True)
        self.check_gaps_var = tk.BooleanVar(value=True)
        self.check_dead_stock_var = tk.BooleanVar(value=False)
        self.screenshot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="1 yıllık veri şartı", variable=self.require_year_var).grid(
            row=0, column=0, padx=8, pady=3, sticky="w"
        )
        ttk.Checkbutton(options_frame, text="Kesinti kontrolü", variable=self.check_gaps_var).grid(
            row=0, column=1, padx=8, pady=3, sticky="w"
        )
        ttk.Checkbutton(
            options_frame, text="Telef şüphesi kontrolü (satıcı düşüşü)", variable=self.check_dead_stock_var
        ).grid(row=0, column=2, padx=8, pady=3, sticky="w")
        self.check_customs_risk_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame,
            text="JP gümrük riski kontrolü (pil/kablosuz/sıvı/bıçak/deri vb. başlıkları ele)",
            variable=self.check_customs_risk_var,
        ).grid(row=0, column=3, padx=8, pady=3, sticky="w")
        # DROPSHIPPING MANTIGI (kullanicidan geldi, ozetle): kaynak (JP,
        # Amazon.co.jp) sadece LISTELEME icin -- stok TUTULMUYOR. Siparis
        # gelince urun HEDEF pazardan (asagida secilen ulke, ornek: ABD icin
        # Amazon.com) satin alinip musteriye gonderiliyor. Yani ASIN'in
        # hedef pazarda GERCEKTEN var olmasi hayati -- yoksa siparisi
        # karsilayacak/kar hesabi yapacak bir kaynak yok demektir. Bu kontrol
        # tam olarak bunu -- EasyCentral'a gondermeden once -- dogruluyor.
        # (Ic test detaylari musteriye gosterilmiyor, sadece is mantigi.)
        self.check_target_market_var = tk.BooleanVar(value=True)
        target_market_cell = ttk.Frame(options_frame)
        target_market_cell.grid(row=1, column=0, columnspan=2, padx=8, pady=(0, 4), sticky="w")
        self.check_target_market_checkbox = ttk.Checkbutton(
            target_market_cell, text="Hedef pazarda bulunabilirlik kontrolü",
            variable=self.check_target_market_var,
        )
        self.check_target_market_checkbox.pack(side="left")
        HelpTooltip(
            target_market_cell,
            "Dropshipping'de stok tutmuyorsun -- sipariş gelince ürünü hedef pazardan (aşağıda "
            "seçtiğin ülke) satın alıp müşteriye gönderiyorsun. Yani ASIN'in o ülkede GERÇEKTEN "
            "satılıyor olması şart -- yoksa siparişi karşılayamaz, kâr hesabı yapamazsın.\n\n"
            "Bu kontrol, EasyCentral'a göndermeden önce ürünün seçtiğin ülkede bulunup "
            "bulunmadığını kontrol eder -- EasyCentral'ın \"Satıştan kaldırılmış\" diye elediği "
            "ürünlerin çoğunu daha en baştan eler, günlük tarama hakkını boşa harcatmaz. Ekstra "
            "bir Keepa API çağrısı (ekstra token) gerektirir.\n\nÖNEMLİ: SADECE hedef ülken de "
            "bir Amazon pazarıysa işe yarar -- Keepa yalnızca Amazon'u izliyor, eBay vb. "
            "Amazon-dışı bir hedefte bu kontrolün hiçbir anlamı yok, kapalı bırak.",
        ).widget.pack(side="left", padx=(4, 0))
        # Kutu DUZENLENEBILIR (readonly degil) -- listede olmayan bir ID'yi
        # de elle yazabilir. Liste ve dogrulama notu: bkz. modul basi
        # KEEPA_MARKET_DOMAINS.
        self._target_market_label_to_id = {label: str(dom) for label, dom in KEEPA_MARKET_DOMAINS}
        self._target_market_id_to_label = {str(dom): label for label, dom in KEEPA_MARKET_DOMAINS}
        domain_cell = ttk.Frame(options_frame)
        domain_cell.grid(row=1, column=2, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(domain_cell, text="Hedef ülke/mağaza:").pack(side="left")
        self.target_market_domain_var = tk.StringVar(value="1")
        self.target_market_domain_display_var = tk.StringVar(value=KEEPA_MARKET_DOMAINS[0][0])

        def _on_target_market_label_change(_event=None):
            label = self.target_market_domain_display_var.get()
            if label in self._target_market_label_to_id:
                self.target_market_domain_var.set(self._target_market_label_to_id[label])
            else:
                # Listede olmayan bir sey yazdiysa (elle ID girdiyse) oldugu
                # gibi birak -- start_check zaten sayi oldugunu dogruluyor.
                self.target_market_domain_var.set(label.strip())

        self.target_market_domain_entry = ttk.Combobox(
            domain_cell, textvariable=self.target_market_domain_display_var,
            values=[label for label, _ in KEEPA_MARKET_DOMAINS], width=26,
        )
        self.target_market_domain_entry.pack(side="left", padx=(4, 0))
        self.target_market_domain_entry.bind("<<ComboboxSelected>>", _on_target_market_label_change)
        self.target_market_domain_entry.bind("<FocusOut>", _on_target_market_label_change)
        HelpTooltip(
            domain_cell,
            "Sipariş gelince ürünü hangi ülkeden satın alıp müşteriye göndereceğini seç -- "
            "senin dropshipping hedef pazarın. Emin değilsen listeden en yakın olanı seç ya da "
            "kutuya doğrudan Keepa domain ID'sini yazabilirsin (Keepa'nın Product Finder "
            "ekranındaki ülke seçiciden bulabilirsin).",
        ).widget.pack(side="left", padx=(4, 0))
        self.screenshot_checkbox = ttk.Checkbutton(
            options_frame, text="Ekran görüntüsü kaydet", variable=self.screenshot_var
        )
        self.screenshot_checkbox.grid(row=2, column=0, padx=8, pady=(0, 4), sticky="w")
        parallel_cell = ttk.Frame(options_frame)
        parallel_cell.grid(row=2, column=1, columnspan=2, sticky="w", pady=(0, 4))
        self.parallel_label = ttk.Label(parallel_cell, text="Paralel pencere:")
        self.parallel_label.pack(side="left")
        self.parallel_var = tk.IntVar(value=4)
        self.parallel_spinbox = ttk.Spinbox(parallel_cell, from_=1, to=8, width=5, textvariable=self.parallel_var)
        self.parallel_spinbox.pack(side="left", padx=(4, 0))
        HelpTooltip(
            parallel_cell,
            "Chrome modunda aynı anda kaç sekme/pencere açılıp paralel kontrol edileceğini "
            "belirler -- daha fazlası daha hızlı ama bilgisayarı daha çok yorar. API Modu'nda "
            "hiç Chrome açılmadığı için bu ayarın bir anlamı yok, o modda otomatik devre dışı "
            "kalır.",
        ).widget.pack(side="left", padx=(4, 0))
        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame,
            text="Daha önce taranmışları atla (kaldığı yerden devam et)",
            variable=self.resume_var,
        ).grid(row=3, column=0, columnspan=3, padx=8, pady=(0, 4), sticky="w")
        self.api_mode_var = tk.BooleanVar(value=False)
        api_mode_cell = ttk.Frame(options_frame)
        api_mode_cell.grid(row=4, column=0, columnspan=3, padx=8, pady=(0, 4), sticky="w")
        self.api_mode_checkbox = ttk.Checkbutton(
            api_mode_cell, text="API Modu (Chrome gerektirmez, Keepa API anahtarı ile hızlı çalışır)",
            variable=self.api_mode_var, command=self._on_api_mode_toggle,
        )
        self.api_mode_checkbox.pack(side="left")
        HelpTooltip(
            api_mode_cell,
            "Açıksan: kontrol doğrudan Keepa'nın API'siyle yapılır -- Chrome açılmaz, çok daha "
            "hızlıdır, ama ekran görüntüsü alınamaz (o seçenek bu modda otomatik devre dışı "
            "kalır). Kapalıysa: Chrome üzerinden, Keepa'nın grafiğini okuyarak çalışır, daha "
            "yavaştır ama ekran görüntüsü alabilir. Her iki mod için de kendi Keepa API "
            "anahtarını Ayarlar > Keepa API Ayarları'ndan girmen gerekir.",
        ).widget.pack(side="left", padx=(4, 0))
        row += 1

        self._update_api_dependent_controls()

        self.start_button = ttk.Button(frm, text="Kontrolü Başlat", command=self.start_check)
        self.start_button.grid(row=row, column=0, pady=6, sticky="w")
        self.stop_button = ttk.Button(frm, text="Durdur", command=self.stop_check, state="disabled")
        self.stop_button.grid(row=row, column=1, pady=6, sticky="w", padx=(5, 10))
        self.progress = ttk.Progressbar(frm, length=250, mode="determinate")
        self.progress.grid(row=row, column=2, sticky="ew", padx=10)
        self.progress_count_label = ttk.Label(frm, text="0 / 0")
        self.progress_count_label.grid(row=row, column=3, sticky="w")
        row += 1

        self.active_label = ttk.Label(frm, text="Beklemede", foreground="#888")
        self.active_label.grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 3))
        row += 1

        self.eta_label = ttk.Label(frm, text="", foreground="#555")
        self.eta_label.grid(row=row, column=0, columnspan=4, sticky="w", pady=(0, 3))
        row += 1

        ttk.Label(frm, text="Log:").grid(row=row, column=0, sticky="w")
        row += 1
        self.log_text = scrolledtext.ScrolledText(frm, height=5, width=95, state="disabled")
        self.log_text.grid(row=row, column=0, columnspan=4, pady=(0, 6))
        row += 1

        error_frame = ttk.LabelFrame(frm, text="Hatalar")
        error_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        self.error_text = scrolledtext.ScrolledText(
            error_frame, height=3, width=95, state="disabled", foreground="#b00"
        )
        self.error_text.pack(fill="both", expand=True)
        row += 1

        self.summary_label = ttk.Label(frm, text="", font=("Segoe UI", 10, "bold"))
        self.summary_label.grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1

        footer = ttk.Frame(frm)
        footer.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Separator(footer, orient="horizontal").pack(fill="x", pady=(0, 3))
        ttk.Label(
            footer, text=license_guard.COPYRIGHT_NOTICE, foreground="#666", font=("Segoe UI", 8),
        ).pack(anchor="w")
        self.license_status_label = ttk.Label(footer, text="", font=("Segoe UI", 8, "bold"))
        self.license_status_label.pack(anchor="w", pady=(3, 0))
        self.update_license_status()

    def _on_api_mode_toggle(self):
        """API Modu'nda ekran goruntusu ALINAMAZ (kod zaten bunu sessizce
        yok sayiyordu: take_screenshot = screenshot_var and not api_mode) ve
        "Paralel pencere" Chrome'a ozgu bir kavram (API Modu'nda hic
        pencere/sekme acilmiyor) -- ama ikisi de tiklanabilir/degistirilebilir
        GORUNUYORDU, bu da musteriye yanlis bir sey ayarlamis hissi
        veriyordu (kullanicidan geldi, dogrulandi). Simdi API Modu'nda bu
        secenekler gorsel olarak da devre disi birakiliyor, boylece ne
        oldugu acik."""
        if self.api_mode_var.get():
            self.screenshot_checkbox.state(["disabled"])
            self.parallel_spinbox.state(["disabled"])
            self.parallel_label.state(["disabled"])
        else:
            self.screenshot_checkbox.state(["!disabled"])
            self.parallel_spinbox.state(["!disabled"])
            self.parallel_label.state(["!disabled"])

    def _update_api_dependent_controls(self):
        """API Modu ve Hedef Pazar Kontrolu, IKISI de musterinin KENDI Keepa
        API anahtarini gerektiriyor. Anahtar hic girilmemisse bu iki
        secenegi (ve hedef domain alanini) gorsel olarak devre disi
        birakiyoruz -- eskiden sadece 'Baslat'a basinca bir uyari penceresi
        cikiyordu, musteri once neden calismadigini anlayamiyordu
        (kullanicidan geldi, dogrulandi: 'ne yaparsam aktif olacagi belli
        degil'). Ayarlar > Keepa API Ayarlari'ndan anahtar kaydedilince
        (bkz. show_api_settings_dialog/do_save) bu fonksiyon tekrar
        cagrilir, secenekler otomatik aktif olur."""
        has_key = keepa_api_settings.has_api_key()
        widgets = [self.check_target_market_checkbox, self.target_market_domain_entry, self.api_mode_checkbox]
        if has_key:
            for widget in widgets:
                widget.state(["!disabled"])
        else:
            for widget in widgets:
                widget.state(["disabled"])
            self.check_target_market_var.set(False)
            self.api_mode_var.set(False)
        self._on_api_mode_toggle()

    # -------------------------------------------------------------- dialogs
    def show_help(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Ne İşe Yarar?")
        dialog.geometry("680x560")
        text = scrolledtext.ScrolledText(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")
        ttk.Button(dialog, text="Kapat", command=dialog.destroy).pack(pady=(0, 10))

    def show_about(self):
        messagebox.showinfo(
            "Hakkında",
            f"{license_guard.PROGRAM_NAME} v{license_guard.VERSION}\n\n{license_guard.COPYRIGHT_NOTICE}\n\n{license_guard.status_text()}",
        )

    def show_contact(self):
        messagebox.showinfo("İletişim", license_guard.CONTACT_PLACEHOLDER)

    def show_stats(self):
        avg = scan_stats.all_time_average_seconds()
        count = scan_stats.all_time_total_count()
        total_seconds = scan_stats.all_time_total_seconds()
        avg_text = f"{avg:.1f} sn/ASIN" if avg is not None else "henüz veri yok"
        messagebox.showinfo(
            "İstatistikler (bu bilgisayar)",
            f"Bugüne kadar taranan ASIN: {count}\n"
            f"Ortalama hız: {avg_text}\n"
            f"Toplam tarama süresi: {scan_stats.format_duration(total_seconds)}\n\n"
            "Bu istatistikler bu bilgisayara özeldir; her PC kendi hızına göre "
            "kendi tahminini üretir.",
        )

    def apply_view_mode(self):
        compact = self.view_mode_var.get() == "compact"
        self.log_text.configure(height=4 if compact else 5)
        self.error_text.configure(height=2 if compact else 3)
        self.progress.configure(length=220 if compact else 350)
        self.root.update_idletasks()
        self.root.geometry("")

    def show_account_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Keepa Hesabı")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="Doğru sonuç için Keepa hesabına giriş yapman önerilir\n(bkz. Yardım menüsündeki açıklama).",
            justify="center", padding=(20, 15, 20, 5),
        ).pack()

        form = ttk.Frame(dialog, padding=20)
        form.pack()
        ttk.Label(form, text="Kullanıcı adı / e-posta:").grid(row=0, column=0, sticky="w", pady=5)
        username = keepa_credentials.load_username()
        user_var = tk.StringVar(value=username)
        ttk.Entry(form, textvariable=user_var, width=30).grid(row=0, column=1, pady=5)

        ttk.Label(form, text="Şifre:").grid(row=1, column=0, sticky="w", pady=5)
        pass_var = tk.StringVar(value=keepa_credentials.load_password(username))
        ttk.Entry(form, textvariable=pass_var, width=30, show="*").grid(row=1, column=1, pady=5)

        def do_save_and_login():
            new_username = user_var.get().strip()
            new_password = pass_var.get()
            if not new_username or not new_password:
                messagebox.showwarning("Eksik bilgi", "Kullanıcı adı ve şifre gerekli.", parent=dialog)
                return
            keepa_credentials.save_credentials(new_username, new_password)
            dialog.destroy()
            self._login_keepa(new_username, new_password)

        ttk.Button(dialog, text="Kaydet ve Keepa'ya Giriş Yap", command=do_save_and_login).pack(pady=(0, 15))

    def show_api_settings_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Keepa API Ayarları")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog, text="API Modu için kendi Keepa API anahtarını gir.",
            font=("Segoe UI", 10, "bold"),
        ).pack(padx=20, pady=(15, 5))

        info_text = scrolledtext.ScrolledText(dialog, width=62, height=8, wrap="word")
        info_text.pack(padx=20, pady=(0, 10))
        info_text.insert("1.0", keepa_api_settings.API_KEY_INSTRUCTIONS)
        info_text.configure(state="disabled")

        form = ttk.Frame(dialog, padding=(20, 0))
        form.pack(fill="x")
        ttk.Label(form, text="API Anahtarı:").grid(row=0, column=0, sticky="w")
        key_var = tk.StringVar(value=keepa_api_settings.load_api_key() or "")
        ttk.Entry(form, textvariable=key_var, width=50, show="*").grid(row=0, column=1, padx=8, sticky="ew")

        status_label = ttk.Label(dialog, text="", justify="center", wraplength=520)
        status_label.pack(padx=20, pady=(10, 0))

        def do_save():
            key = key_var.get().strip()
            if not key:
                messagebox.showwarning("Eksik bilgi", "API anahtarı boş olamaz.", parent=dialog)
                return
            try:
                keepa_api_settings.save_api_key(key)
                status_label.config(text="Kaydedildi.", foreground="#0a5")
                self._update_api_dependent_controls()
            except Exception as error:
                messagebox.showerror("Hata", f"Anahtar kaydedilemedi: {error}", parent=dialog)

        def do_check():
            key = key_var.get().strip()
            if not key:
                messagebox.showwarning("Eksik bilgi", "Önce bir API anahtarı gir.", parent=dialog)
                return
            status_label.config(text="Kontrol ediliyor...", foreground="#555")
            dialog.update_idletasks()
            try:
                data = keepa_api_settings.check_account_status(key)
                status_label.config(
                    text=(
                        f"Bağlantı başarılı — Token yenilenme hızı: {data.get('refillRate')} /dk  |  "
                        f"Şu an kalan token: {data.get('tokensLeft')}  |  "
                        f"(Bu kontrol token harcamaz.)"
                    ),
                    foreground="#0a5",
                )
            except Exception as error:
                status_label.config(text=f"Bağlantı başarısız: {error}", foreground="#b00")

        btn_row = ttk.Frame(dialog)
        btn_row.pack(pady=15)
        ttk.Button(btn_row, text="Kaydet", command=do_save).grid(row=0, column=0, padx=5)
        ttk.Button(btn_row, text="Hesabımı Kontrol Et", command=do_check).grid(row=0, column=1, padx=5)
        ttk.Button(btn_row, text="Kapat", command=dialog.destroy).grid(row=0, column=2, padx=5)

    def _login_keepa(self, username, password):
        def do_login():
            try:
                start_chrome_for_attachment(start_url="about:blank", headless=False)
                status = attempt_keepa_login(DEBUG_ADDRESS, username, password)
            except Exception as error:
                self.event_queue.put(("fatal_error", f"Giriş denemesi başarısız: {error}"))
                return
            messages = {
                "submitted": (
                    "Giriş bilgileri gönderildi. Açılan (görünür) Chrome penceresinde "
                    "giriş yapıldığını doğrula; sorun varsa aynı pencerede elle "
                    "tamamlayabilirsin. Bitince pencereyi kapatabilirsin."
                ),
                "submitted_awaiting_otp": (
                    "Giriş bilgileri gönderildi ama hesabında 2 adımlı doğrulama "
                    "(OTP) açık görünüyor. Açılan Chrome penceresinde kodu gir."
                ),
                "form_not_found": (
                    "Giriş formu otomatik doldurulamadı (Keepa sayfası değişmiş "
                    "olabilir). Açılan Chrome penceresinden elle giriş yapabilirsin."
                ),
            }
            self.event_queue.put(("info", messages.get(status, "Giriş penceresi açıldı.")))

        threading.Thread(target=do_login, daemon=True).start()

    def update_license_status(self):
        self.license_status_label.config(text=license_guard.status_text())

    def show_license_dialog(self):
        machine_id = license_guard.get_machine_id()
        dialog = tk.Toplevel(self.root)
        dialog.title("Lisans Anahtarı")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(
            dialog, text=f"Deneme süresi sona erdi ({license_guard.TRIAL_LIMIT} temiz ASIN).",
            font=("Segoe UI", 10, "bold"),
        ).pack(padx=20, pady=(15, 5))
        contact_line = license_guard.OWNER_CONTACT or license_guard.CONTACT_PLACEHOLDER
        tk.Label(
            dialog,
            text=(
                f"Bu makine kimliğini ilet: {contact_line}\n"
                "Karşılığında bir lisans anahtarı alacaksın."
            ),
            justify="center", padx=20,
        ).pack()
        id_entry = ttk.Entry(dialog, width=40, justify="center")
        id_entry.insert(0, machine_id)
        id_entry.configure(state="readonly")
        id_entry.pack(padx=20, pady=10)

        ttk.Label(dialog, text="Lisans anahtarı:").pack()
        key_var = tk.StringVar()
        key_entry = ttk.Entry(dialog, textvariable=key_var, width=30, justify="center")
        key_entry.pack(padx=20, pady=(0, 10))

        result = {"ok": False}

        def verify():
            if license_guard.is_license_valid(machine_id, key_var.get()):
                license_guard.save_license_key(key_var.get())
                result["ok"] = True
                self.update_license_status()
                dialog.destroy()
            else:
                messagebox.showerror("Geçersiz", "Lisans anahtarı geçersiz.", parent=dialog)

        ttk.Button(dialog, text="Doğrula", command=verify).pack(pady=(0, 15))
        dialog.wait_window()
        return result["ok"]

    # -------------------------------------------------------------- helpers
    def results_csv_path(self):
        return self.output_dir / RESULTS_CSV_NAME

    def upload_txt_path(self):
        return self.output_dir / UPLOAD_TXT_NAME

    def choose_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=str(self.output_dir))
        if chosen:
            self.output_dir = Path(chosen)

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8")
        asins = []
        for raw_line in text.splitlines():
            match = ASIN_PATTERN.search(raw_line.upper())
            if match:
                asins.append(match.group(0))
        self.asins = asins
        self.asin_count_label.config(text=f"{len(self.asins)} ASIN yüklendi ({Path(path).name})")

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def log_error(self, message):
        self.error_text.configure(state="normal")
        self.error_text.insert("end", message + "\n")
        self.error_text.see("end")
        self.error_text.configure(state="disabled")

    def open_path(self, path):
        if not path.exists():
            messagebox.showwarning("Bulunamadı", f"{path} henüz oluşturulmadı.")
            return
        os.startfile(path)

    def update_active_label(self):
        if self.active_asins:
            self.active_label.config(
                text="Kontrol ediliyor: " + ", ".join(sorted(self.active_asins)),
                foreground="#0a5",
            )
        else:
            self.active_label.config(text="Beklemede", foreground="#888")

    def _notify_done(self):
        # Saatlerce suren bir tarama sessizce bitip kimse fark etmesin
        # istemiyoruz -- sesli uyari + gorev cubugunda pencereyi yanip
        # sondurme (FlashWindow), kullanici baska bir seyle mesgulken bile
        # "bitti" sinyalini yakalasin diye.
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        try:
            ctypes.windll.user32.FlashWindow(self.root.winfo_id(), True)
        except Exception:
            pass

    def on_close(self):
        try:
            close_existing_chrome_processes()
        except Exception:
            pass
        self.root.destroy()

    # -------------------------------------------------------------- checks
    def start_check(self):
        if self.running:
            return
        if not self.asins:
            messagebox.showwarning("Liste boş", "Önce Dosya menüsünden bir ASIN listesi yükle.")
            return
        if not (
            self.require_year_var.get() or self.check_gaps_var.get() or self.check_dead_stock_var.get()
            or self.check_target_market_var.get()
        ):
            messagebox.showwarning("Seçenek yok", "En az bir kontrol seçeneği işaretli olmalı.")
            return
        target_market_domain = None
        if self.check_target_market_var.get():
            try:
                target_market_domain = int(self.target_market_domain_var.get().strip())
                if target_market_domain <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "Geçersiz hedef ülke",
                    "Hedef ülke/mağaza alanından listeden bir ülke seç, ya da kutuya doğrudan "
                    "Keepa domain ID'sini (pozitif bir sayı, örn. 1 = Amazon.com) yaz.",
                )
                return
        if not has_internet():
            messagebox.showerror(
                "İnternet bağlantısı yok",
                "Bu program çalışmak için internet bağlantısı gerektirir "
                "(Keepa'ya erişim olmadan kontrol yapılamaz). Bağlantını kontrol edip tekrar dene.",
            )
            return
        if not license_guard.is_licensed() and license_guard.remaining_trial() <= 0:
            if not self.show_license_dialog():
                return
        if (self.api_mode_var.get() or self.check_target_market_var.get()) and not keepa_api_settings.has_api_key():
            messagebox.showwarning(
                "Keepa API anahtarı eksik",
                "API Modu veya 'Hedef pazarda bulunabilirlik kontrolü' için önce kendi Keepa "
                "API anahtarını girmelisin.\n\n"
                "Ayarlar > Keepa API Ayarları... menüsünden anahtarını ekleyebilirsin.",
            )
            return

        already_done = []
        to_scan = list(self.asins)
        if self.resume_var.get():
            previous = find_previous_results(self.output_dir)
            if previous:
                already_done = [previous[asin] for asin in self.asins if asin in previous]
                to_scan = [asin for asin in self.asins if asin not in previous]
                if already_done:
                    self.log(
                        f">>> {len(already_done)} ASIN daha önceki bir oturumda zaten kesin "
                        f"sonuç almış, atlanıyor (kaldığı yerden devam)."
                    )

        self.running = True
        self.stop_requested = threading.Event()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.active_asins = set()
        self.progress.config(maximum=len(self.asins), value=len(already_done))
        self.progress_count_label.config(text=f"{len(already_done)} / {len(self.asins)}")
        self.update_active_label()

        self.current_run_id = time.strftime("%Y%m%d_%H%M%S")
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.run_log_handle = (self.output_dir / f"tarama_{self.current_run_id}.log").open(
                "a", encoding="utf-8"
            )
        except Exception:
            self.run_log_handle = None

        prior_avg = scan_stats.all_time_average_seconds()
        self.eta_label.config(
            text=(
                f"Geçmiş ortalamaya göre tahmini süre hesaplanıyor "
                f"(genel ortalama: {prior_avg:.1f} sn/ASIN)..." if prior_avg else
                "Bu ilk tarama -- süre tahmini birkaç ASIN sonra görünecek."
            )
        )
        for widget in (self.log_text, self.error_text):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")
        self.summary_label.config(text="")
        check_options = {
            "require_year": self.require_year_var.get(),
            "check_gaps": self.check_gaps_var.get(),
            "check_dead_stock": self.check_dead_stock_var.get(),
            "check_target_market": self.check_target_market_var.get(),
            "target_market_domain": target_market_domain,
            "domain": int(self.source_market_domain_var.get()),
        }
        api_mode = self.api_mode_var.get()
        take_screenshot = self.screenshot_var.get() and not api_mode  # API modunda ekran goruntusu alinamaz
        max_workers = max(1, self.parallel_var.get())
        output_dir = self.output_dir
        thread = threading.Thread(
            target=self._run_check,
            args=(
                to_scan, take_screenshot, max_workers, output_dir, check_options,
                self.current_run_id, already_done, api_mode,
            ),
            daemon=True,
        )
        thread.start()

    def stop_check(self):
        if not self.running or self.stop_requested.is_set():
            return
        self.stop_requested.set()
        self.stop_button.config(state="disabled")
        self.log(">>> Durdurma istendi: Chrome kapatılıyor, devam eden işlemler birkaç saniye içinde sonlanacak...")
        # Chrome'u hemen kapatmak, o an calisan tum CDP cagrilarini aninda
        # basarisiz kilar -- futures'i tek tek iptal etmeye calismaktan
        # (calisan bir thread'i Python'da zorla durduramayiz) cok daha hizli
        # ve etkili bir "dur" sinyali.
        try:
            close_existing_chrome_processes()
        except Exception:
            pass

    def _run_check(self, asins, take_screenshot, max_workers, output_dir, check_options, run_id, already_done, api_mode=False):
        prevent_sleep()
        # Hedef pazar kontrolu (check_target_market) Chrome modunda da bir
        # Keepa API cagrisi gerektirir -- api_mode disinda da musterinin
        # KENDI anahtarini (keyring) yukluyoruz, boylece keepa_check.py
        # ICE DUSMUS gelistirici-anahtari fallback'ine (frozen exe'de
        # kasitli olarak hata firlatir) hic dusmez.
        api_key_for_run = (
            keepa_api_settings.load_api_key()
            if (api_mode or check_options.get("check_target_market"))
            else None
        )
        if asins and not api_mode:
            try:
                start_chrome_for_attachment(start_url="about:blank", headless=True)
            except Exception as error:
                self.event_queue.put(("fatal_error", f"Chrome başlatılamadı: {error}"))
                self.event_queue.put(("done", None))
                allow_sleep()
                return

        results = []
        run_durations = []
        durations_lock = threading.Lock()
        prior_avg = scan_stats.all_time_average_seconds()

        open_files = []

        def process(asin):
            if self.stop_requested.is_set():
                return asin, {"status": "durduruldu", "reason": "kullanici_durdurdu", "gap_count": None}, None, None
            self.event_queue.put(("start", asin))
            started = time.monotonic()
            last_error, last_trace = None, None
            for attempt in range(MAX_RETRIES + 1):
                if self.stop_requested.is_set():
                    return asin, {"status": "durduruldu", "reason": "kullanici_durdurdu", "gap_count": None}, None, None
                try:
                    if api_mode:
                        result = keepa_check_detailed_api(
                            asin, api_key=api_key_for_run, stop_event=self.stop_requested,
                            require_year=check_options["require_year"],
                            check_gaps=check_options["check_gaps"],
                            check_dead_stock=check_options["check_dead_stock"],
                            check_customs_risk=self.check_customs_risk_var.get(),
                            check_target_market=check_options["check_target_market"],
                            target_market_domain=check_options["target_market_domain"],
                            domain=check_options["domain"],
                        )
                    else:
                        result = keepa_check_detailed(
                            asin, take_screenshot=take_screenshot, api_key=api_key_for_run, **check_options
                        )
                    return asin, result, None, time.monotonic() - started
                except Exception as error:
                    last_error, last_trace = error, traceback.format_exc()
                    if attempt < MAX_RETRIES:
                        self.event_queue.put((
                            "log",
                            f"{asin}: hata ({type(error).__name__}), tekrar deneniyor "
                            f"({attempt + 1}/{MAX_RETRIES})...",
                        ))
                        time.sleep(2)
            if self.stop_requested.is_set():
                return asin, {"status": "durduruldu", "reason": "kullanici_durdurdu", "gap_count": None}, None, None
            return asin, None, (last_error, last_trace), time.monotonic() - started

        try:
            # Sonuclari SADECE tarama tamamen bitince tek seferde yazmak, uzun
            # (saatlerce suren) taramalarda cok riskli: arada bir cokme/kapanma/
            # uyku olursa o ana kadarki HER SEY kaybolabiliyordu (yasandi,
            # dogrulandi -- 9000 ASIN'lik bir gece taramasi boyle kayboldu). Bu
            # yuzden her ASIN biter bitmez ANINDA diske yaziyoruz. Ayrica her
            # oturum kendi zaman damgali dosyasini alir ve BASKA hicbir oturum
            # tarafindan uzerine yazilmaz -- "latest" dosyalar (yukleme_listesi.txt
            # vb.) sadece kolaylik icin en son oturumu da ayrica yansitir. Bu
            # blok da disaridaki try/finally icinde ki dosya acma basarisiz
            # olsa bile Chrome kapatma/uyku izni geri verme calissin.
            output_dir.mkdir(parents=True, exist_ok=True)
            fieldnames = ["asin", "status", "reason", "gap_count", "score"]

            def open_writer(path, is_csv):
                file = path.open("w", newline="" if is_csv else None, encoding="utf-8")
                open_files.append(file)
                if is_csv:
                    writer = csv.DictWriter(file, fieldnames=fieldnames)
                    writer.writeheader()
                    file.flush()
                    return writer
                return file

            run_csv_writer = open_writer(output_dir / f"tarama_{run_id}.csv", True)
            run_upload_file = open_writer(output_dir / f"tarama_{run_id}_temiz.txt", False)
            latest_csv_writer = open_writer(output_dir / RESULTS_CSV_NAME, True)
            latest_upload_file = open_writer(output_dir / UPLOAD_TXT_NAME, False)

            def save_row(row):
                run_csv_writer.writerow(row)
                latest_csv_writer.writerow(row)
                if row["status"] == "upload":
                    run_upload_file.write(row["asin"] + "\n")
                    latest_upload_file.write(row["asin"] + "\n")
                for file in open_files:
                    file.flush()

            # Daha once (resume ile atlanan) kesin sonucu olan ASIN'ler de
            # bu oturumun dosyasina yazilsin -- oturum dosyasi, istenen
            # TUM listenin guncel durumunu kendi icinde eksiksiz gostersin.
            for row in already_done:
                results.append(row)
                save_row(row)
                self.event_queue.put(("progress", 1))

            executor = ThreadPoolExecutor(max_workers=max_workers)
            futures = [executor.submit(process, asin) for asin in asins]
            completed_count = 0
            try:
                for future in as_completed(futures):
                    asin, result, error_info, duration = future.result()
                    self.event_queue.put(("finish", asin))
                    completed_count += 1
                    if duration is not None:
                        scan_stats.record_scan(duration)
                        with durations_lock:
                            run_durations.append(duration)
                            run_avg = sum(run_durations) / len(run_durations)
                        avg_for_eta = run_avg if len(run_durations) >= 2 else (prior_avg or run_avg)
                        remaining = len(asins) - completed_count
                        eta_seconds = (remaining * avg_for_eta) / max_workers if avg_for_eta else None
                        self.event_queue.put(("eta", (run_avg, eta_seconds)))
                    if error_info is not None:
                        error, trace = error_info
                        line = f"{asin}: HATA ({type(error).__name__})"
                        self.event_queue.put(
                            ("error_detail", f"{asin}: {type(error).__name__}: {error}")
                        )
                        row = {
                            "asin": asin,
                            "status": "hata",
                            "reason": f"{type(error).__name__}: {error}",
                            "gap_count": None,
                            "score": None,
                        }
                    else:
                        line = f"{asin}: {result['status']} ({result['reason']})"
                        row = {
                            "asin": asin,
                            "status": result["status"],
                            "reason": result["reason"],
                            "gap_count": result["gap_count"],
                            "score": result.get("score"),
                        }
                    results.append(row)
                    save_row(row)
                    self.event_queue.put(("log", line))
                    self.event_queue.put(("progress", 1))
            finally:
                # Durdurulduysa henuz baslamamis islemleri iptal edip
                # calisanlari BEKLEMEDEN devam ediyoruz (wait=True olsaydi
                # arayuz, hala calisan/agli kesilmis thread'ler bitene kadar
                # kilitlenirdi). Normal bitişte ise hepsi zaten tamamlanmis olur.
                executor.shutdown(wait=not self.stop_requested.is_set(), cancel_futures=True)

            # NOT: sonuclar zaten yukarida save_row() ile ANINDA diske yazildi
            # -- burada ekstra bir "toplu yazma" adimi yok, kaybolma riski
            # bu adimin kendisine bagli olmasin diye kasitli.
            upload_count = sum(1 for r in results if r["status"] == "upload")

            if not license_guard.is_licensed():
                license_guard.add_usage(upload_count)
            self.event_queue.put(("license_status", None))

            durdurulan = sum(1 for r in results if r["status"] == "durduruldu")
            summary = (
                f"Toplam: {len(results)}  |  Upload (temiz): {upload_count}  |  "
                f"Delete: {sum(1 for r in results if r['status'] == 'delete')}  |  "
                f"Hata: {sum(1 for r in results if r['status'] == 'hata')}"
                + (f"  |  Durduruldu: {durdurulan}" if durdurulan else "")
            )
            self.event_queue.put(("summary", summary))
            self.event_queue.put(
                ("log", f">>> Bu oturumun kalıcı kaydı: tarama_{run_id}.csv / tarama_{run_id}_temiz.txt")
            )
        finally:
            for file in open_files:
                try:
                    file.close()
                except Exception:
                    pass
            # Kontrol bitince (headless/ekran-disi) Chrome sureci arkada
            # yasamaya devam etmesin -- kullanici gormedigi icin kapatmayi
            # unutamaz, biz kapatiyoruz.
            try:
                close_existing_chrome_processes()
            except Exception:
                pass
            allow_sleep()
            self.event_queue.put(("done", None))

    def _write_run_log(self, message):
        handle = getattr(self, "run_log_handle", None)
        if handle is None:
            return
        try:
            handle.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")
            handle.flush()
        except Exception:
            pass

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                    self._write_run_log(payload)
                elif kind == "error_detail":
                    self.log_error(payload)
                    self._write_run_log(f"HATA: {payload}")
                elif kind == "start":
                    self.active_asins.add(payload)
                    self.update_active_label()
                elif kind == "finish":
                    self.active_asins.discard(payload)
                    self.update_active_label()
                elif kind == "progress":
                    self.progress.step(payload)
                    current = int(self.progress["value"])
                    total = int(self.progress["maximum"])
                    self.progress_count_label.config(text=f"{current} / {total}")
                elif kind == "summary":
                    self.summary_label.config(text=payload)
                elif kind == "eta":
                    run_avg, eta_seconds = payload
                    if eta_seconds is not None:
                        self.eta_label.config(
                            text=(
                                f"Bu taramada ortalama: {run_avg:.1f} sn/ASIN  |  "
                                f"Tahmini kalan süre: {scan_stats.format_duration(eta_seconds)}"
                            )
                        )
                elif kind == "license_status":
                    self.update_license_status()
                elif kind == "send_status":
                    self.send_status_label.config(text=payload)
                elif kind == "info":
                    messagebox.showinfo("Bilgi", payload)
                elif kind == "fatal_error":
                    messagebox.showerror("Hata", payload)
                elif kind == "done":
                    self.running = False
                    self.start_button.config(state="normal")
                    self.stop_button.config(state="disabled")
                    self.update_active_label()
                    self.eta_label.config(text="")
                    handle = getattr(self, "run_log_handle", None)
                    if handle is not None:
                        try:
                            handle.close()
                        except Exception:
                            pass
                        self.run_log_handle = None
                    self._notify_done()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)


def main():
    root = tk.Tk()
    KeepaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
