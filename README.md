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

### Workspace Module Host
- `main` and user-created workspaces are persisted in `data/workspaces.db` as revisioned Manifest v2 documents
- Every workspace uses a fixed 16 × 15 content grid; Settings can add/remove, drag, resize, duplicate, rename, and delete workspaces
- System info, network, uptime, disks, Vibe, player, and GitHub are single-instance widget types owned by the locked `cuckoo.core.dashboard` package
- The four system widgets share one `system.snapshot`; every mounted card uses the host Widget SDK (`mount/update/destroy` plus `context.subscribe`) and the browser sends only its active card-level subscriptions
- `/` remains the `main` alias, while custom workspaces have stable `/workspaces/<workspace_id>` URLs
- Revision compare-and-swap prevents two Settings tabs from silently overwriting each other's layout changes
- Widget rows persist their canonical extension owner; missing/disabled extensions produce removable placeholder cards instead of breaking the workspace

### Trusted Local Extension Host
- Strict Manifest v1 discovery scans bundled `src/extensions/` and administrator-managed `data/extensions/` directories without importing disabled code
- Non-core extensions are disabled by default; Settings persists `desired_enabled` in `data/extensions.db`, while `effective_enabled` changes only after a Dashboard restart
- Enabled backends can contribute validated Data Sources and Widget Definitions plus idempotent `start/stop` lifecycle hooks
- Dashboard loads frontend modules only from the host-generated, same-origin Runtime Catalog; Workspace manifests never provide executable URLs or arbitrary HTML
- Duplicate IDs, path escapes, API incompatibility, dependency cycles, undeclared contributions, and partial registrations are isolated per extension
- `com.cuckoo.runtime-health` is a bundled, default-disabled example that displays the existing service health snapshot
- This is a trusted-code system, not a sandbox: enabled Python/JavaScript has the same local permissions as Dashboard; online install, signatures, and hot unloading are not implemented

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

同一页面的“工作区与布局”面板独立管理 `data/workspaces.db`：可新建空白工作区、复制现有工作区、添加或移除当前可用卡片，并在 16 × 15 预览网格中拖动和缩放。工作区保存与 YAML 配置保存互不混用；若另一标签页已先保存，旧 revision 会收到 `409`，本地草稿会保留以便人工处理。

“扩展管理”面板独立管理 `data/extensions.db`。复制到 `data/extensions/<extension_id>/` 的包首次发现时默认禁用；开关只修改 desired state，必须重启 Dashboard 才会 import/start 或停用。扩展仍被工作区引用、被其它已启用扩展依赖、Manifest 无效或 ID 冲突时，状态修改会被拒绝并显示原因。仅启用你完全信任的本地代码。

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
| `/` | GET | `main` workspace Dashboard page |
| `/workspaces/<workspace_id>` | GET | Dashboard shell for an existing persisted workspace |
| `/music` | GET | Full-screen music stage (lyrics + optional loopback spectrum) |
| `/ws` | WebSocket | Card-level source subscriptions and `data.snapshot`, with legacy source/channel envelopes, Vibe, updates, navigation and screenshots preserved |
| `/api/data` | GET | Aggregated daily usage, configurable Vibe card payload, and GitHub contributions |
| `/api/health` | GET | Lightweight cached service health; does not refresh external data |
| `/api/system` | GET | System hardware info (CPU/GPU/Memory/Disk/Network) |
| `/api/workspaces` | GET | Public workspace summaries for navigation |
| `/api/workspaces/<workspace_id>` | GET | Manifest v2 with revision, owner/availability, 16 × 15 layouts, constraints, sources, and channels |
| `/api/runtime/extensions` | GET | Active extension frontend catalog; contains only host-generated same-origin module URLs |
| `/runtime/extensions/<extension_id>/assets/<path>` | GET | Frontend assets for an active extension, contained inside its validated `frontend/` directory |
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
| `/settings` | GET | Local-only configuration and workspace management page |
| `/api/settings` | GET/POST | Read sanitized configuration or save validated YAML configuration (local-only) |
| `/api/settings/workspaces` | GET/POST | List workspaces/catalog or create an empty workspace (local-only) |
| `/api/settings/workspaces/<workspace_id>` | GET/PUT/DELETE | Read, revision-save, or delete a workspace (local-only) |
| `/api/settings/workspaces/<workspace_id>/duplicate` | POST | Duplicate a workspace with a new stable ID (local-only) |
| `/api/settings/extensions` | GET | List core/discovered/missing extensions with desired/effective/restart state (local-only) |
| `/api/settings/extensions/<extension_id>` | PUT | Revision-CAS update of desired enablement; restart required (local-only) |
| `/api/settings/extensions/rescan` | POST | Re-read local Manifests without importing or starting code (local-only) |
| `/api/settings/clients/<client_id>/navigate` | POST | Navigate one online client to Music, `main`, or a custom workspace (local-only) |
| `/api/settings/reveal` | POST | Reveal one explicitly requested secret field (local-only) |
| `/auth/<provider>/` | GET | Provider-owned local authentication/account page |
| `/auth/<provider>/api/...` | GET/POST | Provider-owned authentication lifecycle APIs in a protected namespace |

