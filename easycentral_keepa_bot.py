import csv
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from keepa_check import keepa_status

# PyInstaller onefile ile paketlenince __file__ GERCEK exe konumunu degil,
# her calistirmada silinen GECICI bir cikarma klasorunu (sys._MEIxxxxx)
# gosterir -- ekran goruntuleri/sonuclar boylece hicbir zaman kalici
# olmayan bir yere yaziliyordu (denendi, dogrulandi). Frozen halde
# sys.executable'in bulundugu GERCEK klasoru kullaniyoruz.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_DIR / "keepa_screenshots"
RESULTS_FILE = BASE_DIR / "easycentral_keepa_results.csv"
KEEPA_URL = "https://keepa.com/#!product/5-{}"
EASYCENTRAL_URL = "https://app.easycentral.com/product-upload-queue"
DEBUG_ADDRESS = os.getenv("CHROME_DEBUG_ADDRESS", "127.0.0.1:9222")
AUTO_ACTIONS = os.getenv("EASYCENTRAL_AUTO_ACTIONS", "false").lower() == "true"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_PROFILE = Path(r"C:\chrome_debug_temp")


def debug_port_is_open():
    host, port_text = DEBUG_ADDRESS.split(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=1):
            return True
    except OSError:
        return False


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def close_existing_chrome_processes():
    # taskkill.exe konsol tabanli bir program; --noconsole ile derlenmis bir
    # GUI exe'den creationflags olmadan cagrilirsa Windows kisa sureli bir
    # siyah konsol penceresi acip kapatir ("hayalet pencere" goruntusu --
    # kullanicidan geldi, dogrulandi). CREATE_NO_WINDOW bunu tamamen engeller.
    for process_name in ("chrome.exe", "chromedriver.exe"):
        subprocess.run(
            ["taskkill", "/F", "/IM", process_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    time.sleep(1)


def start_chrome_for_attachment(start_url=EASYCENTRAL_URL, headless=False):
    if not Path(CHROME_PATH).exists():
        raise FileNotFoundError(f"Chrome bulunamadı: {CHROME_PATH}")

    print("Chrome ve driver süreçleri kapatılıyor...")
    close_existing_chrome_processes()

    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    print("Chrome debug modda açılıyor...")
    args = [
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_ADDRESS.rsplit(':', 1)[1]}",
        f"--user-data-dir={CHROME_PROFILE}",
        "--profile-directory=Default",
        # Yeni Chrome surumleri, bu bayrak olmadan localhost'tan gelen CDP
        # WebSocket baglantilarini bile 403 ile reddediyor (canli
        # dogrulandi: "Rejected an incoming WebSocket connection...").
        "--remote-allow-origins=*",
    ]
    if headless:
        # Denendi: --headless=new modunda Keepa'nin grafigi guvenilir
        # yuklenmiyor (WebSocketTimeoutException / grafik_yuklenemedi artiyor).
        # Bunun yerine PENCEREYI NORMAL ac ama ekranin tamamen disina, gorunmez
        # bir koordinata konumlandir -- Keepa normal (headed) tarayici gibi
        # davranir, kullanici ise pencereyi hic gormez.
        args += ["--window-position=-32000,-32000", "--window-size=1400,1000"]
    args.append(start_url)
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    for _ in range(20):
        if debug_port_is_open():
            return
        time.sleep(1)
    raise RuntimeError("Chrome debug portu açılmadı. Chrome penceresini kapatıp tekrar deneyin.")


def build_driver():
    start_chrome_for_attachment()
    options = Options()
    options.add_experimental_option("debuggerAddress", DEBUG_ADDRESS)
    return webdriver.Chrome(options=options)


def activate_suitable_products_filter(driver):
    filter_selectors = [
        "//*[normalize-space()='Uygun Ürünler']",
        "//*[contains(normalize-space(), 'Uygun Ürünler')]",
        "//*[contains(@class, 'active-product') and contains(., 'Uygun')]",
    ]

    filter_element = None
    for selector in filter_selectors:
        candidates = driver.find_elements(By.XPATH, selector)
        for candidate in candidates:
            if candidate.is_displayed():
                filter_element = candidate
                break
        if filter_element is not None:
            break

    if filter_element is None:
        raise RuntimeError("'Uygun Ürünler' filtresi bulunamadı.")

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", filter_element)
    driver.execute_script("arguments[0].click();", filter_element)
    time.sleep(7)

    selected = filter_element.get_attribute("class") or ""
    aria_selected = filter_element.get_attribute("aria-selected") or ""
    print(f"Uygun Ürünler filtresine tıklandı. class={selected!r}, aria-selected={aria_selected!r}")

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, "//table/tbody/tr[1]"))
    )


def extract_top_asin(driver, processed_asins):
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//table/tbody/tr"))
        )
    except Exception:
        return None

    rows = driver.find_elements(By.XPATH, "//table/tbody/tr")
    for row in rows:
        match = re.search(r"\b[A-Z0-9]{10}\b", row.text)
        if match and match.group(0) not in processed_asins:
            return match.group(0)
    return None


def accept_easycentral_confirmation(driver):
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            alert = driver.switch_to.alert
            message = alert.text
            alert.accept()
            print(f"EasyCentral tarayıcı onayı kabul edildi: {message}")
            return True
        except Exception:
            pass

        buttons = driver.find_elements(
            By.XPATH,
            "//button[normalize-space()='Tamam' or normalize-space()='Onayla' or normalize-space()='Confirm']",
        )
        confirmation = next((button for button in buttons if button.is_displayed()), None)
        if confirmation is not None:
            driver.execute_script("arguments[0].click();", confirmation)
            print("EasyCentral HTML onayı kabul edildi.")
            return True
        time.sleep(0.5)
    return False


