"""2026-09-18'de gelen iki yeni rapordan (Amazon Business Report + EasyCentral
inventory-list) gercek rekabet/satis sonuclarini cikarip, "kaliteli ASIN"
puanlama algoritmasini (keepa_check.compute_priority_score) hangi varsayimlarin
GERCEKTE dogrulandigini/dogrulanmadigini gormek icin kullanir.

Girdi (Downloads klasorunden -- musteri indirdigi haliyle, tasinmadi):
  - data (1).csv        Amazon Business Report + EasyCentral overlay
                         (PARENT/CHILD ASIN, SESSION, PAGE VIEWS, BUYBOX %,
                         UNITS ORDERED, ORDER PRODUCT SALES, ENVANTERDE VAR/YOK,
                         BUYBOX, LOWEST, TEK SATICI)
  - inventory-list.csv   EasyCentral envanter raporu (17.798 satir) -- ASIN
                         basina GERCEK rekabet durumu: REKABET-DURUMUM,
                         BUYBOX-DURUMUM, LOWEST-DURUMUM, RAKIP-SATICI-SAYISI,
                         RAKIP-*-DURUMU (Prime/SBA/FBA/FBM/Cinli), SIPARIS-SAYISI,
                         kategori/fiyat/rating alanlari.

NOT -- ASIN uzayi uyumsuzlugu: Bu iki rapordaki ASIN, Keepa tarafinda
taradigimiz JP kaynak ASIN'i DEGIL -- EasyCentral'in capraz-listeledigi
GERCEK Amazon listing ASIN'i (SKU'dan JP kaynak ASIN'e geri donus yok, SKU
sadece "JP-<5harfli yontem kodu>" tasiyor -- bkz. easycentral_target.py).
Yani bu script'te DOGRUDAN sonuclar.csv/compute_priority_score ile ASIN
bazinda birebir eslestirme YAPILAMIYOR. Bunun yerine iki raporu KENDI
ARALARINDA (ASIN uzerinden, ikisi de ayni -- listing -- ASIN uzayinda)
birlestirip, GERCEK pazar sonuclarindan (rekabet edebiliyor muyuz, siparis
var mi, buybox kimde) puanlama algoritmasinin dayandigi varsayimlari
(rakip sayisi arttikca kaybetme sansi, Cinli satici varliginin etkisi, vb.)
test ediyoruz -- bu, compute_priority_score'u ILERIDE kalibre etmek icin
kullanilacak ampirik veri.
"""
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Bu tarihten ONCE yuklenip hala 0 siparisi olan ASIN -- "henuz vakit olmadi"
# degil, KANITLANMIS olu stok sayilir (1+ ay gecmis, canli veriyle dogrulandi:
# eski kohortlarda (2024-2025) donusum orani zamanla iyilesmiyor, hep %0-4
# bandinda kaliyor -- yani "isinma" degil, gercek/kalici sorun).
DEAD_STOCK_CUTOFF = "2026-08-01"

BUSINESS_REPORT = Path(r"C:\Users\onury\Downloads\data (1).csv")
INVENTORY = Path(r"C:\Users\onury\Downloads\inventory-list.csv")
OUT_DIR = Path(__file__).resolve().parent / "keepa_full_kontrol"