State-changing endpoints require same-origin `Origin`/`Referer` or an `X-Dashboard-Token` header. The `/settings` page, `/api/settings*`, and Provider authentication namespaces additionally require a loopback client address (`127.0.0.1` or `::1`; workspace CRUD protects POST/PUT/PATCH/DELETE). Set the Dashboard token through `/settings` (it is stored in the DPAPI Vault) or use `DASHBOARD_TOKEN` when exposing other Dashboard APIs beyond `127.0.0.1`.

## Configuration

Copy `config/config.example.yaml` to `config/config.yaml` and fill only non-sensitive settings. Configuration, the encrypted Vault, and runtime state are git-ignored.

### Workspace Database

Editable workspaces are stored separately in `data/workspaces.db` using SQLite schema version 2. The `workspaces` table stores identity, name, kind, required flag, Manifest contract version, grid size and revision; `workspace_widgets` stores ordered card placements, canonical resize constraints, and the authoritative `owner_id`. Schema v1 rows migrate to the locked core owner because only core widgets existed before owner persistence. The required `main` row is seeded only when absent, so restarts and upgrades do not overwrite a saved layout.

Workspace updates replace the parent metadata and all card rows in one `BEGIN IMMEDIATE` transaction. `PUT` and `DELETE` requests include the current revision; a missing delete revision is rejected, while a stale revision returns `409 workspace_conflict` and leaves the database unchanged. If an extension disappears, its rows remain readable as unavailable placeholders and can be moved or removed without inventing a replacement definition.

### Extension State Database

`data/extensions.db` stores a global revision and one desired enablement row per extension. Runtime activation is a startup snapshot: `desired_enabled != effective_enabled` means a process restart is required. Reads do not create a missing database; the first state mutation creates it. The state database contains no extension code, secrets, or configuration values.

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
| `DASHBOARD_TOKEN` | Optional token accepted via `X-Dashboard-Token` for protected state-changing APIs |

## Project Structure

```
.
├── run_dashboard.py              # Web launcher
├── run_desktop.py                # Native PyWebView launcher
├── requirements.txt
├── config/
│   ├── config.example.yaml       # Provider-agnostic schema-v4 template
│   ├── config.yaml               # Private non-secret configuration
│   └── credentials.vault         # DPAPI-encrypted Vault; never edit or share
├── src/
│   ├── dashboard.py              # Compatibility `app` export and web CLI only
│   ├── desktop.py                # PyWebView + managed Werkzeug server lifecycle
│   ├── smtc_worker.py            # Standalone SMTC/UIA media subprocess
│   ├── app/
│   │   ├── factory.py            # Side-effect-free `create_app()` composition root
│   │   └── security.py           # Loopback, same-origin/token and cache guards
│   ├── features/                 # Feature-oriented HTTP and application boundaries
│   │   ├── dashboard/            # Main page, aggregate payload and Vibe APIs
│   │   ├── settings/             # Routes, Schema, persistence, runtime refresh, service
│   │   ├── media/                # Media/cover/player routes
│   │   ├── music/                # Music stage, spectrum and calibration routes
│   │   ├── system/               # System-monitoring routes
│   │   ├── workspaces/           # Public workspace-manifest routes
│   │   ├── extensions/           # Runtime catalog and contained frontend assets
│   │   ├── providers/            # Generic and Provider-owned route registration
│   │   └── appearance/           # Theme/font routes and payload service
│   ├── runtime/
│   │   ├── lifecycle.py          # Ordered start/stop for extensions, sources and transports
│   │   ├── websocket.py          # Legacy-compatible protocol facade + lyric/spectrum channels
│   │   ├── websocket_transport.py # Pure socket/session transport
│   │   ├── client_session.py     # Per-connection state and serialized sends
│   │   ├── subscription_broker.py # Subscription indexes, delivery cadence and wire adapters
│   │   ├── refresh_scheduler.py  # Demand-aware single-flight source sampling/backoff
│   │   └── source_cache.py       # Process-local fresh/stale source snapshots
│   ├── contracts/                # Standard-library dataclass/TypedDict wire contracts
│   │   ├── provider.py
│   │   ├── dashboard.py
│   │   ├── health.py
│   │   ├── settings.py
│   │   ├── extension.py          # Manifest, contributions and lifecycle contracts
│   │   ├── schemas/              # Shared JSON Schema documents
│   │   └── workspace.py          # Data-source, widget type/instance and workspace contracts
│   ├── extensions/               # Discovery, desired-state DB, loader, manager and bundled packages
│   ├── workspaces/               # Registry, built-ins, SQLite repository and workspace service
│   │   ├── registry.py
│   │   ├── data_sources.py
│   │   ├── builtins.py
│   │   ├── repository.py
│   │   └── service.py
│   ├── core/                     # Config, Vault, caching, subprocess and Windows infra
│   ├── providers/                # Auto-discovered, capability-based plugins
│   │   ├── __init__.py           # Registry, typed invocation and Schema discovery
│   │   ├── auth.py               # Credential refresh decorator and managed scheduler
│   │   ├── auth_routes.py        # Restricted Provider route containers
│   │   ├── base.py               # Plugin contract documentation/re-exports
│   │   ├── runtime_config.py
│   │   ├── mimo/
│   │   ├── nug/
│   │   └── nfk/
│   ├── services/                 # Domain collectors and external integrations
│   │   ├── dashboard_data_service.py
│   │   ├── health_service.py
│   │   ├── github_service.py
│   │   ├── media_service.py
│   │   ├── spectrum_service.py
│   │   ├── system_service.py
│   │   └── ...
│   ├── static/
│   │   ├── dashboard.html/css/js # Root JS is a compatibility module entry
│   │   ├── music.html/css/js
│   │   ├── settings.html/css/js
│   │   ├── modules/
│   │   │   ├── shared/           # Fetch, player, font, WS and screenshot helpers
│   │   │   ├── dashboard/        # Dashboard shell, DataBus and workspace host
│   │   │   │   └── workspace/    # Static registry plus built-in widget components
│   │   │   └── music/            # Music-stage native ES Modules
│   │   └── settings/modules/     # Loopback-only Settings ES Modules
│   └── tests/                    # Unit, contract, architecture, route and module tests
└── data/                         # Non-secret generated caches/runtime files
```

