"""Her musterinin KENDI Amazon SP-API kimlik bilgilerini (client_id,
client_secret, refresh_token) ve pazar yerini girip saklamasi icin.

keepa_api_settings.py ile AYNI desen: keyring (Windows Credential Manager)
kullanilir, yerel makineye/musteriye ozel saklanir -- amazon_sp_api.py'nin
kendi load_credentials()'i (env var / amazon_sp_api_credentials.json) BIZIM
kendi ic/kisisel kullanimimiz icindir, bu MODUL ise AsinSeeker'i kullanan
HER MUSTERININ kendi Amazon magazasina baglanmasi icindir (bkz. CLAUDE.md
"Iki ayri Keepa kimlik deposu" kurali -- ayni ayrim burada da uygulaniyor).

Musteri, Amazon Developer Central'da (Seller Central > Apps & Services >
Develop Apps) KENDI "Private developer" uygulamasini olusturup KENDI
hesabina self-authorize ederek bu 3 degeri elde eder -- adim adim rehber
icin bkz. INSTRUCTIONS asagida (GUI'de "Nasil Alinir?" yardim balonunda
gosterilir, ayni metin musteriye giden yazili kilavuzun temelidir).
"""

import json

try:
    import keyring
except ImportError:
    keyring = None

SERVICE_NAME = "AsinSeekerAmazonSPAPI"
ACCOUNT_NAME = "amazon_sp_api_credentials"

# marketplace adi -> (marketplaceId, SP-API bolge ucu). Bolgeler canli
# Amazon dokumantasyonuna gore sabit uclu grup: NA/EU/FE. JP ucu
# (sellingpartnerapi-fe.amazon.com) bu projede canli dogrulandi
# (2026-09-22, marketplaceParticipations + Reports API).
MARKETPLACES = {
    "Japonya (JP)": ("A1VC38T7YXB528", "https://sellingpartnerapi-fe.amazon.com"),
    "ABD (US)": ("ATVPDKIKX0DER", "https://sellingpartnerapi-na.amazon.com"),
    "Kanada (CA)": ("A2EUQ1WTGCTBG2", "https://sellingpartnerapi-na.amazon.com"),
    "Meksika (MX)": ("A1AM78C64UM0Y8", "https://sellingpartnerapi-na.amazon.com"),
    "Brezilya (BR)": ("A2Q3Y263D00KWC", "https://sellingpartnerapi-na.amazon.com"),
    "İngiltere (UK)": ("A1F83G8C2ARO7P", "https://sellingpartnerapi-eu.amazon.com"),
    "Almanya (DE)": ("A1PA6795UKMFR9", "https://sellingpartnerapi-eu.amazon.com"),
    "Fransa (FR)": ("A13V1IB3VIYZZH", "https://sellingpartnerapi-eu.amazon.com"),
    "İtalya (IT)": ("APJ6JRA9NG5V4", "https://sellingpartnerapi-eu.amazon.com"),
    "İspanya (ES)": ("A1RKKUPIHCS9HS", "https://sellingpartnerapi-eu.amazon.com"),
    "Hollanda (NL)": ("A1805IZSGTT6HS", "https://sellingpartnerapi-eu.amazon.com"),
    "Polonya (PL)": ("A1C3SOZRARQ6R3", "https://sellingpartnerapi-eu.amazon.com"),
    "Türkiye (TR)": ("A33AVAJ2PDY3EV", "https://sellingpartnerapi-eu.amazon.com"),
    "BAE (AE)": ("A2VIGQ35RCS4UG", "https://sellingpartnerapi-eu.amazon.com"),
    "Suudi Arabistan (SA)": ("A17E79C6D8DWNP", "https://sellingpartnerapi-eu.amazon.com"),
    "Hindistan (IN)": ("A21TJRUUN4KGV", "https://sellingpartnerapi-eu.amazon.com"),
    "Avustralya (AU)": ("A39IBJ37TRP1C6", "https://sellingpartnerapi-fe.amazon.com"),
    "Singapur (SG)": ("A19VAU5U5O7RUS", "https://sellingpartnerapi-fe.amazon.com"),
}

