# Cuckoo Dashboard

一个实时系统监控看板，支持通过完全插件化的 Provider 接入用量、余额、认证与账户数据，并集成 GitHub 贡献热力图和桌面音频播放器歌词。

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

### Provider 数据系统
- Provider 自动发现、能力声明与动态配置面板
- 标准化今日 Token 用量聚合（输入 / 输出 / 缓存 / 非缓存输入）
- Vibe 环形图、模型条和余额 Footer 可从任意兼容 Provider 选择
- Provider 自己拥有认证账户、外部 API 格式和刷新策略
- 单个 Provider 故障不会阻断其它 Provider 或系统监控

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

### Music Stage (`/music`)
- Full-screen lyric stage that reuses the existing media WebSocket feed
- Optional system-audio spectrum via true WASAPI loopback (`soundcard`) with Stereo Mix fallback
- Capture device can be chosen manually in `/settings` → **Music / 频谱采集**
- SMTC cover art + client-side palette extraction for ambient color grading
- Visual offset knobs: `music.spectrum_offset_ms` and `music.beat_lead_ms`
- One-tap beat calibration (`/api/music/calibrate`) that writes `beat_lead_ms`
- Spectrum capture starts only while a client subscribes (dashboard stays light)
- Dashboard player card has a direct stage entry button

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

### 1. Configure Providers

启动 Dashboard 后访问本机 `/settings`。Provider 由目录自动发现；每个 Provider 在自己的
`/auth/<provider>/` 页面中管理账户、登录、连通性测试、刷新和登出，核心不会要求特定 Provider 存在。

MiMo 插件保留独立 CLI，但实现已位于插件包内：

```bash
PYTHONPATH=src python -m providers.mimo --login qr --save
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

#### 配置后台

启动 Dashboard 后直接访问 `http://127.0.0.1:5000/settings`，即可在网页中修改 `config/config.yaml` 中的主要配置，无需手动编辑 YAML。配置页面只接受 `127.0.0.1` / `::1` 回环请求；即使服务使用 `--host 0.0.0.0`，局域网地址也不能访问该页面或配置 API。

密码、Token 默认以掩码显示，点击“查看”才会读取明文；留空默认保持原值，使用“清空”按钮才会删除敏感配置。保存后会清理相关缓存并立即应用，大多数配置无需重启服务。

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
| `/music` | GET | Full-screen music stage (lyrics + optional loopback spectrum) |
| `/ws` | WebSocket | Bidirectional: server pushes data, client sends vibe/init/spectrum subscribe |
| `/api/data` | GET | Aggregated daily usage, configurable Vibe card payload, and GitHub contributions |
| `/api/health` | GET | Lightweight cached service health; does not refresh external data |
| `/api/system` | GET | System hardware info (CPU/GPU/Memory/Disk/Network) |
| `/api/providers` | GET | Dynamically discovered Provider metadata, capabilities, and health |
| `/api/providers/<provider>/status` | GET | Generic Provider health/status resource |
| `/api/providers/<provider>/today` | GET | Standardized Provider daily-usage resource |
| `/api/providers/<provider>/balance` | GET | Generic Provider balance resource when supported |
| `/api/providers/<provider>/usage` | GET | Generic API-usage resource when supported |
| `/api/providers/<provider>/channels?days=7` | GET | Generic channel/model breakdown resource when supported |
| `/api/providers/<provider>/custom/...` | Provider-defined | Optional Provider-owned public resources, isolated from standard resources |
| `/api/media` | GET | Current media info + lyrics |
| `/api/media/cover` | GET | Current track cover art (SMTC thumbnail) |
| `/api/media/reload` | POST | Clear lyrics cache and refetch |
| `/api/media/offset` | GET/POST | Read or update lyric offset (supports delta or absolute) |
| `/api/music/offset` | GET/POST | Spectrum / beat visual offsets (`spectrum_offset_ms`, `beat_lead_ms`) |
| `/api/music/spectrum` | GET | Latest spectrum frame (REST fallback; WS is preferred) |
| `/api/music/spectrum/status` | GET | Loopback stack status and subscriber count |
| `/api/music/calibrate` | GET/POST | Beat tap calibration (`start` / `tap` / `apply` / `cancel`) |
| `/api/player/<action>` | POST | Media controls: `play`, `pause`, `next`, `prev`, `toggle` |
| `/api/vibe` | GET/POST | Read or set Vibe Coding mode |
| `/api/theme` | GET/POST | Read or set the active theme by name |
| `/api/theme/next` | POST | Switch to the next theme |
| `/api/off-peak-badge` | GET | Off-peak time range configuration for badge display |
| `/settings` | GET | Local-only configuration management page |
| `/api/settings` | GET/POST | Read sanitized configuration or save validated configuration (local-only) |
| `/api/settings/reveal` | POST | Reveal one explicitly requested secret field (local-only) |
| `/auth/<provider>/` | GET | Provider-owned local authentication/account page |
| `/auth/<provider>/api/...` | GET/POST | Provider-owned authentication lifecycle APIs in a protected namespace |

