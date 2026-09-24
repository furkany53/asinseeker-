"""Zayif ASIN <-> Yuksek skorlu ASIN yer degistirme secimi.

2026-09-19 GUNCELLEME (musteriden geldi): Magaza kotasi (25.000 paket limiti)
DOLU -- yeni ASIN gonderilemiyor, bu yuzden her yeni ekleme mutlaka bir
CIKARMAYLA es zamanli olmali (yer degistirme). Ayrica musteri ozellikle
RAKIP TURK SATICILARDAN (harvest_seller_asins.py / build_asin_kaynak_yontemi.py
ile etiketlenmis) toplanan ASIN'leri ONCELIKLENDIRMEK istiyor -- mantik: bir
rakip zaten o urunu satiyorsa, bu urunun gercekten satilabilir oldugunun
organik bir kanitidir (Keepa'nin tahmini metriklerinden daha guclu bir sinyal).

DOGRULANDI (2026-09-19): Keepa JP taramasindan gelen ASIN'ler ile EasyCentral
envanterindeki (inventory-list.csv) ASIN'ler AYNI ASIN uzayinda -- 2.352
dogrudan ortusme bulundu. Dogrudan ASIN eslestirmesi guvenle yapilabiliyor.

2026-09-22 GUNCELLEME: check_us_existence.py canli dogruladi -- EasyCentral'in
"satistan kaldirilmis" diye eledigi ASIN'lerin JP tarafiyla ilgisi yok, sadece
hedef pazarda (Amazon.com) o ASIN HIC YOK (99/99 ornekte dogrulandi, genel
oran ~%44 var/%56 yok). Bu yuzden ABD'de var oldugu KANITLANMAMIS hicbir ASIN
artik bu batch'e giremez -- EasyCentral'in kisitli tarama kotasini bosa
harcamamak icin.

Adimlar:
1. sonuclar.csv'den status=upload olan TUM ASIN'leri skoruyla birlikte al.
2. asin_kaynak_yontemi.csv'den SADECE rakip Turk satici kodlu (HAYAI, SABAN,
   DENIZ, AHMET, ISMAI, ALIMU, OMERS, PINAR, MUAMM, IBRAH, NURAY, YUSUF,
   HATIC) ASIN'lere filtrele.
3. Zaten gonderilmis (easycentral_batch_1/2/3.txt) VE zaten magazada canli
   olan (inventory-list.csv) ASIN'leri CIKAR.
4. us_onaylanmis.txt'de OLMAYAN (ABD'de varligi kanitlanmamis) ASIN'leri CIKAR.
5. Skora gore (en yuksekten) sirala.
6. dead_stock_confirmed.csv'deki zayif ASIN sayisi kadar (N) -- ya da rakip
   havuzunda o kadar yoksa mevcut TUMUNU -- "yeni batch" olarak yaz.

Cikti: keepa_full_kontrol/yer_degistirme_yeni_batch.txt
       keepa_full_kontrol/yer_degistirme_ozet.csv (asin, score, kaynak_kodu)
"""
import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SONUCLAR = BASE_DIR / "keepa_full_kontrol" / "sonuclar.csv"
KAYNAK_YONTEMI = BASE_DIR / "keepa_full_kontrol" / "asin_kaynak_yontemi.csv"
DEAD_STOCK = BASE_DIR / "keepa_full_kontrol" / "dead_stock_confirmed.csv"
INVENTORY = Path(r"C:\Users\onury\Downloads\inventory-list (1).csv")
US_ONAYLANMIS = BASE_DIR / "keepa_full_kontrol" / "us_onaylanmis.txt"
SENT_BATCHES = [
    BASE_DIR / "keepa_sync" / "easycentral_batch_1.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_2.txt",
    BASE_DIR / "keepa_sync" / "easycentral_batch_3.txt",
]
OUT_DIR = BASE_DIR / "keepa_full_kontrol"

RAKIP_KODLARI = {
    "HAYAI", "SABAN", "DENIZ", "AHMET", "ISMAI", "ALIMU",
    "OMERS", "PINAR", "MUAMM", "IBRAH", "NURAY", "YUSUF", "HATIC",
}

