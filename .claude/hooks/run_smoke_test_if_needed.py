"""PostToolUse hook: keepa_gui.py, keepa_check.py veya keepa_finder.py
degistiginde test_gui_smoke.py'yi otomatik calistirir (CLAUDE.md kurali)."""
import json
import subprocess
import sys
from pathlib import Path

WATCHED_FILES = {"keepa_gui.py", "keepa_check.py", "keepa_finder.py"}

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

file_path = payload.get("tool_input", {}).get("file_path", "")
if Path(file_path).name not in WATCHED_FILES:
    sys.exit(0)

project_dir = payload.get("cwd") or str(Path(__file__).resolve().parents[2])
result = subprocess.run(
    [sys.executable, "test_gui_smoke.py"],
    cwd=project_dir,
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    sys.stderr.write(
        f"test_gui_smoke.py basarisiz oldu ({Path(file_path).name} degisikligi sonrasi):\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
    sys.exit(2)

sys.exit(0)