def to_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def to_int01(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- inventory-list
inv_rows = {}
with INVENTORY.open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    inv_fields = reader.fieldnames
    for row in reader:
        asin = (row.get("ASIN") or "").strip()
        if asin:
            inv_rows[asin] = row

print(f"inventory-list.csv: {len(inv_rows)} benzersiz ASIN, {len(inv_fields)} kolon")

REKABET_KEY = "REKABET-DURUMUM (1:REKABET EDEBILIYOR - 0:REKABET EDEMIYOR)"
BUYBOX_KEY = "BUYBOX-DURUMUM (1:BUYBOX - 0:BUYBOX DEGIL)"
LOWEST_KEY = "LOWEST-DURUMUM (1:LOWEST - 0:LOWEST DEGIL)"
FBA_KEY = "US-FBA/SBA DURUMU"

rekabet_dagilim = defaultdict(int)
buybox_dagilim = defaultdict(int)
siparis_var = 0
siparis_yok = 0
rakip_sayisi_by_rekabet = defaultdict(list)
cinli_rakip_by_rekabet = defaultdict(int)
cinli_rakip_toplam = defaultdict(int)

for asin, row in inv_rows.items():
    rek = to_int01(row.get(REKABET_KEY))
    bb = to_int01(row.get(BUYBOX_KEY))
    rekabet_dagilim[rek] += 1
    buybox_dagilim[bb] += 1

    siparis = to_int01(row.get("SIPARIS-SAYISI")) or 0
    if siparis > 0:
        siparis_var += 1
    else:
        siparis_yok += 1

    rakip_sayisi = to_int01(row.get("RAKIP-SATICI-SAYISI"))
    if rek is not None and rakip_sayisi is not None:
        rakip_sayisi_by_rekabet[rek].append(rakip_sayisi)

    cinli = to_int01(row.get("RAKIP-CINLI-SATICI-DURUMU"))
    if rek is not None:
        cinli_rakip_toplam[rek] += 1
        if cinli == 1:
            cinli_rakip_by_rekabet[rek] += 1

print("\n=== REKABET-DURUMUM dagilimi (1=edebiliyor, 0=edemiyor, None=veri yok) ===")
for k, v in sorted(rekabet_dagilim.items(), key=lambda x: (x[0] is None, x[0])):
    pct = 100 * v / len(inv_rows)
    print(f"  {k}: {v} ({pct:.1f}%)")

print("\n=== BUYBOX-DURUMUM dagilimi ===")
for k, v in sorted(buybox_dagilim.items(), key=lambda x: (x[0] is None, x[0])):
    pct = 100 * v / len(inv_rows)
    print(f"  {k}: {v} ({pct:.1f}%)")

print(f"\n=== Siparis gecmisi (SIPARIS-SAYISI alanindan, kumulatif) ===")
print(f"  En az 1 siparis: {siparis_var} ({100*siparis_var/len(inv_rows):.1f}%)")
print(f"  Hic siparis yok: {siparis_yok} ({100*siparis_yok/len(inv_rows):.1f}%)")

print("\n=== Rakip satici sayisi ortalamasi, REKABET-DURUMUM'a gore ===")
for k, values in sorted(rakip_sayisi_by_rekabet.items(), key=lambda x: (x[0] is None, x[0])):
    if values:
        avg = sum(values) / len(values)
        print(f"  REKABET={k}: ortalama {avg:.2f} rakip ({len(values)} ASIN)")

print("\n=== Cinli rakip varligi, REKABET-DURUMUM'a gore ===")
for k in sorted(cinli_rakip_toplam, key=lambda x: (x is None, x)):
    total = cinli_rakip_toplam[k]
    cinli = cinli_rakip_by_rekabet[k]
    print(f"  REKABET={k}: {cinli}/{total} ASIN'de Cinli rakip var ({100*cinli/total:.1f}%)")

# ------------------------------------------------------------- business report
biz_rows = {}
with BUSINESS_REPORT.open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    biz_fields = reader.fieldnames
    for row in reader:
        asin = (row.get("CHILD ASIN") or "").strip()
        if asin:
            biz_rows[asin] = row

print(f"\ndata (1).csv (Business Report): {len(biz_rows)} benzersiz CHILD ASIN, {len(biz_fields)} kolon")

# --------------------------------------------------- iki raporun kesisimi
ortak = set(inv_rows) & set(biz_rows)
print(f"\nİki rapor arasinda ORTAK ASIN sayisi: {len(ortak)}")

trafik_var_siparis_yok = []
for asin in ortak:
    biz = biz_rows[asin]
    sessions = to_float(biz.get("SESSION"))
    units = to_float(biz.get("UNITS ORDERED"))
    if sessions >= 20 and units == 0:
        trafik_var_siparis_yok.append((asin, sessions, to_float(biz.get("BUYBOX %"))))

trafik_var_siparis_yok.sort(key=lambda x: -x[1])
print(f"\n=== Trafik var (>=20 session) ama SIFIR siparis: {len(trafik_var_siparis_yok)} ASIN ===")
print("(bunlar buybox/fiyat sorunu olabilecek, kaybedilen trafik adaylari)")
for asin, sessions, buybox_pct in trafik_var_siparis_yok[:15]:
    inv = inv_rows.get(asin, {})
    rek = inv.get(REKABET_KEY, "?")
    bb = inv.get(BUYBOX_KEY, "?")
    rakip = inv.get("RAKIP-SATICI-SAYISI", "?")
    print(f"  {asin}: {sessions:.0f} session, buybox%={buybox_pct:.1f}, REKABET={rek}, BUYBOX={bb}, rakip_sayisi={rakip}")

# --------------------------------------------------- ciktilari yaz
OUT_DIR.mkdir(exist_ok=True)
out_path = OUT_DIR / "trafik_var_siparis_yok.csv"
with out_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["asin", "sessions", "buybox_pct", "rekabet_durumu", "buybox_durumu", "rakip_sayisi"])
    for asin, sessions, buybox_pct in trafik_var_siparis_yok:
        inv = inv_rows.get(asin, {})
        writer.writerow([
            asin, sessions, buybox_pct,
            inv.get(REKABET_KEY, ""), inv.get(BUYBOX_KEY, ""), inv.get("RAKIP-SATICI-SAYISI", ""),
        ])
