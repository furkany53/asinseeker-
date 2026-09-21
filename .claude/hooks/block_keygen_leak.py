"""PreToolUse hook: keygen.py'nin dist/ veya build_tmp/ altina yazilmasini/
kopyalanmasini engeller (musteri kurulumuna asla gitmemesi gereken dosya)."""
import json
import re
import sys

FORBIDDEN_DIRS = ("dist/", "dist\\", "build_tmp/", "build_tmp\\")

# Sadece gercek dosya-kopyalama/tasima komutlarini yakala -- "keygen.py" ve
# "dist" kelimelerinin bir aciklama/log satirinda birlikte gecmesi yeterli
# degil (bu, bu hook'un ilk halinde kendi test komutunu bile yanlislikla
# engelledigi icin duzeltildi).
COPY_VERBS = re.compile(
    r"\b(cp|copy|move|mv|xcopy|robocopy|Copy-Item|Move-Item)\b", re.IGNORECASE
)

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name = payload.get("tool_name", "")
tool_input = payload.get("tool_input", {})


def is_forbidden_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return "keygen.py" in normalized and any(
        d.replace("\\", "/") in normalized for d in FORBIDDEN_DIRS
    )


blocked_reason = None

if tool_name in ("Write", "Edit", "MultiEdit"):
    file_path = tool_input.get("file_path", "")
    if is_forbidden_path(file_path):
        blocked_reason = f"'{file_path}' -- keygen.py musteri kurulumuna (dist/build_tmp) asla gitmemeli."
elif tool_name == "Bash":
    command = tool_input.get("command", "")
    mentions_target_dir = "dist" in command or "build_tmp" in command
    if "keygen.py" in command and mentions_target_dir and COPY_VERBS.search(command):
        blocked_reason = f"Bash komutu keygen.py'yi dist/build_tmp'a kopyalamaya/tasimaya calisiyor: {command}"

if blocked_reason:
    sys.stderr.write(
        "ENGELLENDI: " + blocked_reason +
        "\nkeygen.py sadece yazilim sahibi icindir, musteri exe'sine asla dahil edilmemeli (CLAUDE.md)."
    )
    sys.exit(2)

sys.exit(0)
