# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This directory is a personal workspace, not a single project. It contains two unrelated things plus a set of vendored third-party skill packages:

1. **AsinSeeker** — the primary, actively-developed codebase: a suite of Python tools for Amazon Japan (co.jp) dropshipping. This is what almost all work in this repo concerns. See below.
2. **A static landing page** (`index.html`, `styles.css`, `script.js`, `package.json` for `easy-keepa-filter.mjs`) — a small, separate, framework-free site. Its own conventions are documented in `AGENTS.md`; don't conflate it with AsinSeeker.
3. **Vendored skill/plugin repos** (`andrej-karpathy-skills/`, `caveman/`, `skill-creator/`, `superpowers/`, `ui-ux-pro-max-skill/`, `OpenMontage/`) — independent third-party projects with their own `CLAUDE.md`/`README.md`. They are not part of this project's code; don't try to maintain or refactor them from here.

Everything below is about AsinSeeker.

## What AsinSeeker does

A three-stage pipeline for finding, validating, and submitting Amazon JP ASINs for dropshipping, plus a licensed Tkinter desktop app (`keepa_gui.py`) that wraps it and is distributed as a Windows `.exe` to paying customers:

1. **Discover** candidate ASINs via Keepa's Product Finder API (`/query`), either by sweeping Sales Rank ranges or via named Buy-Box-focused strategy presets.
2. **Validate/clean** each ASIN against Keepa's per-product history — detect gaps in the seller-count history ("kesinti"), require a full year of tracking, flag suspected dead stock, and (recently added) flag JP customs-risk product titles.
3. **Submit** the clean, priority-ranked ASINs into EasyCentral (a Turkish Amazon-store-management SaaS) via browser automation, stopping short of the final "start scan" click (that step consumes EasyCentral's quota, so it's left to the human).

## Commands

```bash
# Run the desktop app
python keepa_gui.py

# Run the 3-layer smoke test suite (black-box launch, functional, unit)
python test_gui_smoke.py

# Standalone ASIN-harvesting CLI (Sales Rank sweep)
python keepa_finder.py

# Rebuild the distributable .exe (spec lives in build_tmp/, output goes to dist/)
cd build_tmp && python -m PyInstaller AsinSeeker.spec --distpath ../dist --workpath .

# Install the freshly built exe locally (creates Start Menu / Desktop shortcuts)
powershell -ExecutionPolicy Bypass -File dist/Kurulum.ps1

# Large-scale background jobs (see "Full-pool background pipeline" below)
python full_pool_check.py
python run_web_prefilter.py
```

There is no linter/formatter configured; `test_gui_smoke.py` is the only automated check. Run it after any change to `keepa_gui.py`, `keepa_check.py`, or `keepa_finder.py` before considering a change done.

## Architecture

### CDP-based browser automation (not Selenium)

Anything that needs a live, logged-in Chrome session talks to it via raw Chrome DevTools Protocol websocket calls — `CDPSession` / `open_cdp_tab` / `close_cdp_tab` in `keepa_check.py`. This is reused by `easycentral_keepa_bot.py`, `keepa_login.py`, `easycentral_target.py`, and `keepa_web_prefilter.py`. Chrome is always launched against a dedicated automation profile (`C:\chrome_debug_temp`, port 9222 via `start_chrome_for_attachment()` in `easycentral_keepa_bot.py`) — Chrome refuses to enable remote debugging on a user's real/default profile for security reasons, so don't try to attach to it directly.

### Two independent check implementations, same output shape

`keepa_check.py` has two ways to check one ASIN, both returning the same dict (`asin`/`status`/`reason`/`gap_count`/`gaps_px`/`dead_stock_suspected`/`score`):

- `keepa_check_detailed()` — opens the real Keepa product page and hooks `CanvasRenderingContext2D` draw calls to read the seller-count chart directly out of the canvas commands. Slower, needs Chrome, but was the original/validated method.
- `keepa_check_detailed_api()` — reads the same signal from the `/product` API's `csv[11]` (COUNT_NEW) series. Much faster/cheaper, no Chrome needed, and was empirically validated to match the Chrome method exactly before being made the default ("API Modu").

