"""keepa_web_prefilter.py'yi TUM kalan (henuz API ile kontrol edilmemis)
ASIN havuzu uzerinde, 1000'lik dilimler halinde calistirip sonuclari
keepa_full_kontrol/web_onfiltre.csv dosyasina biriktirir.

NEDEN SADECE "Tracking since" KULLANILIYOR (diger alanlar degil)?
Web export'unda "New: 90 days OOS", "Total Offer Count" gibi alanlar da var
ama bunlar bizim ASIL "kesinti" (gap) kontrolumuzun YERINE GECECEK kadar
hassas degil (sadece ozet/90-gunluk istatistik, ham zaman serisi yok).
"Tracking since" ise FARKLI -- API'nin "1 yillik veri sarti" kontrolu ile
MATEMATIKSEL OLARAK AYNI SEYI soyler (Keepa urunu ne zamandir takip
ediyor), yani bunu API'ye hic sormadan, %100 guvenle onceden bilebiliriz.
Sadece BU alan, gercek bir API cagrisini ATLAMAK icin kullanilir; digerleri
sadece kayit altina alinir (ileride puanlamayi zenginlestirmek icin).

CALISTIRMA: python run_web_prefilter.py
DURDURULABILIR/DEVAM EDILEBILIR: web_onfiltre.csv'de zaten olan ASIN'ler
atlanir.
"""

import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import full_pool_check as fpc  # noqa: E402
import keepa_web_prefilter as wp  # noqa: E402
from process_lock import ProcessLock  # noqa: E402

OUT_CSV = fpc.OUTPUT_DIR / "web_onfiltre.csv"
BATCH_SIZE = 1000
FIELDNAMES = [
    "asin", "tracking_since", "has_full_year", "total_offer_count",
    "new_oos_90", "buy_box_oos_90", "sales_rank_drops_90", "sales_rank_current",
]


def has_full_year(tracking_since_text):
    if not tracking_since_text:
        return None
    try:
        dt = datetime.strptime(tracking_since_text.strip(), "%Y/%m/%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age_days = (datetime.now(timezone.utc) - dt).days
    return age_days >= 365


def load_done_asins():
    if not OUT_CSV.exists():
        return set()
    with OUT_CSV.open(encoding="utf-8") as f:
        return {row["asin"] for row in csv.DictReader(f)}


def main():
    fpc.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lock = ProcessLock(fpc.BASE_DIR / "run_web_prefilter.lock")
    try:
        lock.acquire()
    except RuntimeError as error:
        print(f">>> {error}")
        return

    all_asins = fpc.load_target_asins()
    already_checked = fpc.load_already_done()  # zaten API ile kontrol edilmis
    already_sent = set()
    for path in fpc.EXCLUDE_ASIN_FILES:
        if path.exists():
            already_sent |= set(l.strip().upper() for l in path.read_text(encoding="utf-8").splitlines() if l.strip())
    already_web_checked = load_done_asins()

    todo = [
        a for a in all_asins
        if a not in already_checked and a not in already_sent and a not in already_web_checked
    ]
    print(f"Web on-filtre icin hedef: {len(todo)} ASIN ({len(all_asins)} toplam - "
          f"{len(already_checked)} API'de kontrol edilmis - {len(already_sent)} Easy'e gonderilmis - "
          f"{len(already_web_checked)} zaten web'den kontrol edilmis)")

    is_new = not OUT_CSV.exists()
    out_file = OUT_CSV.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_file, fieldnames=FIELDNAMES)
    if is_new:
        writer.writeheader()
        out_file.flush()

    total_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
    no_year_count = 0
    has_year_count = 0
    unknown_count = 0

    try:
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            print(f"[{batch_num}/{total_batches}] {len(batch)} ASIN'lik dilim isleniyor...")

            out_path = wp.STAGING_DIR / f"batch_{batch_num}.csv"
            ok = wp.export_batch(batch, out_path)
            if not ok:
                print(f"  UYARI: bu dilim export edilemedi, atlaniyor (daha sonra tekrar denenebilir).")
                continue

            records = wp.parse_export_csv(out_path)
            for asin in batch:
                info = records.get(asin, {})
                year_ok = has_full_year(info.get("tracking_since", ""))
                if year_ok is True:
                    has_year_count += 1
                elif year_ok is False:
                    no_year_count += 1
                else:
                    unknown_count += 1
                writer.writerow({
                    "asin": asin,
                    "tracking_since": info.get("tracking_since", ""),
                    "has_full_year": year_ok if year_ok is not None else "",
                    "total_offer_count": info.get("total_offer_count", ""),
                    "new_oos_90": info.get("new_oos_90", ""),
                    "buy_box_oos_90": info.get("buy_box_oos_90", ""),
                    "sales_rank_drops_90": info.get("sales_rank_drops_90", ""),
                    "sales_rank_current": info.get("sales_rank_current", ""),
                })
            out_file.flush()
            try:
                out_path.unlink()
            except Exception:
                pass
            print(f"  bitti -- su ana kadar: 1-yillik-yok={no_year_count}, 1-yillik-var={has_year_count}, bilinmiyor={unknown_count}")
    finally:
        out_file.close()
        lock.release()
        print(">>> Web on-filtre bitti/durduruldu.")
        print(f"Toplam: 1-yillik-yok={no_year_count} (API'ye hic gitmeyecek), "
              f"1-yillik-var={has_year_count} (API kontrolu hala gerekli), bilinmiyor={unknown_count}")


if __name__ == "__main__":
    main()
