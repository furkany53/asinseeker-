# AsinSeeker - Yapılacaklar

- [ ] **Diğer pazarlar için hazır strateji/kategori planı (S1-S5 benzeri):**
  Şu an S1-S5 hazır stratejileri ve kategori planı (`keepa_kategori_plani.json`)
  sadece Japonya (domain=5) için var -- JPY fiyat aralıkları ve JP kategori
  ağacının Sales Rank dağılımıyla canlı Keepa verisi üzerinden kalibre
  edilmişler. Diğer pazarlarda (ABD, Almanya, İngiltere vb.) müşteri şu an
  sadece manuel/kişisel filtrelerle arama yapabiliyor.
  İstenirse: her hedef ülke için aynı S1-S5 mantığı, o ülkenin kendi Keepa
  verisiyle (fiyat aralığı + kategori/Sales Rank bandı) yeniden canlı
  doğrulanıp eklenebilir. Proje kuralı gereği (bkz. CLAUDE.md "verify before
  trusting") her yeni ülke için filtreler gerçek API ile `totalResults`
  üzerinden test edilmeden production'a alınmamalı.
  (Kaynak: 2026-09-16 kullanıcı isteği -- "bu işlemi de sıraya al, yapılacaklar
  listesine ekle")

  **2026-09-17 canlı test bulgusu (Keepa Finder /query kapsamı, cok genis
  filtrelerle -- fiyat 1-500000, offer 0-1000, Sales Rank 1-1000000):**
  - ABD (domain=1): totalResults=1.474.574 -- iyi kapsam
  - Almanya (domain=3): totalResults=1.124.647 -- iyi kapsam
  - Avustralya (domain=13): totalResults=0 -- Keepa'da Product Finder
    tarafında pratikte HİÇ veri yok, kullanıcının 0 ASIN bulması bir
    AsinSeeker hatası değildi, Keepa'nın kendi kapsam sınırıydı.
  - İngiltere (2), Hollanda (14), Hindistan (10), Meksika (11), Brezilya (12):
    test edilemedi -- full_pool_check.py'nin arka planda ayni hesabin
    tokenlerini tuketmesi yuzunden surekli 429 alindi, SONUC YOK (0 degil,
    test edilmedi). Ileride full_pool_check.py calismazken/dusuk
    yogunluktayken tekrar denenmeli.
  Öneri: müşteriye şimdilik büyük/olgun pazarları (ABD, Almanya, muhtemelen
  İngiltere/Fransa/Japonya) önermek daha güvenli; küçük pazarlarda (Avustralya
  gibi) Keepa Finder'ın veri kapsamı yetersiz olabilir.
