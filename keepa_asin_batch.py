import csv
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from keepa_check import keepa_check_detailed

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "asin_list.txt"
OUTPUT_FILE = BASE_DIR / "keepa_batch_sonuc.csv"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ONEMLI: Keepa, Cloudflare ile korunuyor. Denendi -- tamamen headless Chrome VEYA
# yepyeni/temiz bir profil (cerez/gecmisi olmayan) Cloudflare tarafindan direkt
# "Attention Required" ile engelleniyor; headless olup olmamasi degil, profilin
# daha once normal kullanimla "guvenilir" hale gelmis olmasi belirleyici. Bu yuzden
# EasyCentral botunun kullandigi ayni profili (C:\chrome_debug_temp, 127.0.0.1:9222)
# yeniden kullaniyoruz -- pencereyi ekran disina konumlandirip pratikte gorunmez
# yapiyoruz, ama tarayicinin kendisi "gercek/guvenilir" bir profil olarak kaliyor.
DEBUG_ADDRESS = os.getenv("CHROME_DEBUG_ADDRESS", "127.0.0.1:9222")
CHROME_PROFILE = Path(os.getenv("KEEPA_BATCH_PROFILE", r"C:\chrome_debug_temp"))
WORKERS = int(os.getenv("KEEPA_BATCH_WORKERS", "4"))
# Ekran goruntusu almak yavaslatir ama gozle inceleme/ornek bulma icin gerekli;
# hiz onemliyse KEEPA_BATCH_SCREENSHOTS=false ile kapatilabilir.
SCREENSHOTS = os.getenv("KEEPA_BATCH_SCREENSHOTS", "true").lower() == "true"


def read_asins(path: Path):
    if not path.exists():
        raise FileNotFoundError(
            f"ASIN listesi bulunamadi: {path}\n"
            f"'{path.name}' dosyasina, her satira bir ASIN olacak sekilde listeni yapistir."
        )
    asins = []
    with path.open("r", encoding="utf-8", errors="ignore") as file:
        for line in file:
            value = line.strip().replace("\ufeff", "")
            if not value:
                continue
            # ASIN listeleri virgul, noktali virgul, bosluk VEYA tab (kopyala-yapistir
            # Excel/Google Sheets tablolarinda ASIN\tMARKA seklinde gelebiliyor) ile
            # ayrilmis olabilir; ilk sutunu almak icin hepsini tek seferde boluyoruz.
            token = re.split(r"[\s,;]+", value)[0]
            if re.fullmatch(r"[A-Za-z0-9]{10}", token):
                asins.append(token.upper())
    return asins


def debug_port_is_open():
    host, port_text = DEBUG_ADDRESS.split(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=1):
            return True
    except OSError:
        return False


def ensure_chrome():
    if debug_port_is_open():
        print(f"Chrome zaten acik, mevcut oturum kullanilacak: {DEBUG_ADDRESS}")
        return

    if not Path(CHROME_PATH).exists():
        raise FileNotFoundError(f"Chrome bulunamadi: {CHROME_PATH}")

    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    print(f"Chrome baslatiliyor ({DEBUG_ADDRESS}, ekran disina konumlandirilacak)...")
    subprocess.Popen(
        [
            CHROME_PATH,
            f"--remote-debugging-port={DEBUG_ADDRESS.rsplit(':', 1)[1]}",
            f"--user-data-dir={CHROME_PROFILE}",
            "--remote-allow-origins=*",
            "--window-position=-2000,-2000",
            "--no-first-run",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    for _ in range(20):
        if debug_port_is_open():
            return
        time.sleep(1)
    raise RuntimeError("Chrome debug portu acilmadi.")


def check_one(asin):
    try:
        result = keepa_check_detailed(asin, debug_address=DEBUG_ADDRESS, take_screenshot=SCREENSHOTS)
        return result
    except Exception as error:
        print(f"{asin}: HATA - {type(error).__name__}: {error}")
        return {"asin": asin, "status": "error", "reason": f"{type(error).__name__}: {error}", "gap_count": None, "gaps_px": []}


def main():
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    asins = read_asins(input_path)
    if not asins:
        print(f"'{input_path}' icinde gecerli ASIN bulunamadi.")
        return

    print(f"{len(asins)} ASIN okundu ({input_path}). Paralel islenecek: {WORKERS}")
    ensure_chrome()

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(check_one, asin): asin for asin in asins}
        done = 0
        for future in as_completed(futures):
            result = future.result()
            results[result["asin"]] = result
            done += 1
            detail = result["reason"]
            if result.get("gap_count"):
                detail += f", kopmalar(px)={result['gaps_px']}"
            print(f"[{done}/{len(asins)}] {result['asin']}: {result['status']} ({detail})")

    default_result = {"status": "error", "reason": "sonuc_alinamadi", "gap_count": None, "gaps_px": []}
    upload_list = [asin for asin in asins if results.get(asin, default_result)["status"] == "upload"]
    delete_list = [asin for asin in asins if results.get(asin, default_result)["status"] == "delete"]
    error_list = [asin for asin in asins if results.get(asin, default_result)["status"] == "error"]

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["asin", "status", "reason", "gap_count", "gaps_px"])
        for asin in asins:
            result = results.get(asin, default_result)
            writer.writerow([asin, result["status"], result["reason"], result["gap_count"], result.get("gaps_px", [])])

    print(f"\nYuklenecek ({len(upload_list)}): {', '.join(upload_list) if upload_list else '-'}")
    print(f"Silinecek  ({len(delete_list)}): {', '.join(delete_list) if delete_list else '-'}")
    if error_list:
        print(f"Hatali     ({len(error_list)}): {', '.join(error_list)}")
    print(f"\nDetayli sonuc: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
