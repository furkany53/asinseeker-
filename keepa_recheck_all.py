import csv
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from easycentral_keepa_bot import start_chrome_for_attachment  # noqa: E402
from keepa_check import keepa_check_detailed  # noqa: E402

SCREENSHOT_DIR = BASE_DIR / "keepa_screenshots"
ASIN_SOURCE_FILE = BASE_DIR / "havuz_asin_listesi.txt"
RECHECK_RESULTS_CSV = BASE_DIR / "keepa_recheck_sonuclari.csv"
UPLOAD_LIST_TXT = BASE_DIR / "yukleme_listesi.txt"
DISCREPANCIES_CSV = BASE_DIR / "keepa_recheck_celiskiler.csv"
ERROR_LOG = BASE_DIR / "keepa_recheck_hatalar.txt"
PROGRESS_LOG = BASE_DIR / "keepa_recheck_progress.txt"

# RAM'i yormamak icin ayni anda acilan Keepa sekmesi sayisi (kullanici istegi: 4-5).
MAX_PARALLEL = 4

SUFFIX_TO_STATUS = {
    "_YUKLE": ("upload", "kesintisiz"),
    "_SIL_YILYOK": ("delete", "yillik_secenek_yok"),
    "_SIL_GRAFIKYOK": ("delete", "grafik_yuklenemedi"),
    "_SIL_VERIYOK": ("delete", "veri_yok"),
    "_SIL_CIZGIYOK": ("delete", "cizgi_bulunamadi"),
    "_SIL_STOKYOK": ("delete", "stok_yok"),
    "_SIL": ("delete", "kesinti_var"),
}


def load_asin_list():
    lines = [line.strip() for line in ASIN_SOURCE_FILE.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line]


def load_previous_statuses():
    latest = {}
    if not SCREENSHOT_DIR.exists():
        return {}
    for file in SCREENSHOT_DIR.iterdir():
        match = re.match(r"^([A-Z0-9]{9,10})(_[A-Z_]+)?$", file.stem)
        if not match:
            continue
        asin, suffix = match.group(1), match.group(2) or ""
        if suffix == "_HATA":
            continue
        mapped = SUFFIX_TO_STATUS.get(suffix)
        if mapped is None:
            continue
        mtime = file.stat().st_mtime
        existing = latest.get(asin)
        if existing is None or mtime > existing[2]:
            latest[asin] = (mapped[0], mapped[1], mtime)
    return {asin: (status, reason) for asin, (status, reason, _) in latest.items()}


def main():
    asins = load_asin_list()
    previous = load_previous_statuses()
    print(f"Toplam ASIN (havuz listesi): {len(asins)}")
    print(f"Paralel pencere sayisi: {MAX_PARALLEL}")

    start_chrome_for_attachment(start_url="about:blank", headless=True)

    fieldnames = [
        "asin", "old_status", "old_reason", "new_status", "new_reason",
        "gap_count", "gaps_px", "changed",
    ]
    results = []
    discrepancies = []
    errors = []
    lock = threading.Lock()
    progress_file = PROGRESS_LOG.open("w", encoding="utf-8")
    completed = 0

    def process(asin):
        old_status, old_reason = previous.get(asin, ("yeni", "daha_once_kontrol_edilmemis"))
        try:
            result = keepa_check_detailed(asin)
            new_status, new_reason = result["status"], result["reason"]
            changed = old_status != "yeni" and new_status != old_status
            row = {
                "asin": asin,
                "old_status": old_status,
                "old_reason": old_reason,
                "new_status": new_status,
                "new_reason": new_reason,
                "gap_count": result["gap_count"],
                "gaps_px": result["gaps_px"],
                "changed": changed,
            }
            line = f"{asin}: {old_status}/{old_reason} -> {new_status}/{new_reason}" + (" [DEGISTI]" if changed else "")
            return row, line, None
        except Exception as error:
            trace = traceback.format_exc()
            row = {
                "asin": asin,
                "old_status": old_status,
                "old_reason": old_reason,
                "new_status": "hata",
                "new_reason": f"{type(error).__name__}: {error}",
                "gap_count": None,
                "gaps_px": "",
                "changed": True,
            }
            line = f"{asin}: HATA {type(error).__name__}: {error}"
            return row, line, (asin, error, trace)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {executor.submit(process, asin): asin for asin in asins}
        for future in as_completed(futures):
            row, line, error_info = future.result()
            with lock:
                completed += 1
                results.append(row)
                if row["changed"]:
                    discrepancies.append(row)
                if error_info:
                    errors.append(error_info)
                full_line = f"[{completed}/{len(asins)}] {line}"
                print(full_line)
                progress_file.write(full_line + "\n")
                progress_file.flush()

    progress_file.close()

    order = {asin: index for index, asin in enumerate(asins)}
    results.sort(key=lambda row: order.get(row["asin"], 0))
    discrepancies.sort(key=lambda row: order.get(row["asin"], 0))

    with RECHECK_RESULTS_CSV.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    with DISCREPANCIES_CSV.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(discrepancies)

    if errors:
        with ERROR_LOG.open("w", encoding="utf-8") as file:
            for asin, error, trace in errors:
                file.write(f"{'=' * 70}\nASIN: {asin}\n{trace}\n")

    upload_asins = [row["asin"] for row in results if row["new_status"] == "upload"]
    upload_asins_ordered = [asin for asin in asins if asin in set(upload_asins)]
    with UPLOAD_LIST_TXT.open("w", encoding="utf-8") as file:
        file.write("\n".join(upload_asins_ordered) + ("\n" if upload_asins_ordered else ""))

    print("\n=== OZET ===")
    print(f"Toplam kontrol edilen: {len(results)}")
    print(f"Upload (temiz): {len(upload_asins_ordered)}")
    print(f"Delete: {sum(1 for r in results if r['new_status'] == 'delete')}")
    print(f"Hata: {len(errors)}")
    print(f"Degisen/yeni sonuc sayisi: {len(discrepancies)}")
    print(f"Temiz liste: {UPLOAD_LIST_TXT}")
    print(f"Tum sonuclar: {RECHECK_RESULTS_CSV}")
    print(f"Celiskiler: {DISCREPANCIES_CSV}")
    if errors:
        print(f"Hata detaylari: {ERROR_LOG}")


if __name__ == "__main__":
    main()
