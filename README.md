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

## Screenshots

> Run the dashboard and open `http://localhost:5050` in your browser to see it live.

## Installation

```bash
# Clone the repository
git clone https://github.com/cuckoo711/CuckooDashboard.git
cd CuckooDashboard

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

- `flask` - Web server
- `psutil` - System monitoring
- `requests` - HTTP client
- `pywebview` - Native desktop window (optional)
- `segno` or `qrcode` - QR code for login (optional)

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
# Open http://localhost:5050 in your browser
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

python desktop.py --port 8080 --width 1200 --height 800
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/data` | GET | MiMo usage data + GitHub contributions |
| `/api/system` | GET | System hardware info (CPU/GPU/Memory/Disk) |
| `/api/nug` | GET | Nug status |
| `/api/media` | GET | Current media info + lyrics |
| `/api/media/reload` | POST | Clear lyrics cache and refetch |

## Configuration

Environment variables or `cookies.json`:

| Key | Description |
|---|---|
| `GITHUB_USER` | GitHub username for contribution heatmap |
| `MIMO_COOKIE` | MiMo login cookie string |
| `MIMO_COOKIE_PATH` | Path to cookie file (default: `cookies.json`) |

## Project Structure

```
.
├── dashboard.py          # Flask server + all API endpoints
├── desktop.py            # PyWebView native window wrapper
├── mimo_usage.py         # MiMo login & CLI tool
├── smtc_worker.py        # Windows SMTC media info worker
├── requirements.txt      # Python dependencies
├── static/
│   └── dashboard.html    # Single-file dashboard (HTML/CSS/JS)
└── LICENSE
```

## Security

- `cookies.json` is in `.gitignore` and will not be committed
- `github_cache.json` (local cache) is also git-ignored
- No sensitive data is transmitted to third-party servers

## Acknowledgments

Login flow inspired by [0xtbug/Mimo-Usage](https://github.com/0xtbug/Mimo-Usage) and [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).

## License

MIT