def apply_action(driver, asin, action):
    if not AUTO_ACTIONS:
        print(f"{asin}: {action.upper()} önerildi; otomatik EasyCentral işlemi kapalı.")
        return

    row = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, f"//table/tbody/tr[contains(., '{asin}')][1]"))
    )
    checkbox_candidates = row.find_elements(
        By.XPATH,
        ".//input[@type='checkbox'] | .//*[contains(@class, 'ui-check')]",
    )
    if not checkbox_candidates:
        raise RuntimeError(f"{asin}: satır seçim kutusu bulunamadı.")
    checkbox = checkbox_candidates[0]
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox)
    driver.execute_script("arguments[0].click();", checkbox)

    action_name = "YÜKLE" if action.lower() == "upload" else "SİL"
    action_menu = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located(
            (By.XPATH, "//*[contains(normalize-space(), 'İşlem Yap')][1]")
        )
    )
    driver.execute_script("arguments[0].click();", action_menu)

    if action_name == "YÜKLE":
        print(">>> 'Mağazaya Yükle' seçeneği tetikleniyor...")
        action_option = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[contains(@class, 't-content')]//span[contains(text(), 'Mağazaya Yükle')] | //span[text()='Mağazaya Yükle']",
                )
            )
        )
        label = "Mağazaya Yükle"
    else:
        print(">>> 'Havuzdan Sil' seçeneği tetikleniyor...")
        action_option = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[contains(@class, 't-content')]//span[contains(text(), 'Havuzdan Sil')] | //span[text()='Havuzdan Sil']",
                )
            )
        )
        label = "Havuzdan Sil"

    driver.execute_script("arguments[0].click();", action_option)
    print(f"[PANEL] {label} seçildi; onay bekleniyor...")

    if not accept_easycentral_confirmation(driver):
        raise RuntimeError(f"{asin}: {label} onay penceresi 15 saniyede bulunamadı.")

    if action == "delete":
        row_xpath = f"//table/tbody/tr[contains(., '{asin}')][1]"
        WebDriverWait(driver, 15).until(
            lambda current_driver: not current_driver.find_elements(By.XPATH, row_xpath)
            or asin not in current_driver.find_element(By.TAG_NAME, "table").text
        )
    else:
        time.sleep(3)
    print(f"{asin}: {label} işlemi tamamlandı.")


def save_asin_error(driver, asin, error):
    report_path = BASE_DIR / "hata_raporu.txt"
    with report_path.open("a", encoding="utf-8") as report:
        report.write(f"\n{'=' * 70}\nASIN: {asin}\n")
        report.write(traceback.format_exc())
    error_image = SCREENSHOT_DIR / f"{asin}_HATA.png"
    try:
        driver.save_screenshot(str(error_image))
    except Exception:
        pass
    print(f"{asin}: {type(error).__name__}: {error}")
    print(f"Hata raporu: {report_path}")
    print(f"Hata görüntüsü: {error_image}")


def main():
    print(f"Chrome debug adresi: {DEBUG_ADDRESS}")
    print("Not: yıllık gösterge aktif değilse ürün otomatik olarak silinir.")
    driver = build_driver()
    results = []
    processed_asins = set()
    try:
        driver.get(EASYCENTRAL_URL)
        print("EasyCentral açık. Ürün kuyruğu tablosu otomatik olarak bekleniyor...")
        WebDriverWait(driver, 180).until(
            EC.presence_of_element_located((By.XPATH, "//table/tbody/tr[1]"))
        )
        activate_suitable_products_filter(driver)
        while True:
            asin = extract_top_asin(driver, processed_asins)
            if not asin:
                print("İşlenecek ASIN kalmadı.")
                break
            try:
                status = keepa_status(asin)
                results.append((asin, status))
                print(f"{asin}: {status}")
                apply_action(driver, asin, status)
                processed_asins.add(asin)
            except Exception as error:
                save_asin_error(driver, asin, error)
                processed_asins.add(asin)
                results.append((asin, "error"))
                try:
                    driver.get(EASYCENTRAL_URL)
                    activate_suitable_products_filter(driver)
                    print(f"{asin}: atlandı, sonraki ASIN için devam ediliyor.")
                except Exception as recovery_error:
                    save_asin_error(driver, asin, recovery_error)
                    print("EasyCentral kurtarma başarısız; tarayıcı bağlantısı kontrol edilemiyor.")
                    break
    except Exception as error:
        report = traceback.format_exc()
        (BASE_DIR / "hata_raporu.txt").write_text(report, encoding="utf-8")
        try:
            driver.save_screenshot(str(BASE_DIR / "bot_hata.png"))
        except Exception:
            pass
        print(f"Beklenmeyen hata: {type(error).__name__}: {error}")
        print("Detay: hata_raporu.txt | Görüntü: bot_hata.png")
        print("Tarayıcı açık bırakıldı; bot katılımsız modda sonlandı.")
    finally:
        with RESULTS_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["asin", "status"])
            writer.writerows(results)
        print(f"Sonuçlar: {RESULTS_FILE}")
        print(f"Ekran görüntüleri: {SCREENSHOT_DIR}")


if __name__ == "__main__":
    main()
