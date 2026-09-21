"""500k'ya kadar taranan Keepa Finder havuzundaki (64.215 benzersiz ASIN)
TUM ASIN'leri, AsinSeeker'in "Temizle" mantigiyla (kesinti + 1 yillik veri
sarti) API uzerinden tek tek kontrol eder.

NEDEN AYRI BIR SCRIPT (GUI degil)?
Bu is Keepa'nin token yenilenme hizina (dogrulandi: 25 token/dk, ASIN basina
~1.3 token) baglica COK UZUN surer (tum havuz icin ~54 saat / ~2.25 gun
surekli calisma) -- bu yuzden kesintiye dayanikli, ARADAN KALDIGI YERDEN
DEVAM edebilen, ilerlemeyi ANINDA diske yazan bir arka plan islemi olarak
tasarlandi (AsinSeeker'in Temizle sekmesiyle AYNI mantik, sadece GUI'siz).

CALISTIRMA: python full_pool_check.py
DURDURMA: Ctrl+C (ya da process'i kapat) -- guvenlidir, sonraki calistirmada
kaldigi yerden (zaten sonuclar.csv'de olan ASIN'leri atlayarak) devam eder.

CIKTI (keepa_full_kontrol/ klasorunde):
  sonuclar.csv          -- HER ASIN icin durum/sebep/gap sayisi/oncelik puani (aninda yazilir)
  uygun.txt             -- SADECE "upload" (temiz) cikanlar, BULUNMA SIRASINA gore (denetim/log amacli)
  uygun_siralanmis.txt  -- AYNI liste ama ONCELIK PUANINA gore (en iyisi en ustte) siralanmis --
                           Easy'e Gonder sekmesine BUNU vermek, en guvenli/karli adaylarin once
                           gitmesini saglar. Periyodik olarak yeniden yazilir (tum tarama bitmeden
                           de kullanılabilir).
"""

import csv
import socket
import sys
import threading
import time
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
from keepa_check import keepa_check_detailed_api  # noqa: E402
from process_lock import ProcessLock  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
# Birden fazla kaynak dosyasi -- ORTAK HAVUZ (keepa_sync/ortak_asin_havuzu.txt)
# her yeni "ASIN Bul" taramasinin (eski 500k taramasi dahil, YENI Buy Box
# stratejileri dahil) sonuclarini otomatik biriktirir; ilk 500k taramasinin
# KENDI tum_asinler.txt'i de ayrica tutuluyor cunku havuz senkronu bir ara
# 5.378 ASIN geriden kalmisti (canli dogrulandi) -- iki dosyayi BIRLESTIRIP
# tekilleştirerek hicbir ASIN'i kaybetmiyoruz. Gelecekte yeni bir strateji
# klasoru (keepa_sync/ icinde) eklenirse, oradan bulunanlar zaten ORTAK
# HAVUZA da yazildigi icin BURAYA ayrica dosya eklemeye GEREK YOK.
SOURCE_ASIN_FILES = [
    BASE_DIR / "keepa_sync" / "keepa_arama_20260913_005808" / "tum_asinler.txt",
    BASE_DIR / "keepa_sync" / "ortak_asin_havuzu.txt",
]
OUTPUT_DIR = BASE_DIR / "keepa_full_kontrol"
RESULTS_CSV = OUTPUT_DIR / "sonuclar.csv"
# sonuclar.csv makineler arasi SENKRONIZE EDILMIYORDU (keepa_sync/ sadece
# giris havuzunu senkronluyor) -- iki makinede bu script paralel calisirsa
# her biri ayni ASIN'leri bilmeden tekrar kontrol edip API token'i cifte
# harcıyordu. Bu yuzden her makine, kendi sonuclarinin bir kopyasini de
# hostname'e gore ayri bir dosyada keepa_sync/ altina yazar (o repo zaten
# periyodik push/pull ediliyor) -- load_already_done() TUM makinelerin
# kopyalarini birlestirip okur.
SYNCED_RESULTS_CSV = BASE_DIR / "keepa_sync" / f"sonuclar_{socket.gethostname()}.csv"
UPLOAD_TXT = OUTPUT_DIR / "uygun.txt"
# En yuksek "oncelik puani"ndan (compute_priority_score) en dusuge dogru
# siralanmis hali -- Easy'e Gonder sekmesine BUNU vermek, en guvenli/karli
# adaylarin ONCE gitmesini saglar. Periyodik olarak (asagida) yeniden yazilir.
UPLOAD_SORTED_TXT = OUTPUT_DIR / "uygun_siralanmis.txt"
PREVIOUSLY_CHECKED_FILES = [
    BASE_DIR / "erhan_kontrol" / "erhan_sonuclar.csv",
]
# EasyCentral'a ZATEN gonderilmis ASIN'ler -- bunlari tekrar Keepa ile kontrol
# etmenin bir anlami yok (o gemi kalkti, EasyCentral kendi kararini zaten verdi;
# tekrar "uygun" cikarsa bile ikinci kez gonderilmeyecek). Duz ASIN listesi
# (durum bilgisi yok, sadece "bunlari atla" icin).
EXCLUDE_ASIN_FILES = [
    BASE_DIR / "keepa_sync" / "easycentral_batch_1.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_2.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_3.txt",
]
# run_web_prefilter.py'nin (Keepa web sitesindeki Product Viewer'i, API
# token'larindan AYRI bir kotayla kullanarak) onceden hesapladigi "1 yillik
# veri sarti" sonuclari -- has_full_year=False olanlar icin API'ye HIC
# GITMEDEN dogrudan "yillik_secenek_yok" yazariz (ayni API'nin soyleyecegi
# SEYIN AYNISI, sadece bedava/hizli bir kaynaktan).
WEB_PREFILTER_CSV = OUTPUT_DIR / "web_onfiltre.csv"

