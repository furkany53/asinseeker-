# Amazon SP-API Bağlantısı Kurulum Kılavuzu

Bu kılavuz, **AsinSeeker**'ın "Envanter Çek" özelliğini kullanabilmen için kendi Amazon mağazanın API erişim bilgilerini nasıl alacağını anlatır. Bu işlem **tamamen ücretsizdir**, Amazon'un kendi geliştirici sistemi üzerinden yapılır ve yaklaşık 10-15 dakika sürer.

Elde edeceğin 3 bilgi (**Client ID**, **Client Secret**, **Refresh Token**) sadece SENİN Amazon mağazana erişim sağlar — bu bilgileri kimseyle paylaşma. AsinSeeker bu bilgileri kendi bilgisayarında, Windows'un güvenli kimlik deposunda (Credential Manager) saklar; hiçbir yere gönderilmez.

---

## Adım 1 — Developer Central'a Git

1. Hangi ülkede satış yapıyorsan, o ülkenin Seller Central adresine giriş yap (örnek: `sellercentral.amazon.co.jp`, `sellercentral.amazon.com`, `sellercentral.amazon.co.uk`, `sellercentral.amazon.de` vb.).
2. Sağ üstteki **dişli/ayarlar** ikonuna tıkla, açılan menüden **"Manage Your Apps"** veya **"Develop Apps"** seçeneğini bul. (Bazı hesaplarda bu ekran doğrudan `sellercentral-apps.amazon.com` ya da `solutionproviderportal.amazon.com` üzerinde "Developer Central" olarak açılır — ikisi de aynı işi görür.)
3. Eğer daha önce hiç geliştirici profili oluşturmadıysan, kısa bir "Private Developer" kayıt formu çıkabilir — temel bilgilerini girip onaylat (genelde anında onaylanır).

## Adım 2 — Yeni Uygulama Oluştur

1. **"+ Add new app client"** butonuna tıkla.
2. Uygulamaya bir isim ver (örnek: "AsinSeeker Envanter").
3. **API Type** olarak **"SP API"** seç.

## Adım 3 — Doğru Rolleri (İzinleri) Seç ⚠️ EN ÖNEMLİ ADIM

Roller listesinde şu **üçünü** mutlaka işaretle:

- ✅ **Product Listing**
- ✅ **Inventory and Order Tracking**
- ✅ **Brand Analytics**

> **Neden önemli:** Bu üç rol işaretlenmeden envanter/rapor verilerine erişim **çalışmaz** (Amazon "yetkisiz erişim" hatası döndürür) — sadece "Selling Partner Insights" rolü yeterli DEĞİLDİR, bu canlı olarak test edilip doğrulanmıştır. "Restricted" (kısıtlı) etiketli rollere dokunmana gerek yok.

Sayfayı kaydet. Uygulama "Draft" (taslak) durumda kalabilir — bu sorun değil, kendi hesabına bağlanmak için yeterlidir.

## Adım 4 — Client ID ve Client Secret'ı Al

1. Uygulama listesinde, oluşturduğun uygulamanın satırında **"LWA credentials"** sütunundaki **"View"** linkine tıkla.
2. **Client identifier** değerini kopyala — bu senin **Client ID**'n.
3. **"Client secret"** açılır menüsüne tıkla, görünen değeri tıklayıp tamamını seç (Ctrl+A) ve kopyala (Ctrl+C) — bu senin **Client Secret**'ın. (Kutu küçük olduğu için değer kesilmiş görünebilir, mutlaka tamamını seçip kopyala.)

## Adım 5 — Kendi Hesabına Yetki Ver ve Refresh Token'ı Al

1. Aynı uygulamanın satırında **"Manage Authorizations"** (ya da "Authorize") bağlantısına git.
2. Kendi satıcı hesabının/pazar yerinin (hangi ülkede satıyorsan) satırında **"Authorize app"** butonuna tıkla.
3. Amazon seni bir giriş/onay ekranına yönlendirir — kendi Seller Central şifrenle giriş yapıp uygulamayı onayla.
4. Onayladıktan sonra sayfa, o satırda uzun bir **Refresh Token** (`Atzr|...` ile başlar) gösterir. Kutuya tıklayıp tamamını kopyala.

## Adım 6 — AsinSeeker'a Gir

1. AsinSeeker'ı aç, üst menüden **Ayarlar > Amazon SP-API Ayarları...**'na git.
2. **Pazar Yeri** açılır listesinden hangi ülkede sattığını seç.
3. **Client ID**, **Client Secret** ve **Refresh Token** alanlarına Adım 4-5'te aldığın değerleri yapıştır.
4. **"Bağlantıyı Test Et"** butonuna bas — "Bağlantı başarılı" mesajı görürsen tamamdır.
5. **"Kaydet"** ile kapat.

Artık **"Envanter Çek"** sekmesinden "Business Report Çek" (tarih aralığı seçerek) ve "Envanter (Stok) Raporu Çek" butonlarını kullanabilirsin.

---

## Sık Karşılaşılan Sorunlar

**"Amazon API hatası (403): Unauthorized"** — Adım 3'teki üç rolden biri veya birkaçı eksik işaretlenmiş. Uygulamanı "Edit App" ile aç, rolleri kontrol et, kaydet, sonra **Adım 5'i tekrarlayıp yeni bir Refresh Token al** (roller değiştikten sonra ESKİ refresh token yeni izinleri yansıtmayabilir).

**Roller listesinde sadece "Selling Partner Insights" görünüyor, diğerleri yok** — Muhtemelen "Solution Provider Portal" hesabında farklı bir uygulama oluşturmuşsundur ve tam rol listesi görünmüyordur. "+ Add new app client" ile tamamen YENİ bir uygulama oluşturmayı dene; tam rol listesi (Product Listing, Pricing, Amazon Fulfillment, Inventory and Order Tracking, Brand Analytics, vb.) o ekranda çıkmalı.

**"Refresh token bulunamadı / expired"** — Refresh token'lar süresiz geçerlidir, silinmez; ama uygulamanın rollerini SONRADAN değiştirirsen (Adım 3'ü tekrar düzenlersen), yeni yetkileri kullanabilmek için Adım 5'i tekrarlayıp **yeni bir** refresh token alman gerekir.

**Client Secret'ı kopyalarken sonu kesik görünüyor** — Kutuya tıkladıktan sonra Ctrl+A ile tüm metni seç, sonra Ctrl+C ile kopyala; sadece görünen kısmı elle seçmeye çalışma.