print(f"\nYazildi: {out_path} ({len(trafik_var_siparis_yok)} satir)")

# Rekabet edemeyen (REKABET-DURUMUM=0) TUM ASIN'leri de ayri dosyaya yaz -- budama/inceleme adaylari
rekabet_edemeyen_path = OUT_DIR / "rekabet_edemeyen_asinler.csv"
with rekabet_edemeyen_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["asin", "urun_adi", "rakip_sayisi", "cinli_rakip_var_mi", "siparis_sayisi", "satis_fiyati"])
    count = 0
    for asin, row in inv_rows.items():
        if to_int01(row.get(REKABET_KEY)) == 0:
            writer.writerow([
                asin, row.get("URUN ADI", ""), row.get("RAKIP-SATICI-SAYISI", ""),
                row.get("RAKIP-CINLI-SATICI-DURUMU", ""), row.get("SIPARIS-SAYISI", ""),
                row.get("SATIS-FIYATI", ""),
            ])
            count += 1
print(f"Yazildi: {rekabet_edemeyen_path} ({count} satir)")

# --------------------------------------------------- kanitlanmis olu stok
# YUKLEME-TARIHI'ndan DEAD_STOCK_CUTOFF'tan ONCE yuklenmis VE hala 0 siparisi
# olan ASIN'ler -- gercek budama listesi (analyze_sales_report.py'nin
# dogrudan gerceklestiremedigi hedef, cunku o SADECE gecmis siparis
# raporundan yola cikiyordu; burada YUKLEME-TARIHI sayesinde "yeterince
# vakit gecti mi" sorusu da cevaplanabiliyor).
dead_stock = []
for asin, row in inv_rows.items():
    tarih = (row.get("YUKLEME-TARIHI") or "").strip()[:10]
    if not tarih or tarih >= DEAD_STOCK_CUTOFF:
        continue
    if (to_int01(row.get("SIPARIS-SAYISI")) or 0) > 0:
        continue
    dead_stock.append((tarih, asin, row))

dead_stock.sort()
rekabet_split = defaultdict(int)
for _tarih, _asin, row in dead_stock:
    rekabet_split[to_int01(row.get(REKABET_KEY))] += 1

print(f"\n=== KANITLANMIS OLU STOK ({DEAD_STOCK_CUTOFF} oncesi yuklenmis, hala 0 siparis): {len(dead_stock)} ASIN ===")
for rek, count in sorted(rekabet_split.items(), key=lambda x: (x[0] is None, x[0])):
    print(f"  REKABET-DURUMUM={rek}: {count} ASIN ({100*count/len(dead_stock):.1f}%)")

dead_stock_path = OUT_DIR / "dead_stock_confirmed.csv"
with dead_stock_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        "asin", "urun_adi", "yukleme_tarihi", "rekabet_durumu", "buybox_durumu",
        "rakip_sayisi", "satis_fiyati", "us_ana_katagori", "us_alt_kategori",
    ])
    for tarih, asin, row in dead_stock:
        writer.writerow([
            asin, row.get("URUN ADI", ""), tarih,
            row.get(REKABET_KEY, ""), row.get(BUYBOX_KEY, ""),
            row.get("RAKIP-SATICI-SAYISI", ""), row.get("SATIS-FIYATI", ""),
            row.get("US-KATEGORI", ""), row.get("US-ALT-KATEGORI", ""),
        ])
print(f"Yazildi: {dead_stock_path} ({len(dead_stock)} satir)")