When changing the check logic, keep both in sync or make sure only one is actually in use for the change in question.

### Two separate Keepa credential stores — do not merge them

- `keepa_api_settings.py` — `keyring`-based, one API key per customer, used by the GUI's "API Modu". This is what real end-users (buyers of AsinSeeker) configure.
- `keepa_finder.py`'s `load_api_key()` — env var `KEEPA_API_KEY` or a local `keepa_api_key.txt` file. This is the internal/personal key used by the standalone CLI scripts and the background pipeline below. It is intentionally not wired to the per-customer keyring store.

### Frozen-exe path handling

Every entry-point module resolves its base directory with this exact pattern (PyInstaller onefile builds otherwise resolve `__file__` to a temp extraction folder that's deleted after each run):

```python
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
```

Apply the same pattern to any new module that needs to read/write files relative to the install location.

### Full-pool background pipeline (separate from the GUI)

`full_pool_check.py`, `run_web_prefilter.py`, and `keepa_web_prefilter.py` are standalone scripts for processing tens of thousands of ASINs — too slow to run interactively from the GUI. They're meant to be launched with `nohup python <script>.py > <logfile> 2>&1 &` and left running; all of them are resumable (they read their own previous output CSVs and skip ASINs already processed) and write incrementally so a kill/crash never loses progress. State lives under `keepa_full_kontrol/` (`sonuclar.csv`, `web_onfiltre.csv`, `uygun_siralanmis.txt`).

Two genuinely separate Keepa quota systems are in play here: API tokens (metered per `/product` and `/query` call, ~25/min refill) and the Keepa **website's** own "Data Allowance" (used only by logged-in browser sessions, e.g. Product Viewer exports). `keepa_web_prefilter.py` exploits the free website quota specifically to pre-check "1 year of tracking history" (via the `Tracking since` field) before spending an API token — but the website export only has summary/90-day stats, not the raw time series, so it cannot replace `keepa_check_detailed_api()` for the actual gap check.

### `keepa_sync/`

A separate, minimal git repository (distinct from this one) used to sync harvested ASIN pools across machines (main PC + a remote VDS), with its own sync daemons. `ortak_asin_havuzu.txt` inside it is the cumulative, deduplicated pool that all discovery runs feed into and that the full-pool pipeline reads from.

### Strategy presets and the "verify before trusting" rule

`keepa_finder.STRATEGIES` holds named, Buy-Box-focused Keepa Finder query presets (`build_strategy_selection`, `fetch_strategy_asins`). These — and any future Keepa filter field someone suggests — must be **live-tested against a real API key before being trusted**: this project has twice hit AI-suggested Keepa parameters that looked plausible but were subtly wrong (a wrong domain ID that would have scanned Canada instead of Japan; `_lte: 0` filters that silently zeroed all results because Keepa represents "no offer of this type" as `-1`, not `0`). Never wire a new Keepa field into production code without confirming its effect on `totalResults` first.

### Licensing

`license_guard.py` implements a trial/paid-license gate (HMAC-signed keys tied to a machine ID) enforced by the GUI. `keygen.py` is for the software owner only — it must never ship in the customer installer.

## Known platform quirks worth remembering

- Windows: any subprocess call to a console-subsystem binary (e.g. `taskkill`) from this `--noconsole` GUI needs `creationflags=subprocess.CREATE_NO_WINDOW`, or a console window flashes briefly.
- Keepa's `/query` selection JSON uses `-1` as a sentinel for "this offer type doesn't exist" in several fields (e.g. `current_USED_gte/lte`) — treat it as a magic value, not a normal lower bound.
- Domain IDs: `5` = Amazon.co.jp (this project's target), `1` = .com. Don't assume other IDs without checking Keepa's domain table.