# ASIN basina olculen gercek maliyet (canli olculdu: 20 ASIN icin 1500->1482
# token dustu, o sirada ~7 token da yenilendi -> ~25 token / 20 ASIN = 1.25).
# Bunu bilerek biraz yukari yuvarlayip GUVENLI tarafta kaliyoruz.
ESTIMATED_TOKENS_PER_ASIN = 1.4
WORKERS = 3
TOKEN_CHECK_EVERY = 25  # bu kadar istekte bir gercek token durumunu (ucretsiz) kontrol et
MIN_TOKENS_BEFORE_PAUSE = 50

write_lock = threading.Lock()
progress_lock = threading.Lock()
request_count = 0
api_key = None
web_prefilter_no_year = set()


def load_target_asins():
    asins = []
    seen = set()
    for path in SOURCE_ASIN_FILES:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for line in f:
                asin = line.strip().upper()
                if asin and asin not in seen:
                    seen.add(asin)
                    asins.append(asin)
    return asins


MAX_HATA_RETRY = 3  # bu kadar basarisiz denemeden sonra ASIN kalici olarak atlanir


def load_already_done():
    done = {}
    hata_counts = {}
    synced_dir = BASE_DIR / "keepa_sync"
    other_machine_files = sorted(synced_dir.glob("sonuclar_*.csv")) if synced_dir.exists() else []
    files = [RESULTS_CSV] + other_machine_files + PREVIOUSLY_CHECKED_FILES
    for path in files:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Sert bir kill/crash sonrasi son satir YARIM kalmis olabilir
            # (flush var ama fsync yok) -- bozuk son satiri atlayip devam
            # ediyoruz, tum dosyayi reddedip baslangici KILITLEMIYORUZ.
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as error:
                    print(f"  ({path.name}: bozuk/yarim son satir atlandi -- {error})")
                    break
                asin = (row.get("asin") or "").strip().upper()
                if not asin:
                    continue
                if row.get("status") == "hata":
                    hata_counts[asin] = hata_counts.get(asin, 0) + 1
                elif row.get("status") not in (None, "", "durduruldu"):
                    done[asin] = row
    # Surekli hata veren ASIN'ler (MAX_HATA_RETRY'yi asan) her calistirmada
    # tekrar denenip sonuclar.csv'ye YENI bir "hata" satiri eklemesin diye
    # kalici olarak "done" sayiliyor -- ASIN listesi cok buyudukce dosyanin
    # sinirsiz buyumesini onler.
    for asin, count in hata_counts.items():
        if count >= MAX_HATA_RETRY and asin not in done:
            done[asin] = {"asin": asin, "status": "hata", "reason": f"{count}x basarisiz, atlaniyor"}
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


