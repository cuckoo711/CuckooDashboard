# Cuckoo Dashboard

A real-time system monitoring dashboard with MiMo Token Plan tracking, GitHub contribution heatmap, and desktop audio player with synchronized lyrics.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![Flask](https://img.shields.io/badge/Flask-3.x-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### System Monitoring
- CPU / GPU / Memory usage with real-time ring gauges
- CPU actual frequency via PDH performance counters
- GPU utilization and VRAM usage via PDH + WMI (PowerShell fallback)
- Physical disk partition overview with capacity bars
- Network throughput (upload / download)
- Uptime tracking
- GPU detection (AMD Radeon series, LUID-based mapping)
- Physical disk hot-plug detection (30s interval)
- Hardware override support (CPU model, GPU model, memory name, VRAM size)

### MiMo Token Plan
- Token Plan usage ring with remaining quota
- Daily token consumption breakdown (input / output / cache hit)
- Stacked bar visualization
- Model-level usage breakdown
- Pay-as-you-go usage tracking
- Multi-source aggregation: MiMo + Local Platforms + NUG

### GitHub Contribution Heatmap
- Full-year contribution calendar
- Dual fetch strategy: GitHub GraphQL API (precise) or web scraping (estimated)
- Three-tier caching: memory TTL (10 min) + disk JSON (24h) + 3-retry with stale fallback
- Auto-refresh based on Vibe Coding mode

### Desktop Audio Player
- Real-time media info via Windows SMTC
- Data source priority: YesPlayMusic local API > SMTC + UI Automation fallback
- Synchronized lyrics display with smooth scrolling
- Dual lyrics source: Netease Cloud Music + QQ Music fallback
- Smart song matching with scoring algorithm (artist match, junk keyword penalty)
- Lyrics time offset adjustment
- Lyrics reload button
- System-level playback controls (play/pause/next/prev via SMTC)

### Dashboard Themes
- Default dark theme (image background)
- Clean mono theme (solid light background)
- Click the red dot in the top-left corner to toggle
- Theme state is persisted and synchronized across WebSocket clients
- Styles are driven by `body[data-theme]` CSS variables; to add a theme, add its backend entry in `THEMES` and define a matching `body[data-theme="..."]` variable block

### Vibe Coding Mode
- Toggle between Coding (high-frequency refresh, 20s) and Chilling (60s) modes
- Persisted to config and synchronized across all connected clients via WebSocket
- REST fallback (`/api/vibe`) for when WebSocket is unavailable

### Off-Peak Badge
- Configurable time ranges for off-peak billing display (default 00:00-08:00 Beijing time)
- Supports multiple time slots and midnight-crossing intervals

## Screenshots

> Run the dashboard and open `http://localhost:5000` in your browser to see it live.

## Installation

```bash
# Clone the repository
git clone https://github.com/cuckoo711/CuckooDashboard.git
cd CuckooDashboard

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

- `flask` / `flask-sock` - Web server and WebSocket push
- `psutil` - System monitoring
- `pyyaml` - Configuration parsing
- `requests` - HTTP client
- `rich` - Terminal formatting (for CLI tool)
- `pywebview` - Native desktop window (optional)
- `pywin32` - PDH/WMI performance counters, COM automation
- `winrt-*` - Windows SMTC media info
- `uiautomation` - Progress bar fallback via UI Automation
- `segno` / `qrcode` - QR code for login (optional)

## Usage

### 1. Login to MiMo

```bash
python src/mimo_usage.py

# Options:
#   1. QR Code login (scan with Xiaomi phone)
#   2. Browser Cookie (auto-read from Chrome/Edge)
#   3. Password login
#   4. Manual Cookie input
```

Or with command-line flags:

```bash
python src/mimo_usage.py --login browser --save   # Auto-refresh cookies
python src/mimo_usage.py --login qr               # QR code scan
python src/mimo_usage.py --json                   # JSON output
```

### 2. Start the Dashboard

```bash
python run_dashboard.py
# Open http://localhost:5000 in your browser
```

```bash
# Custom port and settings
python run_dashboard.py --port 8080 --host 0.0.0.0
python run_dashboard.py --dev   # Debug mode with auto-reload
```

### 3. Desktop App (optional)

```bash
python run_desktop.py
# Native fullscreen window on target monitor, no browser needed

python run_desktop.py --port 8080
python run_desktop.py --dev        # Show console and debug tools
python run_desktop.py --install    # Register Windows startup
python run_desktop.py --uninstall  # Remove Windows startup
```

The desktop app reads `data/monitor.json` to determine which display to use, then launches a frameless, always-on-top, fullscreen PyWebView window with DPI awareness.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard HTML page |
| `/ws` | WebSocket | Bidirectional: server pushes data, client sends vibe/init commands |
| `/api/data` | GET | MiMo usage data + GitHub contributions |
| `/api/health` | GET | Lightweight cached service health; does not refresh external data |
| `/api/system` | GET | System hardware info (CPU/GPU/Memory/Disk/Network) |
| `/api/nug` | GET | NUG platform balance |
| `/api/nug/channels` | GET | NUG per-channel usage breakdown (7 days) |
| `/api/media` | GET | Current media info + lyrics |
| `/api/media/reload` | POST | Clear lyrics cache and refetch |
| `/api/media/offset` | GET/POST | Read or update lyric offset (supports delta or absolute) |
| `/api/player/<action>` | POST | Media controls: `play`, `pause`, `next`, `prev`, `toggle` |
| `/api/vibe` | GET/POST | Read or set Vibe Coding mode |
| `/api/theme` | GET/POST | Read or set the active theme by name |
| `/api/theme/next` | POST | Switch to the next theme |
| `/api/off-peak-badge` | GET | Off-peak time range configuration for badge display |

POST endpoints require same-origin `Origin`/`Referer` or an `X-Dashboard-Token` header. Set `dashboard.token` in `config.yaml` or `DASHBOARD_TOKEN` env var when exposing the server beyond `127.0.0.1`.

## Configuration

Copy `config/config.example.yaml` to `config/config.yaml` and fill only values you need. The config file, cookies, token caches, GitHub cache, and all runtime state are git-ignored.

### Config Sections (`config/config.yaml`)

| Section | Description |
|---|---|
| `dashboard.token` | Optional token for POST endpoint protection |
| `dashboard.off_peak_badge` | Off-peak time range and enable/disable |
| `github_token` | GitHub Personal Access Token for precise contribution data |
| `local_platforms` | Local MiMo-compatible platform instances (JWT auth) |
| `nug` | NUG (NarraFork) platform credentials |
| `hardware_overrides` | Manual corrections for CPU/GPU/memory detection |
| `theme` | Active theme name (auto-saved) |
| `lyric_offset` | Lyric timing offset in seconds (auto-saved) |
| `vibe_active` | Vibe Coding mode state (auto-saved) |

### Environment Variables

| Key | Description |
|---|---|
| `DASHBOARD_TOKEN` | Optional token accepted via `X-Dashboard-Token` for protected POST APIs |

## Project Structure

```
.
├── run_dashboard.py          # Entry point: start web dashboard
├── run_desktop.py            # Entry point: start native desktop app
├── requirements.txt          # Python dependencies
├── config/                   # User-editable configuration (git-ignored except example)
│   ├── config.example.yaml       # Template (copy to config.yaml)
│   ├── config.yaml               # Private config (git-ignored)
│   └── cookies.json              # MiMo login cookies (git-ignored)
├── src/
│   ├── dashboard.py          # Flask app, routes, WebSocket orchestration, background broadcaster
│   ├── desktop.py            # PyWebView native window wrapper with monitor detection
│   ├── mimo_usage.py         # MiMo login CLI tool (QR/browser/password/manual)
│   ├── smtc_worker.py        # Standalone subprocess: SMTC + UIA + YesPlayMusic listener
│   ├── core/                 # Core infrastructure
│   │   ├── config.py             # YAML config load/save with mtime caching
│   │   ├── cache.py              # TTLCache utility
│   │   ├── proc.py               # PowerShell/subprocess execution (hidden window)
│   │   ├── perfcounters.py       # PDH/WMI performance counter sampling
│   │   └── monitor.py            # Windows display enumeration
│   ├── providers/            # Plugin-based data source system
│   │   ├── __init__.py           # Auto-discovery + dashboard aggregation (fetch_all_data)
│   │   ├── base.py               # Plugin interface specification
│   │   ├── mimo/                 # MiMo official platform (Cookie auth)
│   │   │   ├── api.py                # MiMoAPI wrapper, cookie validity check, auto-refresh
│   │   │   └── __init__.py           # Capabilities: token_plan, balance, api_usage
│   │   ├── nug/                  # NUG (NarraFork) platform (session cookie auth)
│   │   │   ├── client.py             # NUGClient HTTP client with 401 auto-relogin
│   │   │   └── __init__.py           # Capabilities: balance, api_usage
│   │   └── local_platform/       # Local MiMo-compatible platforms (JWT auth)
│   │       ├── client.py             # LocalMimoAPI with token persistence
│   │       ├── token_cache.py        # JWT token disk cache
│   │       └── __init__.py           # Capabilities: token_plan
│   ├── services/             # Business logic services
│   │   ├── github_service.py     # GitHub heatmap fetch/cache (GraphQL + scraping)
│   │   ├── media_service.py      # SMTC media state, Netease + QQ Music lyrics
│   │   ├── system_service.py     # System hardware and runtime metrics
│   │   ├── player_service.py     # Windows SMTC playback controls
│   │   ├── off_peak_service.py   # Off-peak time range badge config
│   │   ├── health_service.py     # Service health aggregation
│   │   ├── theme.py              # Theme metadata and persistence
│   │   └── config.py             # Backward-compat re-export of core.config
│   ├── static/
│   │   ├── dashboard.html
│   │   ├── dashboard.css
│   │   ├── dashboard.js
│   │   └── bg/                   # Theme background images
│   └── tests/
│       ├── test_lyrics.py
│       ├── test_off_peak_service.py
│       └── test_perfcounters.py
├── data/                         # Auto-generated caches and runtime files (git-ignored)
│   ├── github_cache.json
│   ├── local_tokens.json
│   ├── monitor.json              # Target display config for desktop mode
│   ├── dashboard_err.log
│   └── desktop_err.log
└── venv/
```

## Architecture

### WebSocket Real-Time Push

The dashboard uses a single WebSocket connection (`/ws`) for all real-time data:

1. On connect: server asynchronously pushes all data categories (vibe → mimo → github → media → system → nug → theme)
2. Background broadcaster thread (1s interval): parallel fetch of system + media + github, broadcast to all clients
3. MiMo/NUG data refreshes at dynamic intervals based on Vibe Coding mode (20s coding / 60s chilling)
4. Client can send `{"type": "vibe", "active": true/false}` to toggle mode or `{"type": "init"}` to request full refresh

### Provider Plugin System

Providers are auto-discovered from `src/providers/*/` directories. Each plugin declares `CAPABILITIES` and implements standardized functions:

- **token_plan**: `get_plan_detail()`, `get_plan_usage()`, `get_daily_detail()`, `get_model_breakdown()`
- **balance**: `get_balance()`
- **api_usage**: `get_usage_summary()`, `get_channel_breakdown()`
- **All plugins**: `get_status()` for health reporting

### Performance Counter Sampling

GPU and CPU metrics use a tiered approach:
1. **Primary**: Native PDH counters + WMI (via pywin32) — low overhead, per-thread sampler instances
2. **Fallback**: PowerShell `Get-Counter` / `Get-CimInstance` — higher latency but always available

GPU LUID mapping stabilizes across refresh cycles to prevent card assignment flicker.

## Security

- `config/` stores all user secrets and is git-ignored (except the example template).
- `data/` stores auto-generated caches and is fully git-ignored.
- `config/config.example.yaml` contains structure only; never put real credentials in it.
- Protected POST endpoints reject cross-site requests unless they are same-origin or include `X-Dashboard-Token`.
- Cookie auto-refresh uses passToken; credentials are never stored in plaintext beyond the encrypted cookie file.
- Internal network HTTPS with self-signed certs is auto-detected and verification skipped for local platform clients.

## Acknowledgments

Login flow inspired by [0xtbug/Mimo-Usage](https://github.com/0xtbug/Mimo-Usage) and [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).

## License

MIT
