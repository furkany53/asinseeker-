# AsinSeeker - Oturum Devir Notu (2026-09-17)

Bu dosya, yeni bir Claude Code oturumuna (hafızası olmayan) bu projede bugüne
kadar neler yapıldığını, hangi süreçlerin çalışır durumda olduğunu ve sırada
ne olduğunu anlatmak için yazıldı. Yeni oturumda: "SESSION_DURUM_2026-09-17.md
dosyasını oku, kaldığımız yerden devam edelim" de yeter.

## Bugün yapılanlar (özet, kronolojik)

1. **GUI iyileştirmeleri** (hepsi `dist/AsinSeeker.exe` + kurulu kopyaya + zip'e işlendi, test edildi):
   - Kaynak Pazar seçici tüm sekmelerde global, ASIN Bul + Temizle'yi etkiliyor
   - Fiyat min/max etiketleri seçili ülkenin para birimini gösteriyor + varsayılan ~30-500 USD karşılığı dolduruyor
   - "Son X gün güncellenmiş" alanına 0/negatif girilince net hata (Keepa hep 0 sonuç dönerdi, sebepsiz sanılıyordu)
   - Manuel filtre modunda ülkeler artık AYRI klasör kullanıyor (eskiden hepsi aynı klasörü paylaşıp "zaten tarandı" diye birbirini yanlış atlıyordu)
   - "Bu Klasördeki Taramayı Sıfırla" butonu ana ASIN Bul sekmesinde (Sales Rank dilim önbelleğini temizler)
   - Smart App Control bu makinede KAPALI (kullanıcı kapattı, tek yönlü -- geri açmak icin Windows temiz kurulumu gerekir)

2. **full_pool_check.py**: JP havuzunun ilk 103.487 ASIN'lik hali TAMAMEN tarandı
   (delete/upload/hata kesinleşti). Sonra yeni ASIN'ler bulundukça (bkz. madde 4)
   birkaç kez yeniden başlatıldı. **ÖNEMLİ:** `sonuclar.csv` APPEND-ONLY -- bir
   ASIN önce "hata" alıp sonra retry'de başarılı olursa eski hata satırı
   silinmiyor, bu yüzden ham "hata" sayısı asla düşmez (normal, hata değil).
   Asıl ilerlemeyi delete+upload toplamından takip et.

3. **EasyCentral'a gönderim** (`easycentral_target.py` + `keepa_gui.py`'nin
   "Easy'e Gönder" sekmesi -- artık SKU kodu + "mağazaya otomatik yükle"
   entegre):
   - batch_1 (25.000, önceden gönderilmişti), batch_2 (5.000, JP-CSBYR),
     batch_3 (25.000, EasyCentral otomatik 5×5.000'lik parçaya böldü:
     JP-00003/Y/YP/YPR/YPRE)
   - **KRİTİK BULGU:** "upload" listesindeki ASIN'lerin ÇOĞU Amazon.com'da
     var mı diye HİÇ doğrulanmamış (check_target_market kodu eklenmeden
     ÖNCE zaten "upload" olarak cache'lenmiş, full_pool_check bir daha
     kontrol etmiyor) -- bu yüzden partilerin çoğunda "uygun" oranı çok
     düşük çıktı (%2-10). `check_us_existence.py` bunu geriye dönük
     düzeltmek için var ama kullanıcı durdurdu (sadece 1.927/40.864 yapıldı,
     "Easy zaten taramadan geçti" dedi -- tekrar başlatmak istenirse
     `python check_us_existence.py` calıştır, kaldığı yerden devam eder).
   - Şu ana kadar partilerden toplam **~4.214 "uygun"** ürün çıktı, mağazaya
     gönderiliyor/gönderildi (kullanıcı EasyCentral üzerinden elle
     yönetiyor).
   - **Son toplu kontrol:** `easycentral_batch_1/2/3.txt` dosyaları
     `full_pool_check.py` ve `check_us_existence.py`'nin EXCLUDE_ASIN_FILES
     listesinde -- yeni bir batch gönderilince aynı isimlendirmeyle
     (batch_4.txt vb.) oraya da eklenmeli.

4. **YENİ ASIN kaynağı keşfedildi -- Keepa Seller API (çok verimli):**
   - `/seller?domain=5&seller=ID&storefront=1` = 10 token karşılığında o
     satıcının TÜM aktif ASIN listesini veriyor (binlerce ürün olabilir).
   - `/product?asin=X&offers=20` ile bir ürünün TÜM canlı tekliflerinin
     sellerId'lerini alıp, sonra toplu `/seller?seller=ID1,ID2,...` ile
     her satıcının `address` alanının SON elemanına (ülke kodu, "TR" gibi)
     bakarak Türk satıcıları tespit ediyoruz.
   - Bugün 1 ASIN'in 29 satıcısından **19'u Türk** çıktı (isim/telefon/adres
     doğrulandı). Hepsinin storefront'u çekildi.
   - `harvest_seller_asins.py`: tekrar kullanılabilir script, `SELLERS`
     listesine (isim, seller_id, domain) eklenip çalıştırılır, zaten
     işlenmiş satıcıları atlar (`keepa_sync/turk_saticilar/islenen_saticilar.csv`).
   - Ayrı JP Sales Rank taraması da (0-500.000) paralel çalıştırıldı,
     47.565 ASIN buldu.
   - **Ortak havuz: 103.487 → 185.254 ASIN** (+81.767, veri kaybı
     doğrulandı YOK).