POST endpoints require same-origin `Origin`/`Referer` or an `X-Dashboard-Token` header. The `/settings` page, `/api/settings*`, and Provider authentication namespaces additionally require a loopback client address (`127.0.0.1` or `::1`). Set the Dashboard token through `/settings` (it is stored in the DPAPI Vault) or use `DASHBOARD_TOKEN` when exposing other Dashboard APIs beyond `127.0.0.1`.

## Configuration

Copy `config/config.example.yaml` to `config/config.yaml` and fill only non-sensitive settings. Configuration, the encrypted Vault, and runtime state are git-ignored.

### Credential Vault

All persistent authentication material is stored only in `config/credentials.vault`. On Windows it is encrypted with the current user's DPAPI context, so the file cannot be decrypted by a different Windows user or copied to another machine for use there. Do not manually edit, commit, back up for cross-machine reuse, or share this file.

The Settings page writes global Dashboard and GitHub tokens to the Vault. Each Provider owns its own account record structure and authentication screen under `/auth/<provider>/`; the framework only supplies encrypted storage, active-account state, refresh scheduling, and protected routes.

启动入口不会执行配置或凭据迁移。运行时只接受 schema v4 YAML 和 DPAPI Vault；旧版 `config.yaml`、Cookie/JWT 文件或明文凭据既不会被读取，也不会被删除。需要切换的安装应先以 `config/config.example.yaml` 重建 v4 配置，再通过本机 Settings 与 Provider 认证页重新配置账户。

### Config Sections (`config/config.yaml`)

| Section | Description |
|---|---|
| `config_version` | 通用配置 schema 版本（当前为 `4`） |
| `dashboard.off_peak_badge` | Off-peak time range and enable/disable |
| `dashboard.vibe_coding` | Vibe ring, model-bar, and balance data-source selection |
| `providers.<provider_id>` | 任意已发现 Provider 的非敏感运行时配置；字段由 Provider Schema 定义 |
| `hardware_overrides` | Manual corrections for CPU/GPU/memory detection |
| `theme` | Active theme name (auto-saved) |
| `lyric_offset` | Lyric timing offset in seconds (auto-saved) |
| `vibe_active` | Vibe Coding mode state (auto-saved) |

### Provider Configuration

核心不会预置、导入或解释任何 Provider。插件以目录名作为稳定 `provider_id`，其 Registry ID、YAML namespace 和 Vault namespace 必须一致。插件可在 `src/providers/<provider_id>/__init__.py` 声明 `CONFIG_SCHEMA`、`CAPABILITIES`、认证 hook 和公开数据资源；设置页会自动发现并渲染非敏感配置。

Provider 的账户结构、密码、Cookie、JWT、Token 和外部 API 格式全部归 Provider 自己所有。核心只提供 DPAPI Vault、刷新调度、路由容器、统一今日用量聚合以及按能力调用。

