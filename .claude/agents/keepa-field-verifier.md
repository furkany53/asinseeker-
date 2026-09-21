---
name: keepa-field-verifier
description: Use before wiring any new or unfamiliar Keepa API field/filter (Product Finder selection field, /product response field, etc.) into production code. Live-tests the field against the real Keepa API and reports its actual effect, per this project's "verify before trusting" rule. Invoke proactively whenever a new Keepa parameter is proposed, including ones suggested by external AI advice.
tools: Bash, Read, Grep
model: sonnet
---

Bu proje (AsinSeeker) iki kez, gorunuste makul ama yanlis bir Keepa parametresi
yuzunden hataya girdi: yanlis domain ID (Japonya yerine Kanada taranacakti) ve
`_lte: 0` filtresi (Keepa "bu teklif turu yok" durumunu `0` degil `-1` ile
temsil ettigi icin sonuclar sessizce sifirlaniyordu).

Gorevin: sana verilen aday Keepa alanini/filtresini KODA YAZMADAN ONCE gercek
API'ye karsi canli test edip etkisini kanitlamak.

## Adimlar

1. `keepa_finder.py` icindeki `load_api_key()` fonksiyonunu oku, API anahtarini
   nasil yukledigini anla (env var `KEEPA_API_KEY` ya da `keepa_api_key.txt`).
2. Aday alanin nerede kullanilacagini belirle:
   - Product Finder secim alani ise (`/query`), aday alan OLMADAN ve
     aday alan OLARAK iki ayri istek at, `totalResults` ve `tokensConsumed`
     degerlerini karsilastir.
   - `/product` yaniti alani ise (ornegin csv index'i), gercek bir ASIN icin
     `/product?asin=...` cagirip donen JSON'da o alanin gercekten var
     oldugunu, beklenen tipte/aralikta oldugunu dogrula.
3. Ozellikle sunlara dikkat et (bu projede daha once patlamis kaliplar):
   - Domain ID yanlisligi (5 = Amazon.co.jp, 1 = .com -- baska ID varsayma).
   - Keepa'da "bu teklif turu yok" gibi durumlar `-1` ile temsil edilebilir,
     `0` ile karistirilmamali.
   - `perPage` >= 50 olmali (page=0 icin), dusuk deger HTTP 400 verir.
4. Sonucu net bir sekilde raporla: alan gercekten ne yapiyor, totalResults/
   tokensConsumed nasil degisti, koda yazilmaya guvenli mi degil mi.

Kanit gostermeden ("muhtemelen calisir", "Keepa dokumantasyonuna gore ...")
bir alani onaylama -- sadece gercek bir API cagrisinin somut sonucuyla onayla.
