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
python dashboard.py
# Open http://localhost:5000 in your browser
```

```bash
# Custom port and settings
python dashboard.py --port 8080 --host 0.0.0.0
python dashboard.py --dev   # Debug mode
```

### 3. Desktop App (optional)

```bash
python desktop.py
# Native window, no browser needed

python desktop.py --port 8080
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/data` | GET | MiMo usage data + GitHub contributions |
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
├── dashboard.py          # Flask app, routes, and WebSocket orchestration
├── desktop.py            # PyWebView native window wrapper
├── mimo_usage.py         # MiMo login & CLI tool
├── smtc_worker.py        # Windows SMTC media info worker
├── requirements.txt      # Python dependencies
├── services/
│   ├── cache.py          # Small cache primitives
│   ├── config.py         # Local private config loading
│   ├── github_service.py # GitHub heatmap fetch/cache (estimated counts)
│   ├── local_platform_service.py # Local MiMo-compatible platform clients
│   ├── media_service.py  # SMTC media state and Netease lyrics
│   ├── mimo_service.py   # MiMo API access and dashboard aggregation
│   ├── nug_service.py    # NUG balance API client
│   ├── system_service.py # System hardware and runtime metrics
│   └── theme.py          # Theme metadata and persistence
├── static/
│   └── dashboard.html    # Single-file dashboard (HTML/CSS/JS)
└── LICENSE
```

## Security

- `config.json`, `cookies.json`, `local_tokens.json`, `github_cache.json`, `display_theme.json`, and `lyric_offset.json` are in `.gitignore` and should stay local-only.
- `config.example.json` contains structure only; never copy real credentials into it.
- Protected POST endpoints reject cross-site requests unless they are same-origin or include `X-Dashboard-Token`.
- If real passwords, cookies, or tokens were ever committed to Git history, rotate those credentials.

## Acknowledgments

Login flow inspired by [0xtbug/Mimo-Usage](https://github.com/0xtbug/Mimo-Usage) and [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).

## License

MIT
