---
name: pipeline-durum
description: Report the current status of the full-pool background scanning pipeline (full_pool_check.py) -- whether it's running, how many ASINs of the shared pool are processed, and the upload/delete/hata breakdown. Use when the user asks "son durum", "ne durumdayız", or asks about scan/tarama progress.
---

# Pipeline Durum Raporu

`full_pool_check.py` benden bagimsiz calisan, resumable bir arka plan
surecidir. Bu skill onun guncel durumunu ozetler.

## Adimlar

1. Surecin gercekten calisip calismadigini kontrol et (PowerShell):
   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'full_pool_check' } | Select-Object ProcessId, CommandLine
   ```

2. Paylasilan havuzun toplam boyutunu al:
   ```bash
   wc -l keepa_sync/ortak_asin_havuzu.txt
   ```

3. Su ana kadar islenen ASIN sayisini ve status dagilimini cikar
   (`keepa_full_kontrol/sonuclar.csv`, sutunlar: asin,status,reason,gap_count,score):
   ```python
   import csv
   from collections import Counter
   counts = Counter()
   with open("keepa_full_kontrol/sonuclar.csv", encoding="utf-8") as f:
       next(csv.reader(f))  # header
       for row in csv.reader(f):
           if len(row) >= 2:
               counts[row[1]] += 1
   print(counts)
   ```

4. Varsa yer-degistirme (slot swap) hazirlik dosyalarinin durumunu da kontrol et:
   ```bash
   for f in keepa_full_kontrol/yer_degistirme_ozet.csv keepa_full_kontrol/dead_stock_confirmed.csv; do
     [ -f "$f" ] && echo "$f: $(wc -l < "$f") satir"
   done
   ```

5. Ozeti Turkce, kisa ve net rapor et: % islenme orani, kalan ASIN sayisi,
   upload/delete/hata kirilimi, surecin canli olup olmadigi.
