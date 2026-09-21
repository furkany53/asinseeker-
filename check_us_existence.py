"""EasyCentral'a gonderilmeden ONCE, "upload" (temiz) havuzundaki her ASIN'in
hedef pazarda (Amazon.com, Keepa domain=1) GERCEKTEN var olup olmadigini
kontrol eder.

NEDEN: EasyCentral'a gonderdigimiz "temiz" ASIN'lerin cogu "Satistan
kaldirilmis" diye eleniyordu -- canli test edildi (99 ornek ASIN, keepa_sync/
disindaki bir dogrulama), bunun JP tarafiyla (kesinti/telef/gumruk) hicbir
ilgisi yoktu: 99 ASIN'in 99'u da Amazon.com'da (domain=1) HIC BULUNAMADI.
EasyCentral'in "satistan kaldirilmis" dedigi sey, kaynak (JP) listing'in degil,
hedef pazardaki KARSILIGIN yoklugu. Bu script o kontrolu (domain=1'de var mi)
yapar, EasyCentral'in kisitli gunluk tarama kotasini bosa harcamadan once.

CALISTIRMA: python check_us_existence.py
DURDURMA: Ctrl+C -- guvenlidir, sonraki calistirmada kaldigi yerden devam eder
(zaten us_varlik_sonuclari.csv'de olan ASIN'leri atlar).

CIKTI (keepa_full_kontrol/ klasorunde):
  us_varlik_sonuclari.csv  -- HER ASIN icin var/yok sonucu (aninda yazilir)
  us_onaylanmis.txt        -- SADECE Amazon.com'da VAR cikanlar -- EasyCentral'a
                               gonderilecek bir sonraki parti BUNDAN secilmeli.
"""

import csv
import gzip
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import keepa_api_settings  # noqa: E402
import keepa_finder as kf  # noqa: E402
from process_lock import ProcessLock  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "keepa_full_kontrol"
CANDIDATES_FILE = OUTPUT_DIR / "uygun_siralanmis.txt"
# Zaten EasyCentral'a gonderilmis (bir daha kontrol etmeye gerek yok) --
# full_pool_check.py'deki EXCLUDE_ASIN_FILES ile AYNI mantik/dosyalar.
EXCLUDE_ASIN_FILES = [
    BASE_DIR / "keepa_sync" / "easycentral_batch_1.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_2.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_3.txt",
]
RESULTS_CSV = OUTPUT_DIR / "us_varlik_sonuclari.csv"
CONFIRMED_TXT = OUTPUT_DIR / "us_onaylanmis.txt"

US_DOMAIN = 1  # amazon.com
# 2026-09-16'da WORKERS=3 + TOKEN_CHECK_EVERY=25 iken full_pool_check.py ile
# AYNI ANDA calisirken 429/400 patlamasina yol acmisti (27.854 ASIN'in
# 26.176'si hata olarak bitti) -- o yuzden WORKERS=1'e dusurulmustu.
# full_pool_check.py artik TAMAMEN BITTI (havuzda islenecek ASIN kalmadi,
# 2026-09-17), token catismasi riski yok -- WORKERS tekrar 3'e cikarildi.
WORKERS = 3
TOKEN_CHECK_EVERY = 10
MIN_TOKENS_BEFORE_PAUSE = 50

write_lock = threading.Lock()
progress_lock = threading.Lock()
request_count = 0
api_key = None


def load_candidates():
    excluded = set()
    for path in EXCLUDE_ASIN_FILES:
        if path.exists():
            excluded |= set(
                l.strip().upper() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()
            )
    asins = []
    seen = set()
    for line in CANDIDATES_FILE.read_text(encoding="utf-8").splitlines():
        asin = line.strip().upper()
        if asin and asin not in seen and asin not in excluded:
            seen.add(asin)
            asins.append(asin)
    return asins


def load_already_checked():
    done = {}
    if not RESULTS_CSV.exists():
        return done
    with RESULTS_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error:
                break
            asin = (row.get("asin") or "").strip().upper()
            if asin:
                done[asin] = row
    return done


def token_wait_seconds():
    data = keepa_api_settings.check_account_status(api_key)
    tokens_left = data.get("tokensLeft")
    refill_rate = data.get("refillRate") or 1
    if tokens_left is not None and tokens_left < MIN_TOKENS_BEFORE_PAUSE:
        deficit = MIN_TOKENS_BEFORE_PAUSE - tokens_left
        return max(5, (deficit / refill_rate) * 60), tokens_left, refill_rate
    return 0, tokens_left, refill_rate


def maybe_throttle():
    global request_count
    with progress_lock:
        request_count += 1
        count = request_count
    if count % TOKEN_CHECK_EVERY == 0:
        try:
            wait_seconds, tokens_left, refill_rate = token_wait_seconds()
        except Exception:
            return
        if wait_seconds:
            print(
                f"  (token azaldi: {tokens_left} kalan, {refill_rate}/dk yenileniyor -- "
                f"{wait_seconds:.0f} sn bekleniyor...)"
            )
            time.sleep(wait_seconds)