5. **Kaynak/yöntem izleme sistemi** (`build_asin_kaynak_yontemi.py`):
   her ASIN'i hangi yöntemden/satıcıdan geldiğine göre 5 haneli koda
   eşliyor -- `keepa_full_kontrol/asin_kaynak_yontemi.csv` (187.534 ASIN).
   Kodlar: S1HOT/S2BAL/S3LOW/S4FBM/S5PRV (stratejiler), KATEG (kategori),
   GENEL (genel tarama), ve satıcı kodları: **HAYAI, SABAN, DENIZ, AHMET,
   ISMAI, ALIMU, OMERS, PINAR, MUAMM, IBRAH, NURAY, YUSUF, HATIC**
   (rakip Türk satıcılar, isimlerinin ilk 5 harfi). EasyCentral'a parti
   gönderirken bu kod Stok Kodu'na yazılır (örn. "DENIZ") -- ileride hangi
   kaynağın daha çok sattığı SKU'dan görülebilir.

## Şu an çalışıyor olabilecek süreçler (yeni oturumda İLK KONTROL ET)

```bash
tasklist //FI "IMAGENAME eq python.exe"
cat full_pool_check.lock   # son bilinen PID: 28068
tail -30 full_pool_check_run7.log
wc -l keepa_full_kontrol/sonuclar.csv   # son bilinen: 99.636 satır
awk -F, 'NR>1{c[$2]++} END{for(k in c) print k,c[k]}' keepa_full_kontrol/sonuclar.csv
# son bilinen: delete=43036 upload=40950 hata=15642 (yukarida daima sabit kalir, normal)
```

`full_pool_check.py` yeni bulunan ~81.767 ASIN'i (185.254'lük havuzun
eskiden işlenmemiş kısmı) işliyor olmalı -- bitince `uygun_siralanmis.txt`
büyür, yeni bir EasyCentral partisi hazırlanabilir.

## Sırada ne var

1. `full_pool_check.py`'nin yeni ASIN'leri işlemesini bekle/izle, bitince
   yeni "upload" sayısını bildir.
2. Yeni EasyCentral partisi hazırlarken, **karışık göndermek yerine
   satıcı/yöntem koduna göre AYRI partiler** göndermeyi düşün (örn. sadece
   DENIZ'in 15.352 ASIN'ini "DENIZ" SKU'suyla) -- temiz istatistik için.
3. İstenirse `check_us_existence.py`'yi devam ettirip eski "upload"
   kayıtlarını Amazon.com'da doğrula (WORKERS=3, tam hızda çalışacak
   şekilde ayarlı).
4. Daha fazla Türk satıcı bulmak istenirse: yeni bir ASIN'in offers
   listesini çek (`/product?offers=20`), sellerId'leri topla, `/seller`
   ile adres kontrolü yap, TR olanları `harvest_seller_asins.py`'nin
   `SELLERS` listesine ekle. Ayrıca Seller API yanıtındaki `competitors`
   alanı da (aynı ürünlerde rekabet eden diğer satıcı ID'leri) yeni Türk
   satıcı adayları için iyi bir kaynak.
5. Diğer ülkeler için S1-S5 tarzı hazır stratejiler -- ayrı TODO.md'de,
   düşük öncelik.
6. Kod imzalama sertifikası (Smart App Control/SmartScreen için) -- ayrı
   konu, ertelendi.

## Önemli dosyalar/scriptler (bugün eklenen/değişen)

- `harvest_seller_asins.py` -- Türk satıcı ASIN toplama (tekrar çalıştırılabilir)
- `build_asin_kaynak_yontemi.py` -- ASIN -> kaynak/yöntem kodu haritası
- `run_fresh_jp_sweep.py` -- tek seferlik JP taraması (tekrar kullanılabilir, run_id klasörü değişir)
- `check_us_existence.py` -- Amazon.com varlık doğrulama (duraklatıldı, devam ettirilebilir)
- `easycentral_target.py` -- artık `sku_code` parametresi destekliyor (paste_asins + submit_and_start_scan)
- `keepa_gui.py` -- "Easy'e Gönder" sekmesinde Stok Kodu alanı eklendi

## Chrome / EasyCentral durumu

Chrome debug modda (`C:\chrome_debug_temp`, port 9222) açık olabilir,
EasyCentral'da MyHouse mağazası seçili, giriş yapılı. Yeni oturumda
kontrol etmeden ASIN yapıştırma/gönderme denemeden önce sekmenin hala
açık olup olmadığını doğrula.
