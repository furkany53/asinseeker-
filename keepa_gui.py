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
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        finder_tab = ttk.Frame(self.notebook)
        check_tab = ttk.Frame(self.notebook)
        send_tab = ttk.Frame(self.notebook)
        self.notebook.add(finder_tab, text="ASIN Bul")
        self.notebook.add(check_tab, text="Temizle")
        self.notebook.add(send_tab, text="Easy'e Gönder")

        self._build_finder_tab(finder_tab)
        self._build_ui(check_tab)
        self._build_send_tab(send_tab)

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

        ttk.Label(frm, text="Kayıt Klasörü:").grid(row=row, column=0, sticky="w")
        self.finder_output_dir_var = tk.StringVar(value=str(BASE_DIR))
        # Kullanici "Klasor Sec..." ile kendi elleriyle bir klasor secene
        # kadar, klasoru BIZ (strateji secimine gore) otomatik yonetiyoruz --
        # boylece her strateji kendi klasorune yazar, farkli kriterlerle
        # bulunmus ASIN'ler/dilim dosyalari yanlislikla ayni klasorde
        # karismaz (musteriden geldi: strateji secince klasor kendiliginden
        # degismiyordu).
        self._finder_output_dir_is_auto = True
        ttk.Entry(frm, textvariable=self.finder_output_dir_var, width=55).grid(
            row=row, column=1, columnspan=2, sticky="ew", padx=5
        )
        ttk.Button(frm, text="Klasör Seç...", command=self.finder_choose_output_dir).grid(
            row=row, column=3, sticky="w"
        )
        row += 1

        strategy_frame = ttk.LabelFrame(frm, text="Hazır Strateji (Buy Box / dropshipping odaklı, opsiyonel)")
        strategy_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 3))
        self.finder_strategy_var = tk.StringVar(value="Yok (aşağıdaki manuel/kişisel filtreleri kullan)")
        strategy_values = ["Yok (aşağıdaki manuel/kişisel filtreleri kullan)"] + [
            kf.STRATEGY_LABELS[key] for key in kf.STRATEGIES
        ]
        self._finder_strategy_label_to_key = {
            kf.STRATEGY_LABELS[key]: key for key in kf.STRATEGIES
        }
        strategy_combo = ttk.Combobox(
            strategy_frame, textvariable=self.finder_strategy_var, values=strategy_values,
            state="readonly", width=48,
        )
        strategy_combo.grid(row=0, column=0, padx=8, pady=5, sticky="w")
        strategy_combo.bind("<<ComboboxSelected>>", self._finder_on_strategy_change)
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

        def add_filter(label_text, attr_name, kf_attr, col, r, width=8):
            ttk.Label(filters, text=label_text).grid(row=r, column=col, padx=5, pady=3, sticky="w")
            var = tk.StringVar(value=str(getattr(kf, kf_attr, "")))
            ttk.Entry(filters, textvariable=var, width=width).grid(row=r, column=col + 1, padx=5, pady=3, sticky="w")
            self.finder_filter_vars[kf_attr] = var

        add_filter("Domain (5=co.jp, 1=com):", "domain", "DOMAIN", 0, 0, width=6)
        add_filter("Fiyat min:", "price_min", "LISTPRICE_MIN", 2, 0)
        add_filter("Fiyat max:", "price_max", "LISTPRICE_MAX", 4, 0)
        add_filter("Offer count min:", "offer_min", "OFFER_COUNT_MIN", 0, 1, width=6)
        add_filter("Offer count max:", "offer_max", "OFFER_COUNT_MAX", 2, 1, width=6)
        add_filter("Son X gün güncellenmiş:", "recent_days", "RECENT_OFFERS_UPDATE_DAYS", 4, 1, width=6)
        add_filter("30 günlük SR düşüş min:", "sr_drop_30", "SALES_RANK_DROPS_30_MIN", 0, 2, width=6)
        add_filter("90 günlük SR düşüş min:", "sr_drop_90", "SALES_RANK_DROPS_90_MIN", 2, 2, width=6)
        add_filter("Aylık satış (tahmini) min:", "monthly_sold_min", "MONTHLY_SOLD_MIN", 4, 2, width=6)
        add_filter("Amazon'un kendisi satıyorsa hariç tut:", "no_amazon", "AVAILABILITY_AMAZON_EXCLUDE", 0, 3, width=6)
        add_filter("Paket ağırlığı max (gram):", "package_weight_max", "PACKAGE_WEIGHT_GRAMS_MAX", 2, 3, width=8)
        add_filter("Kategori (Root Category ID):", "root_category", "ROOT_CATEGORY", 4, 3, width=10)
        ttk.Label(
            filters, foreground="#666", font=("Segoe UI", 8), wraplength=760, justify="left",
            text="Boş bırakılan alanlar Keepa Finder'a hiç gönderilmez (o filtre uygulanmaz).",
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
        strategy_key = self._finder_strategy_label_to_key.get(self.finder_strategy_var.get())
        if strategy_key is not None:
            self.finder_manual_frame.grid_remove()
        else:
            self.finder_manual_frame.grid()

        if self._finder_output_dir_is_auto:
            if strategy_key is not None:
                self.finder_output_dir_var.set(str(BASE_DIR / f"keepa_arama_{strategy_key}"))
            else:
                self.finder_output_dir_var.set(str(BASE_DIR))

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
        "DOMAIN", "LISTPRICE_MIN", "LISTPRICE_MAX",
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
                setattr(kf, kf_attr, int(raw))
            except ValueError:
                raise ValueError(f"'{raw}' sayı değil (alan: {kf_attr})")

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

        step = 0  # strateji modunda kullanilmiyor, sadece manuel modda gercek deger alir
        if strategy_key is not None:
            # Hazir strateji modu: manuel Sales Rank/filtre alanlari YOK
            # SAYILIR -- strateji kendi bandini/kriterlerini tasir.
            strategy = kf.STRATEGIES[strategy_key]
            start, end = strategy["sales_gte"], strategy["sales_lte"]
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
        elif using_personal_filter:
            self.finder_log(">>> Kişisel Keepa filtresi kullanılıyor (yukarıdaki hazır alanlar yok sayıldı).")

        self.finder_current_output_dir = output_dir
        self.finder_current_shared_pool_path = output_dir.resolve().parent / "ortak_asin_havuzu.txt"
        thread = threading.Thread(
            target=self._finder_run,
            args=(output_dir, start, end, step, api_key, strategy_key),
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

    def _finder_run(self, output_dir, start, end, step, api_key, strategy_key=None):
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

        def do_paste():
            try:
                result = target["paste_asins"](DEBUG_ADDRESS, asins)
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
        # Bu adim EasyCentral'in gunluk kotasini harcar ve GERI ALINAMAZ --
        # otomatik akis hizli olsun diye kurulsa da, yanlislikla tikla(n)ip
        # kota bosa gitmesin diye burada TEK bir onay birakiyoruz.
        if not messagebox.askyesno(
            "Onayla",
            f"{len(asins)} ASIN, Chrome'da sayfa açılıp mağaza kontrolü yapıldıktan hemen sonra "
            f"OTOMATİK olarak gönderilip taramayı başlatacak (EasyCentral kotasını harcar, geri alınamaz).\n\n"
            f"Devam edilsin mi?",
        ):
            return

        self.send_status_label.config(text="Chrome açılıyor, sayfa yükleniyor, gönderiliyor...", foreground="#555")

        def do_submit():
            try:
                start_chrome_for_attachment(start_url="about:blank", headless=False)
                result = target["submit_and_start_scan"](DEBUG_ADDRESS, asins)
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
        ttk.Checkbutton(options_frame, text="Ekran görüntüsü kaydet", variable=self.screenshot_var).grid(
            row=1, column=0, padx=8, pady=(0, 4), sticky="w"
        )
        ttk.Label(options_frame, text="Paralel pencere:").grid(row=1, column=1, sticky="e", pady=(0, 4))
        self.parallel_var = tk.IntVar(value=4)
        ttk.Spinbox(options_frame, from_=1, to=8, width=5, textvariable=self.parallel_var).grid(
            row=1, column=2, sticky="w", pady=(0, 4)
        )
        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame,
            text="Daha önce taranmışları atla (kaldığı yerden devam et)",
            variable=self.resume_var,
        ).grid(row=2, column=0, columnspan=3, padx=8, pady=(0, 4), sticky="w")
        self.api_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options_frame,
            text="API Modu (Chrome gerektirmez, Keepa API anahtarı ile hızlı çalışır)",
            variable=self.api_mode_var,
        ).grid(row=3, column=0, columnspan=3, padx=8, pady=(0, 4), sticky="w")
        row += 1

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
        if not (self.require_year_var.get() or self.check_gaps_var.get() or self.check_dead_stock_var.get()):
            messagebox.showwarning("Seçenek yok", "En az bir kontrol seçeneği işaretli olmalı.")
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
        if self.api_mode_var.get() and not keepa_api_settings.has_api_key():
            messagebox.showwarning(
                "Keepa API anahtarı eksik",
                "API Modu için önce kendi Keepa API anahtarını girmelisin.\n\n"
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
        api_key_for_run = keepa_api_settings.load_api_key() if api_mode else None
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
                        )
                    else:
                        result = keepa_check_detailed(asin, take_screenshot=take_screenshot, **check_options)
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