## Architecture

### App Factory and Feature Boundaries

`app.factory.create_app()` is the composition root. It creates Flask/Sock, installs security hooks, registers feature Blueprints, mounts every discovered Provider route for that specific app, and stores a fresh `DashboardRuntime` in `app.extensions`. The factory does **not** start threads or subprocesses, so tests can safely create multiple isolated apps.

`dashboard.py` remains a compatibility facade (`dashboard.app`) and CLI. HTTP handlers live under `features/*/routes.py`; routes adapt HTTP only, while payload composition, Settings Schema interpretation, Vault/YAML persistence and runtime refresh are separated into feature services.

### Managed Runtime Lifecycle

`DashboardRuntime` owns the ExtensionManager lifecycle, WebSocket broadcasters and Provider credential-refresh scheduler. Startup and shutdown are idempotent: enabled extensions start in dependency order before WebSocket workers, while shutdown stops WebSocket delivery first and then extensions in reverse dependency order. Shutdown also releases spectrum subscriptions, stops executors/schedulers, terminates the SMTC worker, and joins system/media/spectrum workers.

System and media collectors retain their existing lazy-start behavior: they start only when first consumed, but now expose explicit shutdown hooks and can restart after a clean stop.

### Typed Contracts

Stable cross-module structures are defined with standard-library dataclasses, Protocols and TypedDicts in `src/contracts/`:

- Provider status, daily usage and typed call outcomes
- Dashboard totals, usage sources and the internal aggregate/snapshot boundary
- Normalized service health
- Stable Settings request/response keys while Provider-specific values stay dynamic

Provider functions continue returning their compatible dict/list payloads. Consumers normalize internally and serialize back to the existing API wire format, including `today.in/out/cache/total/inMiss` and all current Vibe/Settings keys.

### Workspace Registry, Persistence, and Built-in Widgets

Each `DashboardRuntime` owns an isolated `WorkspaceRegistry`, `WorkspaceRepository`, and `WorkspaceService`; App Factory exposes the same instances through `app.extensions`. The Registry owns immutable data-source/widget capabilities and the built-in `main` seed. The SQLite Repository owns user state and atomic revision updates. The Service validates every 16 × 15 placement, rejects overlap/out-of-bounds/constraint changes, and enriches persisted rows into public Manifest v2 payloads.

The built-in widget set is `builtin.dashboard.system-info`, `builtin.dashboard.network`, `builtin.dashboard.uptime`, `builtin.dashboard.disks`, `builtin.dashboard.vibe`, `builtin.dashboard.player`, and `builtin.dashboard.github`. All are single-instance within one workspace. System widgets intentionally share `system.snapshot`; Vibe owns `dashboard.aggregate`; the player declares `media.playback` plus the optional `media.lyric` channel. Removing a card removes its otherwise-unused data-source/channel subscription.

