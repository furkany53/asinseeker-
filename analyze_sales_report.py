"""EasyCentral'in kendi satis raporunu (SalesReport_*.xlsx) ASIN bazinda
ozetleyip, "kanitlanmis satis/kar gecmisi" veritabani olusturur.

Cikti: keepa_full_kontrol/satis_gecmisi_asin.csv
  asin, siparis_sayisi, toplam_kar_usd, ortalama_kar_usd, ortalama_kar_orani,
  toplam_satis_usd, ilk_siparis, son_siparis

Bu dosya, iki amac icin kullanilacak:
1. Mevcut envanterdeki HANGI ASIN'lerin HICBIR ZAMAN satmadigini bulup
   (budama/cikarma adaylari).
2. Yeni ASIN kesfinde, kanitlanmis-kar getiren urunlerin ORTAK ozelliklerini
   (kategori, fiyat araligi, agirlik) ogrenmek icin egitim verisi.

2026-09-20 DUZELTME (musteriden geldi): Musteri her ay TUM isletme
giderlerini (Keepa/EasyInventory abonelikleri, Amazon magaza kirasi, Prime
uyeligi vb.) o ayin rastgele/uygun bir siparisine "Profit" alaninda ek
maliyet olarak yansitiyor -- yani bazi ASIN'lerin gorunen "toplam kari"
gercekte O URUNUN degil, O AYIN GENEL GIDERININ negatif etkisini tasiyor.
CANLI DOGRULANDI: "Order Note"/"Tag" alanlarinda "masraf/gider/kira/harcama/
Easyinvertory" gibi anahtar kelimeler geciyor -- 28 siparis satiri, ~26 aya
yayili, toplam -$4.193,81 etki (kullanicidan onaylandi). Bu satirlar artik
per-ASIN karliligini KIRLETMESIN diye ayri tutulup ASIN toplamlarina hic
KATILMIYOR -- ayrica denetim icin kendi dosyalarina yaziliyor.
"""
import openpyxl
import csv
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

SOURCE = Path(r"C:\Users\onury\Downloads\SalesReport_[2018-01-01_2026-09-18].xlsx")
OUTPUT = Path(__file__).resolve().parent / "keepa_full_kontrol" / "satis_gecmisi_asin.csv"
EXPENSE_ROWS_OUTPUT = Path(__file__).resolve().parent / "keepa_full_kontrol" / "aylik_gider_satirlari.csv"

# Turkce isletme-gideri anahtar kelimeleri -- Order Note/Tag alanlarinda
# gecerse o satir bir URUN satisi degil, aylik GENEL GIDER kaydi sayilir.
EXPENSE_KEYWORDS = re.compile(
    r"masraf|gider|kira|harcama|easyinvertory|easyinventory", re.IGNORECASE
)

wb = openpyxl.load_workbook(SOURCE, read_only=True, data_only=True)
ws = wb["Worksheet"]

rows = ws.iter_rows(min_row=1, values_only=True)
header = next(rows)
col = {name: i for i, name in enumerate(header)}

asin_col = col["ASIN 1"]
profit_col = col["Profit"]
profit_rate_col = col["Profit Rate"]
sales_price_col = col["Sales Price"]
order_date_col = col["Order Date"]
status_col = col["Seller Order Status"]
note_col = col["Order Note"]
tag_col = col["Tag"]

stats = defaultdict(lambda: {
    "orders": 0, "total_profit": 0.0, "total_sales": 0.0,
    "profit_rates": [], "first_date": None, "first_date_key": None,
    "last_date": None, "last_date_key": None,
})

total_rows = 0
skipped_no_asin = 0
skipped_cancelled = 0
skipped_expense = 0
expense_rows = []


def parse_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def date_sort_key(date_str):
    """'DD.MM.YYYY' -> (YYYY, MM, DD) karsilastirilabilir tuple. Duz string
    karsilastirmasi bu formatta KRONOLOJIK DEGIL (orn. '22.03.2025' <
    '01.10.2025' string olarak, ama tarih olarak degil) -- eskiden bu
    hataliydi, ilk_siparis/son_siparis bazen ters cikiyordu."""
    try:
        d, m, y = str(date_str).split(".")
        return (int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


for row in rows:
    total_rows += 1
    asin = row[asin_col]
    if not asin:
        skipped_no_asin += 1
        continue
    status = row[status_col]
    if status and "cancel" in str(status).lower():
        skipped_cancelled += 1
        continue

    note = str(row[note_col] or "")
    tag = str(row[tag_col] or "")
    if EXPENSE_KEYWORDS.search(note) or EXPENSE_KEYWORDS.search(tag):
        skipped_expense += 1
        expense_rows.append({
            "asin": str(asin).strip().upper(),
            "order_date": row[order_date_col],
            "profit": parse_float(row[profit_col]),
            "note": note[:200],
            "tag": tag[:100],
        })
        continue

    asin = str(asin).strip().upper()
    s = stats[asin]
    s["orders"] += 1
    s["total_profit"] += parse_float(row[profit_col])
    s["total_sales"] += parse_float(row[sales_price_col])
    rate = parse_float(row[profit_rate_col])
    if rate:
        s["profit_rates"].append(rate)
    date = row[order_date_col]
    if date:
        key = date_sort_key(date)
        if key is not None:
            if s["first_date_key"] is None or key < s["first_date_key"]:
                s["first_date_key"] = key
                s["first_date"] = str(date)
            if s["last_date_key"] is None or key > s["last_date_key"]:
                s["last_date_key"] = key
                s["last_date"] = str(date)

print(
    f"Toplam satir: {total_rows}, ASIN'siz atlanan: {skipped_no_asin}, "
    f"iptal atlanan: {skipped_cancelled}, aylik gider (urun disi) atlanan: {skipped_expense}"
)
print(f"Benzersiz ASIN sayisi (en az 1 gercek siparis): {len(stats)}")

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with OUTPUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow([
        "asin", "siparis_sayisi", "toplam_kar_usd", "ortalama_kar_usd",
        "ortalama_kar_orani", "toplam_satis_usd", "ilk_siparis", "son_siparis",
    ])
    for asin, s in sorted(stats.items(), key=lambda x: -x[1]["total_profit"]):
        avg_profit = s["total_profit"] / s["orders"] if s["orders"] else 0
        avg_rate = sum(s["profit_rates"]) / len(s["profit_rates"]) if s["profit_rates"] else 0
        writer.writerow([
            asin, s["orders"], round(s["total_profit"], 2), round(avg_profit, 2),
            round(avg_rate, 2), round(s["total_sales"], 2), s["first_date"], s["last_date"],
        ])

print(f"Yazildi: {OUTPUT}")

with EXPENSE_ROWS_OUTPUT.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["asin", "order_date", "profit", "note", "tag"])
    for r in expense_rows:
        writer.writerow([r["asin"], r["order_date"], r["profit"], r["note"], r["tag"]])
total_expense_effect = sum(r["profit"] for r in expense_rows)
print(f"Yazildi: {EXPENSE_ROWS_OUTPUT} ({len(expense_rows)} satir, toplam etki ${total_expense_effect:.2f} -- ASIN toplamlarina KATILMADI)")

# En karli 15 ASIN'i goster
top = sorted(stats.items(), key=lambda x: -x[1]["total_profit"])[:15]
print("\n=== EN KARLI 15 ASIN ===")
for asin, s in top:
    avg_profit = s["total_profit"] / s["orders"]
    print(f"{asin}: {s['orders']} siparis, toplam kar ${s['total_profit']:.2f}, ort. kar ${avg_profit:.2f}")
