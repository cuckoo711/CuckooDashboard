# Cuckoo Dashboard

A real-time system monitoring dashboard with MiMo Token Plan tracking, GitHub contribution heatmap, and desktop audio player with synchronized lyrics.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![Flask](https://img.shields.io/badge/Flask-3.x-green) ![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### System Monitoring
- CPU / GPU / Memory usage with real-time ring gauges
- Physical disk partition overview with capacity bars
- Network throughput (upload / download)
- Uptime tracking
- GPU detection (AMD Radeon series)
- Physical disk hot-plug detection

### MiMo Token Plan
- Token Plan usage ring with remaining quota
- Daily token consumption breakdown (input / output / cache hit)
- Stacked bar visualization
- Model-level usage breakdown
- Pay-as-you-go usage tracking

### GitHub Contribution Heatmap
- Full-year contribution calendar fetched from your GitHub profile
- Disk cache + 3-retry logic for reliability
- Auto-refresh based on Vibe Coding mode

### Desktop Audio Player
- Real-time media info via Windows SMTC
- Synchronized lyrics display with smooth scrolling
- Lyrics time offset adjustment
- Lyrics reload button

### Dashboard Themes
- Default dark theme
- Clean mono theme (click the red dot in the top-left corner to toggle)
- Theme state is persisted by name and synchronized across WebSocket clients
- Styles are driven by `body[data-theme]` CSS variables; to add a theme, add its backend entry in `_THEMES` and define a matching `body[data-theme="..."]` variable block

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
- `requests` - HTTP client
- `pywebview` - Native desktop window (optional)
- `winrt-*` / `uiautomation` - Windows SMTC media info and progress fallback
- `segno` / `qrcode` - QR code for login (optional)

## Usage

### 1. Login to MiMo

```bash
python mimo_usage.py

# Options:
#   1. QR Code login (scan with Xiaomi phone)
#   2. Browser Cookie (auto-read from Chrome/Edge)
#   3. Password login
#   4. Manual Cookie input
```

Or with command-line flags:

```bash
python mimo_usage.py --login browser --save   # Auto-refresh cookies
python mimo_usage.py --login qr               # QR code scan
python mimo_usage.py --json                   # JSON output
```

### 2. Start the Dashboard

```bash
python run_dashboard.py
# Open http://localhost:5000 in your browser
```

```bash
# Custom port and settings
python run_dashboard.py --port 8080 --host 0.0.0.0
python run_dashboard.py --dev   # Debug mode
```

### 3. Desktop App (optional)

```bash
python run_desktop.py
# Native window, no browser needed

python run_desktop.py --port 8080
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/data` | GET | MiMo usage data + GitHub contributions |
| `/api/health` | GET | Lightweight cached service health; does not refresh external data |
| `/api/system` | GET | System hardware info (CPU/GPU/Memory/Disk) |
| `/api/nug` | GET | Nug status |
| `/api/media` | GET | Current media info + lyrics |
| `/api/media/reload` | POST | Clear lyrics cache and refetch |
| `/api/media/set_song_id` | POST | Manually bind current song to a NetEase song ID |
| `/api/media/offset` | GET/POST | Read or update lyric offset |
| `/api/player/<action>` | POST | Media controls: `play`, `pause`, `next`, `prev`, `toggle` |
| `/api/theme` | GET/POST | Read or set the active theme by name |
| `/api/theme/next` | POST | Switch to the next theme |

POST endpoints require same-origin `Origin`/`Referer` or an `X-Dashboard-Token` header. Set `dashboard.token` in `config.json` or `DASHBOARD_TOKEN` when exposing the server beyond `127.0.0.1`.

## Configuration

Copy `config.example.json` to `config.json` and fill only local/private values you need. `config.json`, cookies, token caches, GitHub cache, lyric offset, and display theme state are intentionally git-ignored.

Environment variables or `cookies.json`:

| Key | Description |
|---|---|
| `GITHUB_USER` | GitHub username for contribution heatmap |
| `MIMO_COOKIE` | MiMo login cookie string |
| `MIMO_COOKIE_PATH` | Path to cookie file (default: `cookies.json`) |
| `DASHBOARD_TOKEN` | Optional token accepted via `X-Dashboard-Token` for protected POST APIs |

## Project Structure

```
.
├── run_dashboard.py      # Entry point: start web dashboard
├── run_desktop.py        # Entry point: start native desktop app
├── requirements.txt      # Python dependencies
├── config/               # User-editable configuration
│   ├── config.example.json   # Template (copy to config.json)
│   ├── config.json           # Private config (git-ignored)
│   ├── cookies.json          # MiMo login cookies
│   ├── display_theme.json    # Theme preference
│   └── lyric_offset.json     # Lyric timing offset
├── src/
│   ├── dashboard.py      # Flask app, routes, and WebSocket orchestration
│   ├── desktop.py        # PyWebView native window wrapper
│   ├── mimo_usage.py     # MiMo login & CLI tool
│   ├── smtc_worker.py    # Windows SMTC media info worker
│   ├── services/
│   │   ├── config.py         # Config + path constants (CONFIG_DIR, DATA_DIR)
│   │   ├── github_service.py # GitHub heatmap fetch/cache
│   │   ├── health_service.py # Service health aggregation
│   │   ├── local_platform_service.py # Local platform clients
│   │   ├── media_service.py  # SMTC media state and Netease lyrics
│   │   ├── mimo_service.py   # MiMo API and data aggregation
│   │   ├── nug_service.py    # NUG balance API client
│   │   ├── player_service.py # Windows SMTC playback controls
│   │   ├── system_service.py # System hardware and runtime metrics
│   │   └── theme.py          # Theme metadata and persistence
│   ├── static/
│   │   ├── dashboard.html
│   │   ├── dashboard.css
│   │   └── dashboard.js
│   └── tests/
│       └── test_lyrics.py
├── data/                     # Auto-generated caches (git-ignored)
│   ├── github_cache.json
│   └── local_tokens.json
└── venv/
```

## Security

- `config/` stores all user secrets and is git-ignored (except the example template).
- `data/` stores auto-generated caches and is fully git-ignored.
- `config/config.example.json` contains structure only; never put real credentials in it.
- Protected POST endpoints reject cross-site requests unless they are same-origin or include `X-Dashboard-Token`.

## Acknowledgments

Login flow inspired by [0xtbug/Mimo-Usage](https://github.com/0xtbug/Mimo-Usage) and [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).

## License

MIT
