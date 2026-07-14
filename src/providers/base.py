"""providers 插件体系 — 接口规范与标准数据格式定义。

每个插件是 providers/ 下的一个子文件夹，其 __init__.py 为入口文件。
入口文件需声明 CAPABILITIES 列表，并实现对应的标准函数。

=== CAPABILITIES 可选值 ===
- "token_plan"  : Token 套餐相关（额度、用量、每日明细、按模型明细）
- "balance"     : 账户余额/费用
- "api_usage"   : API 调用量统计（按量付费、按渠道分组）

=== 各 capability 对应的入口函数签名 ===

--- token_plan ---
get_plan_detail() -> dict | None
    套餐详情。推荐格式:
    {
        "name": str,           # 套餐名称
        "expire_at": str,      # 到期时间 ISO8601
        "auto_renew": bool,    # 是否自动续费
        ...                    # 渠道自有字段
    }

get_plan_usage() -> dict | None
    套餐用量。推荐格式:
    {
        "percent": float,      # 总用量百分比
        "items": [             # 分项
            {"name": str, "used": int, "limit": int, "percent": float},
            ...
        ]
    }

get_daily_detail(year: int, month: int) -> dict | None
    每日 token 明细。推荐格式:
    {
        "tokenUsage": [
            [日期key(str), 输入(int), 输出(int), 总量(int), 缓存(int)],
            ...
        ]
    }

get_model_breakdown() -> list | None
    按模型明细列表。推荐格式:
    [
        {
            "date": str,             # "2025-01"
            "model": str,            # 模型名称
            "totalToken": int,
            "inputHitToken": int,    # 缓存命中输入
            "inputMissToken": int,   # 未缓存输入
            "outputToken": int,
            "requestCount": int,
        },
        ...
    ]

--- balance ---
get_balance() -> dict | None
    账户余额。推荐格式:
    {
        "balance": str,        # 总余额（字符串，保留精度）
        "currency": str,       # "CNY" | "USD" | ...
        "details": {           # 可选：渠道自有细分
            ...
        }
    }

--- api_usage ---
get_usage_summary() -> dict | None
    按量付费汇总。推荐格式:
    {
        "tokenUsage": int,     # 总 token 消耗
        "costUsage": str,      # 总费用
        "requestCount": int,   # 总请求数
    }

get_channel_breakdown(days: int = 7) -> list | None
    按渠道/模型分组用量。推荐格式:
    [
        {
            "channel": str,    # 渠道/模型标识
            "cost": str,       # 费用（与 tokens 二选一即可）
            "tokens": int,     # token 数（可选）
            "currency": str,   # 成本货币代码（成本数据建议提供）
            "requests": int,   # 请求数
        },
        ...
    ]

=== Vibe Coding 卡片来源选择 ===
Dashboard 的 ``dashboard.vibe_coding`` 仅使用 Provider 注册名和 capability，
不依赖任何内置 Provider 名称：
- 环形图从 ``token_plan.get_plan_usage()`` 的 ``monthUsage.items`` 读取单个套餐项；
  标准 Provider 也可直接返回 ``{items, percent}``。
- 模型条优先消费 ``get_model_breakdown()``（模型 Token），缺失时消费
  ``get_channel_breakdown()``；含 ``cost`` / ``totalCost`` 的行显示为成本，
  含 ``tokens`` / ``totalToken`` 的行显示为 Token，货币读取可选 ``currency`` 字段。
- 余额 footer 仅调用声明 ``balance`` capability 的 ``get_balance()``。

聚合器如已在同一刷新周期取得 Provider 数据，可通过私有快照传递：
``{provider_name: {method_name: result}}``。Vibe 层按注册名和标准方法名复用，
未命中快照时直接调用当前选中 Provider，因此新增插件无需修改 Dashboard 代码。

--- 通用（所有插件必须实现） ---
get_status() -> dict
    插件当前状态。格式:
    {
        "status": str,             # "ok" | "error" | "disabled" | "unknown"
        "ok": bool,
        "enabled": bool,
        "error": str | None,
        "last_success_at": str | None,
    }

=== 配置 Schema（可选） ===

Provider 可声明 ``CONFIG_SCHEMA``，让本地配置后台自动渲染配置表单：
```python
CONFIG_SCHEMA = {
    "config_key": "provider_name",
    "title": "显示名称",
    "description": "配置说明",
    "order": 30,
    "fields": [
        {"key": "enabled", "label": "启用", "type": "boolean", "default": False},
        {"key": "url", "label": "服务地址", "type": "url"},
        {"key": "password", "label": "密码", "type": "secret"},
    ],
}
```

支持的字段类型：``boolean``、``string``、``secret``、``url``、``integer``、
``number``、``select``、``color``、``time``、``string_list``、``object_list``、
``key_value_map``。``object_list`` 可以通过 ``identity_key`` 稳定匹配列表项，
避免列表排序/删除后敏感字段错配。

可选钩子：
- ``validate_config(config)``：执行 Provider 专属的跨字段校验。
- ``reload_config()``：配置保存后清理客户端和内存缓存。
"""
