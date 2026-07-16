"""Dashboard feature data composition."""

from __future__ import annotations

from services.dashboard_data_service import fetch_dashboard_aggregate
from services.vibe_data_service import get_vibe_data


def get_dashboard_data() -> dict:
    """组合既有今日用量与可配置的 Vibe 卡片数据。

    通用聚合器可携带按 Provider ID 归档的私有快照；这里将其传给 Vibe 服务
    复用后移除，避免向 API/WS 客户端泄露内部缓存结构。
    """
    aggregate = fetch_dashboard_aggregate()
    data = aggregate.to_public_payload()
    data["vibe"] = get_vibe_data(prefetched_provider_data=aggregate.snapshots)
    return data