def check_exists_on_us(asin):
    """domain=1 (Amazon.com) icin en ucuz varlik kontrolu -- stats/history
    ISTEMEDEN sadece temel urun kaydini ceker (yine de 1 token, Keepa'da daha
    ucuzu yok -- fiyatlandirma istek basina degil urun basina, bkz. proje
    icinde canli dogrulanan token testi)."""
    params = {"key": api_key, "domain": US_DOMAIN, "asin": asin}
    url = "https://api.keepa.com/product?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url)
    for attempt in range(10):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8"))
                break
        except urllib.error.HTTPError as error:
            # 429 (rate limit) BEKLENEN bir durum -- ama full_pool_check.py
            # ile AYNI token bütçesini paylaşırken (canli dogrulandi,
            # 2026-09-16) Keepa ara sira 400 de donduruyor (muhtemelen ayni
            # kok sebep -- kota baskisi altinda tutarsiz hata kodu). Ikisini
            # de RETRYABLE sayiyoruz; digerleri (401/403/vs.) hala fatal.
            if error.code in (429, 400) and attempt < 9:
                time.sleep(min(30 * (attempt + 1), 300))
                continue
            raise
        except urllib.error.URLError as error:
            # DNS/baglanti hatalari (getaddrinfo failed, baglanti zorla
            # kapatildi vb.) -- full_pool_check.py'nin 108K'lik taramasinda
            # CANLI dogrulandi: "hata" satirlarinin %95'i buydu, Keepa'nin
            # kendisiyle ilgisi yoktu. Kisa bir bekleme yeterli.
            if attempt < 9:
                time.sleep(min(3 * (attempt + 1), 30))
                continue
            raise RuntimeError(f"Ag/DNS hatasi devam ediyor ({asin}): {error}") from error
    else:
        raise RuntimeError(f"Kota asimi devam ediyor ({asin})")

    products = data.get("products") or []
    exists = bool(products and products[0] is not None and products[0].get("title"))
    return exists


def check_one(asin):
    try:
        maybe_throttle()
        exists = check_exists_on_us(asin)
        return asin, exists, None
    except Exception as error:
        return asin, None, f"{type(error).__name__}: {error}"


def main():
    global api_key
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lock = ProcessLock(BASE_DIR / "check_us_existence.lock")
    try:
        lock.acquire()
    except RuntimeError as error:
        print(f">>> {error}")
        return
    api_key = kf.load_api_key()

    all_candidates = load_candidates()
    already_checked = load_already_checked()
    todo = [a for a in all_candidates if a not in already_checked]

    print(f"Toplam aday (henuz Easy'e gonderilmemis, uygun): {len(all_candidates)}")
    print(f"Daha once bu testten gecmis: {len(already_checked)}")
    print(f"Bu oturumda kontrol edilecek: {len(todo)}")

    is_new_file = not RESULTS_CSV.exists()
    results_file = RESULTS_CSV.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(results_file, fieldnames=["asin", "exists_on_us"])
    if is_new_file:
        writer.writeheader()
        results_file.flush()

    confirmed_file = CONFIRMED_TXT.open("a", encoding="utf-8")
    existing_confirmed = set()
    if CONFIRMED_TXT.exists():
        existing_confirmed = set(
            l.strip() for l in CONFIRMED_TXT.read_text(encoding="utf-8").splitlines() if l.strip()
        )
    for asin, row in already_checked.items():
        if row.get("exists_on_us") == "True" and asin not in existing_confirmed:
            confirmed_file.write(asin + "\n")
            existing_confirmed.add(asin)
    confirmed_file.flush()

    done_count = 0
    exists_count = 0
    not_exists_count = 0
    error_count = 0
    start_time = time.monotonic()

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(check_one, asin): asin for asin in todo}
            for future in as_completed(futures):
                asin, exists, error = future.result()
                done_count += 1
                with write_lock:
                    if error is not None:
                        error_count += 1
                    else:
                        writer.writerow({"asin": asin, "exists_on_us": exists})
                        if exists:
                            exists_count += 1
                            confirmed_file.write(asin + "\n")
                            confirmed_file.flush()
                        else:
                            not_exists_count += 1
                    results_file.flush()

                if done_count % 50 == 0 or done_count == len(todo):
                    elapsed = time.monotonic() - start_time
                    rate_per_min = done_count / (elapsed / 60) if elapsed > 0 else 0
                    remaining = len(todo) - done_count
                    eta_min = remaining / rate_per_min if rate_per_min > 0 else float("inf")
                    print(
                        f"[{done_count}/{len(todo)}] var={exists_count} yok={not_exists_count} "
                        f"hata={error_count} -- ETA: {eta_min:.0f} dk"
                    )
    finally:
        results_file.close()
        confirmed_file.close()
        lock.release()
        print(">>> Bitti / durduruldu.")
        print(
            f"Bu oturumda: {done_count} kontrol edildi, {exists_count} US'de var, "
            f"{not_exists_count} US'de yok, {error_count} hata"
        )


if __name__ == "__main__":
    main()