# Kullaniciya rehberlik icin -- 2026-09-22'de bu projenin kendi Amazon
# hesabinda CANLI olarak tek tek denenip dogrulanan adimlar (bkz.
# amazon_sp_api.py basi notu). Roller ozellikle onemli: sadece "Selling
# Partner Insights" ile Reports API 403 donuyor, "Product Listing" +
# "Inventory and Order Tracking" + "Brand Analytics" gerekiyor.
INSTRUCTIONS = """Amazon SP-API kimlik bilgilerini almak icin (kendi Amazon Seller Central hesabinla, ucretsiz):

1) sellercentral.amazon.<ulken> (or. .co.jp, .com, .co.uk) adresine giris yap.
2) Sag ustteki disli/ayarlar ikonuna tikla -> "Kullanici izinleri" / bulamazsan
   dogrudan developer.amazonservices.com veya solutionprovilerportal.amazon.com
   uzerinden "Develop Apps" / "Developer Central" sayfasina git.
3) "+ Add new app client" ile yeni bir uygulama olustur, API Type: "SP API" sec.
4) "Roles" bolumunde MUTLAKA su ucunu isaretle (canli dogrulandi, sadece
   "Selling Partner Insights" YETMEZ, Reports API erisimi vermiyor):
     - Product Listing
     - Inventory and Order Tracking
     - Brand Analytics
5) Kaydet. Uygulama "Draft" durumda kalsa da SORUN DEGIL -- kendi hesabina
   yetkilendirmek icin yeterli.
6) "LWA credentials" -> "View" ile Client Identifier'i gor, "Client secret"
   acilir menusune tiklayip Client Secret'i de al (tam metnini kopyala).
7) "Manage Authorizations" sayfasina git, KENDI satici hesabinin/pazar
   yerinin (hangi ulkede satiyorsan) satirinda "Authorize app" tuşuna bas.
   Amazon giris/onay ekranina yonlendirir, onaylayinca sayfa bir
   "Refresh Token" (Atzr|... ile baslar) gosterir -- onu kopyala.
8) Bu 3 degeri (Client ID, Client Secret, Refresh Token) ve hangi pazar
   yerinde sattigini asagidaki forma gir, "Kaydet"e tikla.

NOT: Bu bilgiler SADECE bu bilgisayarda, Windows'un kendi guvenli kimlik
deposunda (Credential Manager) saklanir -- hicbir yere gonderilmez."""


def save_credentials(client_id, client_secret, refresh_token, marketplace_name):
    client_id = (client_id or "").strip()
    client_secret = (client_secret or "").strip()
    refresh_token = (refresh_token or "").strip()
    if not (client_id and client_secret and refresh_token and marketplace_name in MARKETPLACES):
        raise ValueError("Client ID, Client Secret, Refresh Token ve pazar yeri hepsi gerekli.")
    if keyring is None:
        raise RuntimeError("Anahtar guvenli sekilde kaydedilemedi (keyring kullanilamiyor).")
    payload = json.dumps({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "marketplace_name": marketplace_name,
    })
    keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, payload)


def load_credentials():
    """Doner: {"client_id", "client_secret", "refresh_token", "marketplace_id",
    "sp_api_base", "marketplace_name"} ya da hic kayit yoksa None."""
    if keyring is None:
        return None
    try:
        raw = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    marketplace_name = data.get("marketplace_name")
    marketplace = MARKETPLACES.get(marketplace_name)
    if not marketplace:
        return None
    marketplace_id, sp_api_base = marketplace
    return {
        "client_id": data.get("client_id"),
        "client_secret": data.get("client_secret"),
        "refresh_token": data.get("refresh_token"),
        "marketplace_name": marketplace_name,
        "marketplace_id": marketplace_id,
        "sp_api_base": sp_api_base,
    }


def has_credentials():
    return bool(load_credentials())
