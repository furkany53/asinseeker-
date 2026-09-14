import json
from pathlib import Path

try:
    import keyring
except ImportError:
    keyring = None

APP_DATA_DIR = Path.home() / "AppData" / "Local" / "KeepaKesintiKontrol"
CONFIG_FILE = APP_DATA_DIR / "keepa_account.json"
SERVICE_NAME = "KeepaKesintiKontrolKeepaHesabi"


def save_credentials(username, password):
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"username": username}), encoding="utf-8")
    if keyring is not None and username:
        try:
            keyring.set_password(SERVICE_NAME, username, password)
        except Exception:
            pass


def load_username():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("username", "")
        except Exception:
            return ""
    return ""


def load_password(username):
    if keyring is not None and username:
        try:
            return keyring.get_password(SERVICE_NAME, username) or ""
        except Exception:
            return ""
    return ""


def has_account():
    return bool(load_username())
