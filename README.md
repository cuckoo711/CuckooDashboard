# Mimo_usage

查询自己的小米 MiMo Token Plan 使用情况（套餐详情、额度用量等）。

## 功能

- **多种登录方式** - 扫码登录 / 浏览器 Cookie 自动读取 / 密码登录
- **Cookie 自动刷新** - 过期后自动重新获取（浏览器/密码模式）
- **用量查询** - 查询 Token Plan 套餐详情、当月用量、总用量
- **美化输出** - 使用 rich 库在终端展示带进度条的用量报告
- **JSON 输出** - 支持 `--json` 导出结构化数据
- **桌面应用** - 原生窗口显示，无需打开浏览器

## 安装

```bash
pip install requests rich flask pywebview

# 可选: 终端显示二维码
pip install segno   # 或 pip install qrcode
```

## 使用

### 方式一: 交互式（推荐）

```bash
python mimo_usage.py
# 会提示选择登录方式:
#   1. 扫码登录（用小米手机扫码）
#   2. 浏览器 Cookie（自动从 Chrome/Edge 读取）
#   3. 密码登录
#   4. 手动输入 Cookie
```

### 方式二: 命令行参数

```bash
python mimo_usage.py --login qr         # 扫码登录
python mimo_usage.py --login browser    # 自动从浏览器读取 Cookie
python mimo_usage.py --login password   # 密码登录
python mimo_usage.py --cookie FILE      # 从文件加载 Cookie
```

### 保存 Cookie 并启用自动刷新

```bash
python mimo_usage.py --login browser --save
# 或
python mimo_usage.py --login password --save
```

`--save` 会保存登录方式，下次运行时如果 Cookie 过期:
- **browser**: 自动重新从浏览器读取
- **password**: 自动用缓存的账号密码重新登录
- **qr / manual**: 需要手动重新登录

### 其他选项

```bash
python mimo_usage.py --json       # JSON 格式输出
python mimo_usage.py --no-cache   # 忽略缓存，强制重新登录
python mimo_usage.py --help       # 查看帮助
```

## 桌面应用

原生窗口显示，无需打开浏览器：

```bash
# 先登录获取Cookie
python mimo_usage.py --login browser --save

# 启动桌面应用
python desktop.py

# 指定端口和窗口大小
python desktop.py --port 8080 --width 1200 --height 800

# 开发模式（显示控制台）
python desktop.py --dev
```

桌面应用特性：
- ✅ 原生窗口，无需浏览器
- ✅ 支持窗口缩放
- ✅ 后台自动刷新数据
- ✅ 系统托盘支持（计划中）

## 输出示例

```
╭──── 用户信息 ────╮
│ 字段       值             │
│ 用户ID     123456789      │
│ 邮箱       user@email.com │
│ 昵称       MiMoFan        │
╰─────────────────╯

╭──── Token Plan 套餐 ────╮
│ 字段          值              │
│ 套餐名称      Pro             │
│ 到期时间      2025-08-15      │
│ 自动续费      已开启           │
╰─────────────────────────────╯

当月用量 (总使用率: 25.0%)
  项目                已使用    总额度   使用率  进度
  mimo-v2.5-pro       2.50B    10.00B   25.0%  █████░░░░░░░░░░░░░░░
```

## 安全说明

- `cookies.json` 已被 `.gitignore` 忽略，不会提交到仓库
- 密码登录模式使用 `--save` 时会将密码保存到本地 `cookies.json`，仅用于自动刷新
- 如果不需要自动刷新，可以不用 `--save`，每次手动登录

## 参考

本项目的实现参考了 [0xtbug/Mimo-Usage](https://github.com/0xtbug/Mimo-Usage) 和 [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor) 的登录流程。
