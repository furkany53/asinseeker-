import csv
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "asin_list.txt"
OUTPUT_APPROVED = BASE_DIR / "approved_asins.csv"
OUTPUT_REJECTED = BASE_DIR / "rejected_asins.csv"
OUTPUT_BLOCKED = BASE_DIR / "blocked_asins.csv"
SCREENSHOT_DIR = BASE_DIR / "keepa_screenshots"


def read_asins(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"ASIN list not found: {path}")

    asins = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            value = line.strip()
            if not value:
                continue
            value = value.replace("\ufeff", "")
            value = value.split(",")[0].strip()
            value = value.split(";")[0].strip()
            value = value.split(" ")[0].strip()
            if re.fullmatch(r"[A-Z0-9]{10}", value):
                asins.append(value)
    return asins


def ensure_chrome_debug_session():
    port = 9222
    host = "127.0.0.1"
    try:
        with socket.create_connection((host, port), timeout=1):
            return
    except OSError:
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not Path(chrome_path).exists():
            raise FileNotFoundError(f"Chrome not found at: {chrome_path}")

        subprocess.Popen(
            [
                chrome_path,
                "--remote-debugging-port=9222",
                "--user-data-dir=C:\\Users\\onury\\AppData\\Local\\Google\\Chrome\\User Data",
                "--profile-directory=Default",
                "--new-window",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        time.sleep(5)

        try:
            with socket.create_connection((host, port), timeout=5):
                return
        except OSError as exc:
            raise RuntimeError(
                "Chrome could not be started with remote debugging enabled. Open Chrome manually and retry."
            ) from exc


def build_driver():
    ensure_chrome_debug_session()
    options = Options()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    return webdriver.Chrome(options=options)


def keepa_url(asin: str) -> str:
    return f"https://keepa.com/#!product/5-{asin}"


def page_status(driver) -> str:
    try:
        text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return "blocked"

    blocked_tokens = ["anti-bot", "cloudflare", "gerçek kişi", "verify you are human"]
    if any(token in text for token in blocked_tokens):
        return "blocked"

    has_year = any(token in text for token in ["1y", "12m", "year", "last 12 months"])
    has_sales = any(token in text for token in ["sales", "sold", "units", "offers", "buy box"])
    return "approved" if has_year and has_sales else "rejected"


def save_page_diagnostics(driver, asin: str):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    screenshot_path = SCREENSHOT_DIR / f"{asin}.png"
    driver.save_screenshot(str(screenshot_path))
    body_text = driver.find_element(By.TAG_NAME, "body").text.replace("\n", " ").strip()
    print(f"  URL: {driver.current_url}")
    print(f"  Title: {driver.title}")
    print(f"  Body preview: {body_text[:240]}")
    print(f"  Screenshot: {screenshot_path}")


def main():
    if not INPUT_FILE.exists():
        print(f"Missing input file: {INPUT_FILE}")
        sys.exit(1)

    asins = read_asins(INPUT_FILE)
    if not asins:
        print("No valid ASINs found.")
        sys.exit(1)

    print("Opening or attaching to the Chrome profile with debugging enabled...")
    driver = build_driver()
    approved = []
    rejected = []
    blocked = []

    try:
        for index, asin in enumerate(asins, start=1):
            print(f"[{index}/{len(asins)}] {asin}")
            driver.get(keepa_url(asin))
            time.sleep(5)
            save_page_diagnostics(driver, asin)

            status = page_status(driver)
            if status == "approved":
                approved.append(asin)
                print("APPROVED")
            elif status == "blocked":
                blocked.append(asin)
                print("BLOCKED - Cloudflare/anti-bot page")
            else:
                rejected.append(asin)
                print("REJECTED")

            time.sleep(1.2)
    finally:
        driver.quit()

    with OUTPUT_APPROVED.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["asin"])
        writer.writerows([[x] for x in approved])

    with OUTPUT_REJECTED.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["asin"])
        writer.writerows([[x] for x in rejected])

    with OUTPUT_BLOCKED.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["asin"])
        writer.writerows([[x] for x in blocked])

    print(f"Approved: {len(approved)}")
    print(f"Rejected: {len(rejected)}")
    print(f"Blocked: {len(blocked)}")
    print(f"Approved file: {OUTPUT_APPROVED}")
    print(f"Rejected file: {OUTPUT_REJECTED}")
    print(f"Blocked file: {OUTPUT_BLOCKED}")


if __name__ == "__main__":
    main()