def rewrite_sorted_upload_list():
    """sonuclar.csv'deki TUM 'upload' satirlarini oncelik puanina (yuksekten
    dusuge) gore siralayip uygun_siralanmis.txt'ye yazar. Puani olmayan/eksik
    olanlar (eski, puanlama eklenmeden once kontrol edilmis ASIN'ler) listenin
    EN SONUNA konur -- cezalandirmak icin degil, "bilinmiyor" oldugu icin."""
    rows = []
    if not RESULTS_CSV.exists():
        return
    with RESULTS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("status") != "upload":
                continue
            raw_score = row.get("score")
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = -1.0
            rows.append((score, row["asin"]))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    UPLOAD_SORTED_TXT.write_text("\n".join(asin for _score, asin in rows) + ("\n" if rows else ""), encoding="utf-8")


def _parse_percent(text):
    text = (text or "").strip().replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _parse_number(text):
    text = (text or "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def compute_web_prescore(row):
    """AI karar motorunun (compute_priority_score) web-export verisiyle
    calisan kucuk kardesi -- API'ye HENUZ gitmemis ama 1 yillik gecmisi
    OLAN adaylari, hangisinin API kuyrugunda ONCE kontrol edilmesi
    gerektigine karar vermek icin kullanilir. Amac: kisitli API butcesini,
    zaten en umut vaat eden adaylara ONCE harcamak -- boylece "temiz" liste
    tum tarama bitmeden, cok daha erken buyumeye baslar.

    Ayni compute_priority_score gibi 0-100 araliginda ama SADECE web
    export'unun sagladigi (daha kaba/ozet) sinyallerden hesaplanir:
    - %40 Stok surekliligi (New: 90 gun OOS%, dusuk olsun)
    - %35 Satis hizi (Sales Rank Drops 90 gun, yuksek olsun)
    - %25 Rekabet yogunlugu (Total Offer Count, dusuk olsun)
    """
    score = 0.0

    oos = _parse_percent(row.get("new_oos_90"))
    if oos is not None:
        score += 40 * max(0.0, 1 - oos / 100.0)
    else:
        score += 40 * 0.5

    drops = _parse_number(row.get("sales_rank_drops_90"))
    if drops is not None:
        score += 35 * min(1.0, drops / 20.0)
    else:
        score += 35 * 0.5

    offer_count = _parse_number(row.get("total_offer_count"))
    if offer_count is not None and offer_count >= 1:
        score += 25 * max(0.0, 1 - (offer_count - 1) / 9.0)
    else:
        score += 25 * 0.5

    return round(score, 1)


def load_web_prefilter_data():
    """web_onfiltre.csv'yi bir kere okuyup iki sey dondurur:
    1) no_year: has_full_year=False olan ASIN'lerin kumesi (API'ye HIC
       gitmeyecekler -- kesin/net bir durum).
    2) prescores: has_full_year=True olan ASIN'ler icin, API kontrol
       SIRASINI belirlemekte kullanilacak kaba oncelik puani (yuksek =
       API kuyrugunda ONCE kontrol edilsin)."""
    no_year = set()
    prescores = {}
    if not WEB_PREFILTER_CSV.exists():
        return no_year, prescores
    with WEB_PREFILTER_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            asin = row.get("asin")
            if not asin:
                continue
            if (row.get("has_full_year") or "").strip() == "False":
                no_year.add(asin)
            elif (row.get("has_full_year") or "").strip() == "True":
                prescores[asin] = compute_web_prescore(row)
    return no_year, prescores


def check_one(asin):
    if asin in web_prefilter_no_year:
        return asin, {
            "asin": asin, "status": "delete", "reason": "yillik_secenek_yok(web_onfiltre)",
            "gap_count": None, "score": None,
        }, None
    maybe_throttle()
    try:
        result = keepa_check_detailed_api(
            asin, api_key=api_key, require_year=True, check_gaps=True, check_dead_stock=False,
            check_customs_risk=True, check_target_market=True,
        )
        return asin, result, None
    except Exception as error:
        return asin, None, f"{type(error).__name__}: {error}"


def main():
    global api_key, web_prefilter_no_year
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lock = ProcessLock(BASE_DIR / "full_pool_check.lock")
    try:
        lock.acquire()
    except RuntimeError as error:
        print(f">>> {error}")
        return
    api_key = kf.load_api_key()
    web_prefilter_no_year, web_prescores = load_web_prefilter_data()

    all_asins = load_target_asins()
    already_done = load_already_done()
    already_sent_to_easy = set()
    for path in EXCLUDE_ASIN_FILES:
        if path.exists():
            already_sent_to_easy |= set(l.strip().upper() for l in path.read_text(encoding="utf-8").splitlines() if l.strip())
    todo = [asin for asin in all_asins if asin not in already_done and asin not in already_sent_to_easy]

    # AI karar motoru: API kuyrugunu, web on-filtresinden gelen kaba oncelik
    # puanina gore YENIDEN SIRALA -- boylece kisitli API butcesi ONCE en
    # umut vaat eden adaylara harcanir, "temiz" liste cok daha erken
    # buyumeye baslar. Web verisi olmayanlar (henuz web'den gecmemis)
    # notr puanla ORTAYA yerlestirilir -- ne cezalandirilir ne kayirilir.
    neutral_score = 50.0
    todo.sort(key=lambda asin: web_prescores.get(asin, neutral_score), reverse=True)

    print(f"Toplam hedef ASIN: {len(all_asins)}")
    print(f"Zaten Easy'e gonderilmis (atlanacak): {len(already_sent_to_easy)}")
    print(f"Daha once kontrol edilmis (atlanacak): {len(already_done)}")
    print(f"Web on-filtreden '1 yillik yok' cikan (API'ye gitmeyecek): {len(web_prefilter_no_year)}")
    print(f"Bu oturumda kontrol edilecek: {len(todo)}")

    is_new_file = not RESULTS_CSV.exists()
    results_file = RESULTS_CSV.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(results_file, fieldnames=["asin", "status", "reason", "gap_count", "score"])
    if is_new_file:
        writer.writeheader()
        results_file.flush()

    SYNCED_RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    is_new_synced_file = not SYNCED_RESULTS_CSV.exists()
    synced_file = SYNCED_RESULTS_CSV.open("a", newline="", encoding="utf-8")
    synced_writer = csv.DictWriter(synced_file, fieldnames=["asin", "status", "reason", "gap_count", "score"])
    if is_new_synced_file:
        synced_writer.writeheader()
        synced_file.flush()

    upload_file = UPLOAD_TXT.open("a", encoding="utf-8")

    # Zaten "upload" olarak bilinen (onceki oturumlardan) ASIN'ler uygun.txt'de
    # zaten var mi diye tekrar tekrar eklemeyelim -- ilk acilista bir kere
    # senkronize edelim.
    existing_upload_asins = set()
    if UPLOAD_TXT.exists():
        existing_upload_asins = set(l.strip() for l in UPLOAD_TXT.read_text(encoding="utf-8").splitlines() if l.strip())
    for asin, row in already_done.items():
        if row.get("status") == "upload" and asin not in existing_upload_asins:
            upload_file.write(asin + "\n")
            existing_upload_asins.add(asin)
    upload_file.flush()

    done_count = 0
    upload_count = 0
    delete_count = 0
    error_count = 0
    start_time = time.monotonic()

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(check_one, asin): asin for asin in todo}
            for future in as_completed(futures):
                asin, result, error = future.result()
                done_count += 1
                with write_lock:
                    if result is not None:
                        row = {
                            "asin": asin, "status": result["status"],
                            "reason": result["reason"], "gap_count": result["gap_count"],
                            "score": result.get("score"),
                        }
                        writer.writerow(row)
                        synced_writer.writerow(row)
                        if result["status"] == "upload":
                            upload_count += 1
                            upload_file.write(asin + "\n")
                            upload_file.flush()
                        else:
                            delete_count += 1
                    else:
                        error_count += 1
                        row = {"asin": asin, "status": "hata", "reason": error, "gap_count": "", "score": ""}
                        writer.writerow(row)
                        synced_writer.writerow(row)
                    results_file.flush()
                    synced_file.flush()

                if done_count % 50 == 0 or done_count == len(todo):
                    elapsed = time.monotonic() - start_time
                    rate_per_min = done_count / (elapsed / 60) if elapsed > 0 else 0
                    remaining = len(todo) - done_count
                    eta_min = remaining / rate_per_min if rate_per_min > 0 else float("inf")
                    print(
                        f"[{done_count}/{len(todo)}] upload={upload_count} delete={delete_count} "
                        f"hata={error_count} -- hiz: {rate_per_min:.1f} ASIN/dk -- "
                        f"tahmini kalan sure: {eta_min:.0f} dk"
                    )
                    rewrite_sorted_upload_list()
    finally:
        results_file.close()
        synced_file.close()
        upload_file.close()
        rewrite_sorted_upload_list()
        lock.release()
        print(">>> Bitti / durduruldu.")
        print(f"Bu oturumda: {done_count} kontrol edildi, {upload_count} upload, {delete_count} delete, {error_count} hata")


if __name__ == "__main__":
    main()