# ---------------------------------------------------------- 1) skorlar
asin_score = {}
asin_abd_dogrulandi = set()  # reason'inda "abd_dogrulandi" gecen ASIN'ler --
# full_pool_check.py'nin kendi check_target_market kontrolunden zaten
# gecmis, check_us_existence.py ile TEKRAR kontrol etmeye gerek yok.
with SONUCLAR.open(encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row["status"] != "upload":
            continue
        raw = row.get("score")
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue
        asin = row["asin"].strip()
        asin_score[asin] = score  # son gorulen kazanir
        if "abd_dogrulandi" in (row.get("reason") or ""):
            asin_abd_dogrulandi.add(asin)
        else:
            asin_abd_dogrulandi.discard(asin)

print(f"sonuclar.csv -- skorlu 'upload' ASIN sayisi (tekil): {len(asin_score)}")
print(f"Bunlardan full_pool_check.py'nin kendi ABD kontrolunden ZATEN gecmis (reason=uygun+abd_dogrulandi): {len(asin_abd_dogrulandi)}")

# ---------------------------------------------------------- 2) rakip kaynakli filtre
asin_kod = {}
with KAYNAK_YONTEMI.open(encoding="utf-8") as f:
    for row in csv.DictReader(f):
        kod = row.get("method_code", "").strip()
        if kod in RAKIP_KODLARI:
            asin_kod[row["asin"].strip()] = kod

print(f"asin_kaynak_yontemi.csv -- rakip Turk satici kodlu ASIN sayisi (toplam): {len(asin_kod)}")

rakip_scored = {a: s for a, s in asin_score.items() if a in asin_kod}
print(f"Bunlardan skorlu VE 'upload' (temiz) olanlar: {len(rakip_scored)}")

# ---------------------------------------------------------- 3) haric tut
already_sent = set()
for path in SENT_BATCHES:
    if path.exists():
        already_sent.update(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
print(f"Zaten gonderilmis (batch_1/2/3) ASIN sayisi: {len(already_sent)}")

live_in_store = set()
with INVENTORY.open(encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        a = (row.get("ASIN") or "").strip()
        if a:
            live_in_store.add(a)
print(f"Su an magazada canli ASIN sayisi: {len(live_in_store)}")

excluded = already_sent | live_in_store
candidates = {asin: score for asin, score in rakip_scored.items() if asin not in excluded}
print(f"\nRakip kaynakli, TEMIZ, HENUZ gonderilmemis aday sayisi: {len(candidates)}")

# ------------------------------------------- 3b) ABD'de var oldugu kanitlanmis
# Iki kaynaktan birlesir: (a) check_us_existence.py'nin backfill kütüphanesi
# (eski, check_target_market'ten ONCE "upload" olmus ASIN'ler icin), (b)
# full_pool_check.py'nin kendi ANLIK check_target_market kontrolunden gecmis
# ASIN'ler (reason=uygun+abd_dogrulandi) -- bunlar icin check_us_existence.py
# TEKRAR calismasin diye ayri kontrol edilmiyor.
us_confirmed = set(asin_abd_dogrulandi)
if US_ONAYLANMIS.exists():
    us_confirmed.update(
        l.strip() for l in US_ONAYLANMIS.read_text(encoding="utf-8").splitlines() if l.strip()
    )
print(
    f"check_us_existence.py -- ABD'de (Amazon.com) var oldugu kanitlanmis ASIN sayisi: {len(us_confirmed)} "
    f"({len(asin_abd_dogrulandi)} tanesi full_pool_check.py'nin kendi kontrolunden geldi)"
)

before_us_filter = len(candidates)
candidates = {asin: score for asin, score in candidates.items() if asin in us_confirmed}
print(
    f"ABD-varlik filtresinden sonra kalan aday sayisi: {len(candidates)} "
    f"(once: {before_us_filter} -- henuz kontrol edilmemis olanlar da elendi, "
    f"check_us_existence.py ilerledikce bu sayi artacak)"
)

# ---------------------------------------------------------- 4) zayif sayisi
with DEAD_STOCK.open(encoding="utf-8") as f:
    dead_stock_count = sum(1 for _ in csv.DictReader(f))
print(f"Zayif (kanitlanmis olu stok) ASIN sayisi: {dead_stock_count}")

target_n = min(dead_stock_count, len(candidates))
if len(candidates) < dead_stock_count:
    print(
        f"\nUYARI: rakip kaynakli temiz aday havuzu ({len(candidates)}), zayif ASIN sayisindan "
        f"({dead_stock_count}) KUCUK -- sadece {target_n} yer degistirme yapilabilir bu havuzla. "
        f"Kalan {dead_stock_count - target_n} zayif ASIN icin ya baska kaynaktan (GENEL/KATEG/S1-S5) "
        f"takviye gerekir ya da sadece {target_n} tanesi degistirilir."
    )

# ---------------------------------------------------------- 5) en iyi N
ranked = sorted(candidates.items(), key=lambda x: -x[1])
top_n = ranked[:target_n]

print(f"\nSecilen yeni batch: {len(top_n)} ASIN")
if top_n:
    print(f"  En yuksek skor: {top_n[0][1]}")
    print(f"  En dusuk skor (bu batch icinde): {top_n[-1][1]}")
    from collections import Counter
    kod_dagilim = Counter(asin_kod[a] for a, _s in top_n)
    print("  Kaynak dagilimi (satici bazinda):")
    for kod, n in kod_dagilim.most_common():
        print(f"    {kod}: {n}")

out_txt = OUT_DIR / "yer_degistirme_yeni_batch.txt"
out_txt.write_text("\n".join(a for a, _s in top_n) + ("\n" if top_n else ""), encoding="utf-8")
print(f"\nYazildi: {out_txt}")

out_csv = OUT_DIR / "yer_degistirme_ozet.csv"
with out_csv.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["asin", "score", "kaynak_kodu"])
    for asin, score in top_n:
        writer.writerow([asin, score, asin_kod.get(asin, "")])
print(f"Yazildi: {out_csv}")