Every snapshot Data Source now exposes an additive Manifest v2 `refresh_policy` (`default/minimum/active` intervals, cache TTL, push capability, pause-without-subscribers and retry/stale limits). The old second-based interval fields remain serialized for Extension API v1 compatibility and must agree with the policy.

The Dashboard host renders the Header outside the workspace coordinate system and mounts cards inside a nested 16 × 15 CSS Grid using Manifest `layout` values. Settings uses the same server-authoritative constraints in a Pointer Events editor with collision rejection and first-fit placement. The browser first registers the static core package, then imports active extension modules from the host-generated Runtime Catalog. Owner-scoped transactions enforce the declared widget allowlist; persisted manifests still cannot inject executable URLs or arbitrary HTML.

### Native ES Modules

Dashboard, Music and Settings use browser-native ES Modules without a bundler or framework. Shared stateless helpers live in `static/modules/shared`; page state and controllers live in page-specific modules. HTML inline handlers were removed in favor of module-bound `data-action` events. Settings modules are served only through the loopback-protected `/settings-assets/modules/...` namespace.

### WebSocket Real-Time Push

The dashboard uses one WebSocket connection (`/ws`), but ordinary Data Source work is split into explicit layers:

1. `WebSocketTransport` owns sockets, JSON framing, per-session send locks and idempotent disconnect cleanup; it has no Workspace or getter knowledge
2. The Widget SDK gives every card `mount/update/destroy` and a constrained `context.subscribe`; the browser sends stable card subscriptions as `{type: "subscribe", subscriptions: [...]}`
3. `SubscriptionBroker` keeps session/source indexes, derives reference counts, replays cache, applies each subscription's delivery interval and emits canonical `{type: "data.snapshot", subscriptionId, channel, ...}` messages
4. `RefreshScheduler` clamps requested cadence to each Data Source refresh policy, performs one single-flight getter call per source, caches the result, applies stale/error backoff, and pauses sources with no demand
5. Existing `subscribe.sources`, `unsubscribe.sources`, `init`, `dashboard_data`, `github`, `media`, `system`, and extension `workspace_source` envelopes remain available to legacy clients
6. Product Dashboard clients no longer activate every source on connection; `/music` explicitly requests only `media.playback`
7. Spectrum remains a dedicated 12–60 FPS acquire/release path, while lyrics remain a 120ms change-only channel; neither is forced through the ordinary snapshot scheduler
8. Dashboard reports still include `workspace_id`, workspace updates still trigger Manifest reconciliation, and Vibe/navigation/theme/font/ping/screenshot behavior is unchanged

Multiple cards or clients consuming the same source share one sample/cache entry while retaining independent delivery cadence. Dashboard media remains slim while the Music Stage receives the full media payload, and all disconnect paths release subscriptions and spectrum references exactly once.

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

## Development and Tests

```bash
# Full Python regression suite
python -m pytest -q

# Syntax-check the three browser module entry graphs
node --check src/static/modules/dashboard/main.js
node --check src/static/modules/music/main.js
node --check src/static/settings/modules/main.js

# Pure 16 × 15 placement/collision tests
node --test src/tests/grid-layout.test.mjs
```

The test suite covers App Factory/Registry/SQLite isolation, workspace seed/CRUD/CAS rollback, the complete route and loopback security surface, ordered runtime shutdown/restart, scheduler single-flight/cache/backoff, broker reference counting and legacy/canonical WebSocket compatibility, transport send serialization, Widget SDK subscription cleanup, Manifest v2 fallback parity, AST dependency boundaries, ES Module loading, and pure grid placement/collision rules.

## Security

- `config/credentials.vault` is the only persistent credential store. It is encrypted with Windows DPAPI, bound to the Windows user profile, and git-ignored together with its lock file.
- `config/config.yaml` and `config/config.example.yaml` contain only non-sensitive configuration. Never add passwords, cookies, access tokens, JWTs, or client secrets to either file.
- `data/` contains only non-secret auto-generated caches/runtime state, including `workspaces.db`, and is fully git-ignored.
- Provider authentication and Settings state-changing endpoints are loopback-only and reject cross-site requests unless they are same-origin or include `X-Dashboard-Token`.
- Vault and workspace writes use independent revisions/locks so concurrent Settings, authentication, and layout updates cannot silently overwrite each other.
- Only schema-v4 configuration and the Vault are supported; legacy configuration and plaintext credential files are never read, migrated, or deleted automatically.
- Internal network HTTPS with self-signed certs is auto-detected and verification skipped for local platform clients.

## Acknowledgments

Login flow inspired by [0xtbug/Mimo-Usage](https://github.com/0xtbug/Mimo-Usage) and [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor).

## License

MIT
