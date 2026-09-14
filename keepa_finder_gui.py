"""Keepa Finder Takip Ekrani -- keepa_finder.py'nin Sales Rank taramasini
gorsel olarak baslatip durdurabilecegin, canli durumu (su an taranan aralik,
token/kota durumu, toplam ASIN sayisi) gosteren masaustu programi.

Nereden calistirirsan calistir (bu bilgisayar, baska bir PC/VM), "Kayit
Klasoru" alanindan PAYLASILAN proje klasorunu (ornegin bu OneDrive klasorunun
kendisini) gosterirsen, topladigi ASIN'ler dogrudan o klasore yazilir --
ayri bir senkron/transfer protokolune gerek yoktur.

Bu araç yalnizca Keepa API anahtarini kullanir; TARAYICI/Keepa oturum
girisi GEREKMEZ (AsinSeeker'daki grafik kontrolunun aksine, bu tamamen
API uzerinden calisir).
"""

import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
import winsound
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# PyInstaller onefile ile paketlenince __file__ GERCEK exe konumunu degil,
# her calistirmada silinen GECICI bir cikarma klasorunu gosterir -- frozen
# halde sys.executable'in bulundugu GERCEK klasoru kullaniyoruz.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import keepa_finder as kf  # noqa: E402


class FinderApp:
    def __init__(self, root):
        self.root = root
        root.title("Keepa Finder Takip Ekranı")
        self.event_queue = queue.Queue()
        self.running = False
        self.stop_event = None
        self.total_asins = 0
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(150, self._poll)

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")
        row = 0

        ttk.Label(frm, text="Kayıt Klasörü:").grid(row=row, column=0, sticky="w")
        self.output_dir_var = tk.StringVar(value=str(BASE_DIR))
        ttk.Entry(frm, textvariable=self.output_dir_var, width=55).grid(
            row=row, column=1, columnspan=2, sticky="ew", padx=5
        )
        ttk.Button(frm, text="Klasör Seç...", command=self.choose_output_dir).grid(row=row, column=3, sticky="w")
        row += 1

        params = ttk.LabelFrame(frm, text="Sales Rank Aralığı")
        params.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(8, 8))
        ttk.Label(params, text="Başlangıç:").grid(row=0, column=0, padx=5, pady=5)
        self.start_var = tk.IntVar(value=kf.SALES_RANK_START)
        ttk.Entry(params, textvariable=self.start_var, width=10).grid(row=0, column=1, padx=5)
        ttk.Label(params, text="Bitiş:").grid(row=0, column=2, padx=5)
        self.end_var = tk.IntVar(value=kf.SALES_RANK_END)
        ttk.Entry(params, textvariable=self.end_var, width=10).grid(row=0, column=3, padx=5)
        ttk.Label(params, text="Dilim genişliği:").grid(row=0, column=4, padx=5)
        self.step_var = tk.IntVar(value=kf.SALES_RANK_STEP)
        ttk.Entry(params, textvariable=self.step_var, width=8).grid(row=0, column=5, padx=5)
        row += 1

        filters = ttk.LabelFrame(frm, text="Diğer Filtreler (yeni bir arama kriteriyle devam etmek için değiştir)")
        filters.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="Domain (5=co.jp, 1=com):").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.domain_var = tk.IntVar(value=kf.DOMAIN)
        ttk.Entry(filters, textvariable=self.domain_var, width=6).grid(row=0, column=1, padx=5)
        ttk.Label(filters, text="Fiyat min:").grid(row=0, column=2, padx=5)
        self.price_min_var = tk.IntVar(value=kf.LISTPRICE_MIN)
        ttk.Entry(filters, textvariable=self.price_min_var, width=8).grid(row=0, column=3, padx=5)
        ttk.Label(filters, text="Fiyat max:").grid(row=0, column=4, padx=5)
        self.price_max_var = tk.IntVar(value=kf.LISTPRICE_MAX)
        ttk.Entry(filters, textvariable=self.price_max_var, width=8).grid(row=0, column=5, padx=5)
        ttk.Label(filters, text="Offer count min:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.offer_min_var = tk.IntVar(value=kf.OFFER_COUNT_MIN)
        ttk.Entry(filters, textvariable=self.offer_min_var, width=6).grid(row=1, column=1, padx=5)
        ttk.Label(filters, text="Offer count max:").grid(row=1, column=2, padx=5)
        self.offer_max_var = tk.IntVar(value=kf.OFFER_COUNT_MAX)
        ttk.Entry(filters, textvariable=self.offer_max_var, width=6).grid(row=1, column=3, padx=5)
        ttk.Label(filters, text="Son X gün güncellenmiş:").grid(row=1, column=4, padx=5)
        self.recent_days_var = tk.IntVar(value=kf.RECENT_OFFERS_UPDATE_DAYS)
        ttk.Entry(filters, textvariable=self.recent_days_var, width=6).grid(row=1, column=5, padx=5)
        row += 1

        self.start_button = ttk.Button(frm, text="Başlat", command=self.start)
        self.start_button.grid(row=row, column=0, pady=8, sticky="w")
        self.stop_button = ttk.Button(frm, text="Durdur", command=self.stop, state="disabled")
        self.stop_button.grid(row=row, column=1, pady=8, sticky="w")
        row += 1

        status_frame = ttk.LabelFrame(frm, text="Durum")
        status_frame.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        self.position_label = ttk.Label(status_frame, text="Şu an taranan: -", font=("Segoe UI", 10, "bold"))
        self.position_label.grid(row=0, column=0, sticky="w", padx=8, pady=4, columnspan=2)
        self.total_label = ttk.Label(status_frame, text="Toplam ASIN: 0", font=("Segoe UI", 10, "bold"))
        self.total_label.grid(row=0, column=2, sticky="w", padx=8, pady=4)
        self.token_label = ttk.Label(status_frame, text="Token: -")
        self.token_label.grid(row=1, column=0, sticky="w", padx=8, pady=4, columnspan=2)
        self.progress_pct_label = ttk.Label(status_frame, text="İlerleme: -")
        self.progress_pct_label.grid(row=1, column=2, sticky="w", padx=8, pady=4)
        self.cost_label = ttk.Label(status_frame, text="Token maliyeti: -")
        self.cost_label.grid(row=2, column=0, sticky="w", padx=8, pady=4, columnspan=3)
        self.progress = ttk.Progressbar(status_frame, length=400, mode="determinate")
        self.progress.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))
        row += 1

        ttk.Label(frm, text="Log:").grid(row=row, column=0, sticky="w")
        row += 1
        self.log_text = scrolledtext.ScrolledText(frm, height=16, width=95, state="disabled")
        self.log_text.grid(row=row, column=0, columnspan=4, pady=(0, 5))
        row += 1

        ttk.Label(
            frm,
            text=(
                "Not: Bu araç sadece Keepa API anahtarını kullanır -- tarayıcı/Keepa oturum girişi "
                "gerekmez. \"keepa_api_key.txt\" dosyası bu programla aynı klasörde olmalı."
            ),
            foreground="#666", font=("Segoe UI", 8), wraplength=760, justify="left",
        ).grid(row=row, column=0, columnspan=4, sticky="w")

    def choose_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_dir_var.get())
        if chosen:
            self.output_dir_var.set(chosen)

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def start(self):
        if self.running:
            return
        try:
            output_dir = Path(self.output_dir_var.get())
            start = int(self.start_var.get())
            end = int(self.end_var.get())
            step = int(self.step_var.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("Geçersiz değer", "Sales Rank alanları sayı olmalı.")
            return
        if step <= 0 or end <= start:
            messagebox.showwarning("Geçersiz aralık", "Bitiş, başlangıçtan büyük ve dilim genişliği pozitif olmalı.")
            return
        try:
            kf.load_api_key()
        except Exception as error:
            messagebox.showerror("API anahtarı bulunamadı", str(error))
            return

        # Diger filtreler (fiyat/offer count/domain/vs.) kf modulunun kendi
        # sabitleri uzerinden okunuyor (build_selection bunlari dogrudan
        # kullanir) -- GUI'den degistirilebilsinler diye baslamadan once
        # modulun sabitlerini GUI'deki degerlerle guncelliyoruz. Tek seferde
        # tek tarama calistigi icin (bu program baska bir taramayla ayni
        # anda kullanilmiyor) bu guvenli.
        try:
            kf.DOMAIN = int(self.domain_var.get())
            kf.LISTPRICE_MIN = int(self.price_min_var.get())
            kf.LISTPRICE_MAX = int(self.price_max_var.get())
            kf.OFFER_COUNT_MIN = int(self.offer_min_var.get())
            kf.OFFER_COUNT_MAX = int(self.offer_max_var.get())
            kf.RECENT_OFFERS_UPDATE_DAYS = int(self.recent_days_var.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("Geçersiz değer", "Filtre alanları sayı olmalı.")
            return

        self.running = True
        self.stop_event = threading.Event()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.progress.config(maximum=max(1, end - start), value=0)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.current_output_dir = output_dir
        # Ortak havuz, output_dir'in bir ust klasorune (KARDESI olarak)
        # yaziliyor -- boylece output_dir'i git ile senkronlanan paylasilan
        # bir klasorun ICINE koyarsan (ornegin "keepa_sync\keepa_arama_xxx"),
        # ortak havuz da ayni git klasorune ("keepa_sync\ortak_asin_havuzu.txt")
        # duser, hangi makinede/BASE_DIR'de calistigina bakmaksizin.
        self.current_shared_pool_path = output_dir.resolve().parent / "ortak_asin_havuzu.txt"
        thread = threading.Thread(
            target=self._run, args=(output_dir, start, end, step), daemon=True
        )
        thread.start()

    def _notify_complete(self, total):
        # Bu program uzak bir masaustunde/VM'de, kimse ekrana bakmadan
        # calisiyor olabilir -- ses/pencere yanip sonmesi sadece o an ekranin
        # basinda olan biri icin ise yarar. Asil GUVENILIR bildirim, klasore
        # (OneDrive ile senkronsa ana bilgisayara da yansir) yazilan bu
        # kalici TAMAMLANDI dosyasi -- kullanici klasoru her ne zaman
        # kontrol ederse etsin tarama bittigini gorur.
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        try:
            ctypes.windll.user32.FlashWindow(self.root.winfo_id(), True)
        except Exception:
            pass
        try:
            marker = self.current_output_dir / f"TAMAMLANDI_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            marker.write_text(
                f"Tarama tamamlandi.\n"
                f"Zaman: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Sales Rank araligi: {self.start_var.get()} - {self.end_var.get()}\n"
                f"Toplam ASIN (bu klasor): {total}\n"
                f"Ortak havuz dosyasi: {self.current_shared_pool_path}\n"
                f"Yeni bir arama kriteriyle devam etmek icin filtreleri degistirip "
                f"farkli bir Kayit Klasoru ile tekrar Baslat.\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def stop(self):
        if not self.running or self.stop_event is None:
            return
        self.stop_event.set()
        self.stop_button.config(state="disabled")
        self.log(">>> Durdurma istendi -- şu an beklenen bir aralık varsa bitince güvenli şekilde duracak.")

    def _run(self, output_dir, start, end, step):
        def progress_callback(kind, payload):
            self.event_queue.put((kind, payload))

        try:
            kf.fetch_all_asins(
                output_dir, sales_rank_start=start, sales_rank_end=end, sales_rank_step=step,
                progress=progress_callback, stop_event=self.stop_event,
                shared_pool_path=self.current_shared_pool_path,
            )
        except Exception as error:
            self.event_queue.put(("fatal", {"message": f"{type(error).__name__}: {error}"}))
        self.event_queue.put(("done", {}))

    def _poll(self):
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                message = payload.get("message", "")
                if kind in ("log", "slice_start", "page"):
                    if message:
                        self.log(message)
                if kind in ("slice_start", "page"):
                    position = payload.get("slice_end") if kind == "slice_start" else payload.get("position")
                    if position is not None:
                        self.position_label.config(text=f"Şu an taranan Sales Rank: {position}")
                        try:
                            start = int(self.start_var.get())
                            end = int(self.end_var.get())
                            done = max(0, position - start)
                            self.progress.config(value=done)
                            pct = min(100, done / max(1, end - start) * 100)
                            self.progress_pct_label.config(text=f"İlerleme: %{pct:.2f}")
                        except (ValueError, tk.TclError):
                            pass
                if kind == "page":
                    total = payload.get("total")
                    if total is not None:
                        self.total_asins = total
                        self.total_label.config(text=f"Toplam ASIN: {total}")
                    tokens_left = payload.get("tokens_left")
                    refill_rate = payload.get("refill_rate")
                    if tokens_left is not None:
                        self.token_label.config(
                            text=f"Token: {tokens_left} kalan, {refill_rate or '?'} /dk yenileniyor"
                        )
                    tokens_total = payload.get("tokens_consumed_total")
                    tokens_per_asin = payload.get("tokens_per_asin")
                    if tokens_total is not None:
                        self.cost_label.config(
                            text=f"Token maliyeti: bu oturumda {tokens_total} token kullanıldı "
                                 f"(ASIN başına ortalama {tokens_per_asin:.2f} token)"
                        )
                elif kind == "complete":
                    self.log(f">>> TAMAMLANDI: {message}")
                    self._notify_complete(payload.get("total", self.total_asins))
                elif kind == "stopped":
                    self.log(f">>> {message}")
                elif kind == "fatal":
                    messagebox.showerror("Hata", message)
                elif kind == "done":
                    self.running = False
                    self.start_button.config(state="normal")
                    self.stop_button.config(state="disabled")
                    self.log(">>> Durdu / tamamlandı.")
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def on_close(self):
        if self.running and self.stop_event is not None:
            self.stop_event.set()
            time.sleep(0.3)
        self.root.destroy()


def main():
    root = tk.Tk()
    FinderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
