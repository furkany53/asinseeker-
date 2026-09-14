import csv
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

try:
    import undetected_chromedriver as uc
except Exception:  # pragma: no cover
    uc = None


BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "asin_list.txt"
OUTPUT_APPROVED = BASE_DIR / "approved_asins.csv"
OUTPUT_REJECTED = BASE_DIR / "rejected_asins.csv"


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


def build_driver():
    options = Options()
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if os.path.exists(chrome_path):
        options.binary_location = chrome_path

    user_data_dir = r"C:\Users\onury\AppData\Local\Google\Chrome\User Data"
    if os.path.exists(user_data_dir):
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--profile-directory=Default")
    else:
        temp_profile = tempfile.mkdtemp(prefix="chrome_keepa_")
        options.add_argument(f"--user-data-dir={temp_profile}")
        options.add_argument("--profile-directory=Profile 1")

    options.add_argument("--window-size=1920,1080")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-features=Translate,OptimizationHints")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )
        return driver
    except Exception:
        if uc is not None:
            return uc.Chrome(options=options)
        raise


def keepa_url(asin: str) -> str:
    return f"https://keepa.com/#!product/5-{asin}"


def has_1y_sales(driver, asin: str) -> bool:
    try:
        driver.get(keepa_url(asin))
        time.sleep(4)

        # Try the year tabs and key labels.
        year_buttons = driver.find_elements(By.XPATH, "//button[contains(., '1Y')] | //button[contains(., 'Year')] | //span[contains(., '1Y')] | //span[contains(., 'Year')] | //div[contains(., '1Y')] ")
        if year_buttons:
            for btn in year_buttons[:10]:
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    break
                except Exception:
                    pass

        body_text = driver.find_element(By.TAG_NAME, "body").text
        profile = body_text.lower()

        has_year_data = "1y" in profile or "year" in profile or "12m" in profile
        has_sales_signal = any(token in profile for token in ["sales", "sold", "units", "buy box", "offers"])
        if has_year_data and has_sales_signal:
            return True

        try:
            driver.find_element(By.XPATH, "//*[contains(text(), 'Price History') or contains(text(), 'Amazon Price History')]")
            return True
        except Exception:
            return False

    except (NoSuchWindowException, WebDriverException):
        return False


def main():
    if not INPUT_FILE.exists():
        print(f"Missing input file: {INPUT_FILE}")
        print("Create a file named asin_list.txt with one ASIN per line.")
        sys.exit(1)

    asins = read_asins(INPUT_FILE)
    if not asins:
        print("No valid ASINs found.")
        sys.exit(1)

    driver = build_driver()
    approved = []
    rejected = []

    try:
        for index, asin in enumerate(asins, start=1):
            print(f"[{index}/{len(asins)}] {asin}")
            try:
                ok = has_1y_sales(driver, asin)
            except (NoSuchWindowException, WebDriverException):
                driver.quit()
                driver = build_driver()
                ok = has_1y_sales(driver, asin)

            if ok:
                approved.append(asin)
                print("APPROVED")
            else:
                rejected.append(asin)
                print("REJECTED")

            time.sleep(1.5)

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

    print(f"Approved: {len(approved)}")
    print(f"Rejected: {len(rejected)}")
    print(f"Approved file: {OUTPUT_APPROVED}")
    print(f"Rejected file: {OUTPUT_REJECTED}")


if __name__ == "__main__":
    main()
