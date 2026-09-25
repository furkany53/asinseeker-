"""EasyCentral ana sayfasindaki "Envanter Durumu" kartini (BuyBox / Lowest /
toplam envanter) ve ustteki paket kullanim yuzdesini her yukleme/silme
sonrasi kaydeder.

Kullanim:  python snapshot_envanter.py "UYGUN yuklemesi sonrasi"

Cikti:
  keepa_full_kontrol/envanter_snapshots/<zaman>_<etiket>.png   (kartin goruntusu)
  keepa_full_kontrol/envanter_durumu_log.csv                   (her calismada 1 satir)
"""
import csv
import base64
import io
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image

from keepa_check import open_cdp_session
from easycentral_keepa_bot import DEBUG_ADDRESS, start_chrome_for_attachment
from easycentral_target import _find_existing_tab

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

OUT_DIR = BASE_DIR / "keepa_full_kontrol"
SHOT_DIR = OUT_DIR / "envanter_snapshots"
LOG_CSV = OUT_DIR / "envanter_durumu_log.csv"
DASHBOARD_URL = "https://app.easycentral.com/"
FIELDS = ["zaman", "etiket", "envanter", "buybox_urun", "lowest_urun",
          "yuzdeler", "paket_limiti", "paket_kullanimi_pct", "gorsel"]

WIDGET_EXPR = """
(function () {
    var card = document.querySelector('.inventory-status-chart');
    if (!card) return null;
    card.scrollIntoView({block: 'center'});
    var r = card.getBoundingClientRect();
    return {
        text: card.innerText,
        x: r.x, y: r.y, w: r.width, h: r.height, vw: window.innerWidth,
        header: document.body.textContent
    };
})()
"""


def _num(text):
    return int(re.sub(r"[^\d]", "", text)) if re.search(r"\d", text) else None


def _debug_port_open():
    try:
        urllib.request.urlopen(f"http://{DEBUG_ADDRESS}/json/version", timeout=3)
        return True
    except Exception:
        return False


def parse_widget(text, page_text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    values = {}
    for i, line in enumerate(lines):
        if line in ("BuyBox Ürün", "Lowest Ürün", "Envanter") and i > 0:
            values[line] = _num(lines[i - 1])
    pcts = [l for l in lines if re.fullmatch(r"[\d.,]+%", l)]
    limit = re.search(r"Paket Limitiniz\s*([\d.]+)", page_text)
    usage = re.search(r"Toplam Paket Kullanımı\s*%?([\d.,]+)", page_text)
    return {
        "envanter": values.get("Envanter"),
        "buybox_urun": values.get("BuyBox Ürün"),
        "lowest_urun": values.get("Lowest Ürün"),
        "yuzdeler": " / ".join(pcts),
        "paket_limiti": _num(limit.group(1)) if limit else None,
        "paket_kullanimi_pct": usage.group(1) if usage else None,
    }


def take_snapshot(label=""):
    if not _debug_port_open():
        start_chrome_for_attachment(start_url=DASHBOARD_URL, headless=False)
        time.sleep(8)

    tab, session = open_cdp_session(DEBUG_ADDRESS, tab=_find_existing_tab(DEBUG_ADDRESS))
    try:
        session.call("Page.enable")
        session.call("Runtime.enable")
        session.call("Page.navigate", {"url": DASHBOARD_URL})
        info = None
        for _ in range(30):
            time.sleep(2)
            info = session.eval_json(WIDGET_EXPR)
            if info and re.search(r"\d", info["text"]):
                break
        if not info:
            raise RuntimeError("Envanter Durumu karti bulunamadi (giris yapili mi?)")
        time.sleep(3)  # grafik animasyonu bitsin
        info = session.eval_json(WIDGET_EXPR)

        now = datetime.now()
        safe = re.sub(r"[^\w\-]+", "_", label).strip("_") if label else "anlik"
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        png_path = SHOT_DIR / f"{now:%Y%m%d_%H%M%S}_{safe}.png"
        shot = session.call("Page.captureScreenshot", {"format": "png"})
        full = Image.open(io.BytesIO(base64.b64decode(shot["data"])))
        k = full.width / info["vw"]
        box = (max(0, int(info["x"] * k)), max(0, int(info["y"] * k)),
               min(full.width, int((info["x"] + info["w"]) * k)), min(full.height, int((info["y"] + info["h"]) * k)))
        full.crop(box).save(png_path)
    finally:
        session.close()

    row = parse_widget(info["text"], info["header"])
    row.update({"zaman": now.strftime("%Y-%m-%d %H:%M:%S"), "etiket": label, "gorsel": png_path.name})

    prev = None
    if LOG_CSV.exists():
        with LOG_CSV.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        prev = rows[-1] if rows else None
    new_file = not LOG_CSV.exists()
    with LOG_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in FIELDS})

    print(f"Kaydedildi: {png_path}")
    print(f"Envanter={row['envanter']} BuyBox={row['buybox_urun']} Lowest={row['lowest_urun']} "
          f"Paket=%{row['paket_kullanimi_pct']} / {row['paket_limiti']}")
    if prev and prev.get("envanter") and row["envanter"] is not None:
        try:
            print(f"Onceki kayda gore envanter degisimi: {row['envanter'] - int(prev['envanter']):+d} ({prev['zaman']})")
        except ValueError:
            pass
    return row


if __name__ == "__main__":
    take_snapshot(" ".join(sys.argv[1:]))