最小配置示例：

```yaml
config_version: 4
providers: {}
```

安装/发现 Provider 后，Settings 页面会按其 Schema 写入相应的 `providers.<provider_id>` 项。

### Vibe Coding Data Sources

```yaml
dashboard:
  vibe_coding:
    ring:
      provider: "token-plan-provider-id"  # optional: discovered Provider id
      item: "plan-code-or-name"            # optional: id / planCode / name
    model_bars:
      provider: "usage-provider-id"        # optional: discovered Provider id
    balances:
      - provider: "balance-provider-a"
        name: "Service A"
        color: "#d1a15c"
        enabled: true
      - provider: "balance-provider-b"
        name: "Service B"
        color: "#5fa89e"
        enabled: true
```

- Provider IDs are discovered from registered `src/providers/` plugins; adding a compatible plugin requires no Dashboard source-name changes.
- The ring renders one Token Plan item only. When `provider` or `item` is missing or invalid, eligible Providers and plan items are sorted deterministically and the first usable item is selected.
- `model_bars.provider` chooses the source of model/channel data. Without a value, available Providers are tried in stable name order.
- `balances` is opt-in: omit it or set `[]` to hide the footer. Disabled, duplicate, unsupported, or unknown entries are ignored; valid entries are sorted by Provider/name and only the first two are rendered.
- Balance colors must use `#RRGGBB`. Each rendered value follows `Name · colored dot · currency+balance`; common currency codes are shown with symbols.

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
├── config/                   # User configuration and secrets (git-ignored except example)
│   ├── config.example.yaml       # Provider-agnostic schema-v4 template
│   ├── config.yaml               # Private non-secret configuration
│   └── credentials.vault         # DPAPI-encrypted credential Vault (never edit or share)
├── src/
│   ├── dashboard.py          # Flask app, generic Provider routes and background tasks
│   ├── desktop.py            # PyWebView native window wrapper with monitor detection
│   ├── smtc_worker.py        # Standalone subprocess: SMTC + UIA + YesPlayMusic listener
│   ├── core/                 # Core infrastructure
│   │   ├── config.py             # Provider-agnostic YAML load/save and schema-v4 storage
│   │   ├── credentials.py        # DPAPI Vault, account state, revisions and locking
│   │   ├── cache.py              # TTLCache utility
│   │   ├── proc.py               # PowerShell/subprocess execution (hidden window)
│   │   ├── perfcounters.py       # PDH/WMI performance counter sampling
│   │   └── monitor.py            # Windows display enumeration
│   ├── providers/            # Fully decoupled plugin system
│   │   ├── __init__.py           # Auto-discovery, Registry, schemas and capability calls
│   │   ├── runtime_config.py     # Provider-side YAML defaults + Vault-secret resolution
│   │   ├── auth.py               # AuthResult, refresh decorator, scheduler
│   │   ├── auth_routes.py        # Generic auth/public Provider route containers
│   │   ├── base.py               # Provider capability contracts
│   │   ├── mimo/                 # MiMo Provider implementation
│   │   │   ├── __main__.py           # `python -m providers.mimo` CLI entry
│   │   │   ├── implementation.py     # Provider-owned QR/browser/password CLI implementation
│   │   │   └── ...
│   │   ├── nug/                  # NUG Provider implementation
│   │   │   └── ...
│   │   └── local_platform/       # Local-platform Provider implementation
│   │       └── ...
│   ├── services/             # Business logic services
│   │   ├── github_service.py     # GitHub heatmap fetch/cache (GraphQL + scraping)
│   │   ├── media_service.py      # SMTC media state, Netease + QQ Music lyrics
│   │   ├── system_service.py     # System hardware and runtime metrics
│   │   ├── player_service.py     # Windows SMTC playback controls
│   │   ├── off_peak_service.py   # Off-peak time range badge config
│   │   ├── health_service.py     # Service health aggregation
│   │   ├── dashboard_data_service.py # Capability-based daily-usage aggregation
│   │   ├── theme.py              # Theme metadata and persistence
│   │   └── config.py             # Provider-agnostic config storage exports
│   ├── static/
│   │   ├── dashboard.html
│   │   ├── dashboard.css
│   │   ├── dashboard.js
│   │   └── bg/                   # Theme background images
│   └── tests/
│       ├── test_credentials_vault.py
│       ├── test_config_storage.py
│       ├── test_auth_lifecycle.py
│       ├── test_auth_routes.py
│       └── ...
├── data/                         # Auto-generated non-secret caches/runtime files (git-ignored)
│   ├── github_cache.json
│   └── monitor.json              # Target display config for desktop mode
└── venv/
```

## Architecture

### WebSocket Real-Time Push

The dashboard uses a single WebSocket connection (`/ws`) for all real-time data:

1. On connect: server asynchronously pushes state plus `dashboard_data`, github, media, system, and theme payloads
2. Background broadcaster thread (1s interval): parallel fetch of system + media + github, broadcast to all clients
3. Selected Vibe Providers refresh at dynamic intervals based on Vibe Coding mode (20s coding / 60s chilling)
4. Client can send `{"type": "vibe", "active": true/false}` to toggle mode or `{"type": "init"}` to request full refresh

### Provider Plugin System

Providers are auto-discovered from `src/providers/*/` directories. The host never imports or identifies a concrete Provider. Each plugin declares `CAPABILITIES` and implements standardized functions:

- **daily_usage**: `get_today_usage()` → normalized input/output/cache/total values for cross-Provider aggregation
- **token_plan**: `get_plan_detail()`, `get_plan_usage()`, `get_daily_detail()`, `get_model_breakdown()`
- **balance**: `get_balance()`
- **api_usage**: `get_usage_summary()`, `get_channel_breakdown()`
- **All plugins**: `get_status()` for health reporting
- **Authentication-capable plugins**: account lifecycle hooks such as `get_auth_status()`, `test_connection()`, `refresh_credentials()`, `logout()`, and `register_auth_routes()`

Provider authentication is exposed only within the local, CSRF-protected `/auth/<provider>/` namespace. Generic public Provider resources live under `/api/providers/<provider>/...`. Providers define their own account structures, external APIs, and credential payloads; the shared Vault never imposes a password, cookie, JWT, or token schema. `@auto_refresh` can refresh credentials both before provider requests and on a background schedule.

### Performance Counter Sampling

GPU and CPU metrics use a tiered approach:
1. **Primary**: Native PDH counters + WMI (via pywin32) — low overhead, per-thread sampler instances
2. **Fallback**: PowerShell `Get-Counter` / `Get-CimInstance` — higher latency but always available

GPU LUID mapping stabilizes across refresh cycles to prevent card assignment flicker.

## Security

- `config/credentials.vault` is the only persistent credential store. It is encrypted with Windows DPAPI, bound to the Windows user profile, and git-ignored together with its lock file.
- `config/config.yaml` and `config/config.example.yaml` contain only non-sensitive configuration. Never add passwords, cookies, access tokens, JWTs, or client secrets to either file.
- `data/` contains only non-secret auto-generated caches/runtime state and is fully git-ignored.
- Provider authentication and Settings POST endpoints are loopback-only and reject cross-site requests unless they are same-origin or include `X-Dashboard-Token`.
- Vault writes use a lock and a monotonic revision to prevent concurrent Settings/authentication updates from overwriting each other.
- Only schema-v4 configuration and the Vault are supported; legacy configuration and plaintext credential files are never read, migrated, or deleted automatically.
- Internal network HTTPS with self-signed certs is auto-detected and verification skipped for local platform clients.

## Acknowledgments

Login flow inspired by [0xtbug/Mimo-Usage](https://github.com/0xtbug/Mimo-Usage) and [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).

## License

MIT
