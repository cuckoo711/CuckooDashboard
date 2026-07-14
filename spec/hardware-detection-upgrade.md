# 硬件检测升级计划

## 目标
1. 所有硬件字段都加上手动覆盖入口
2. 内存条名字：DDR型号 + 空格 + 频率（如 "DDR5 6000"）
3. 内存总值：从 psutil 系统实际读取（不再硬编码 78.3GB）
4. GPU 显存总值：通过注册表 `qwMemorySize` 自动检测（快速、跨厂商可靠），不再依赖硬编码映射表

---

## GPU 显存检测方案（关键）

### 问题根因
`Win32_VideoController.AdapterRAM` 是 uint32 类型，最大只到 4GB，现代显卡（>4GB）会溢出返回 0 或截断值。这是微软的已知缺陷。

### 解决方案：注册表 `qwMemorySize`
显卡驱动在注册表中存储了正确的 64 位显存值：
```
HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*
→ HardwareInformation.qwMemorySize (QWORD, 单位字节)
```

匹配逻辑：通过 WMI 的 `PNPDeviceID` 前两段与注册表的 `MatchingDeviceId` 进行匹配。

来源：GLPI Agent Issue #199 — 已在生产环境验证，覆盖所有厂商 GPU。

### 分层检测策略
1. **注册表 `qwMemorySize`** — 快速、可靠、跨厂商（NVIDIA/AMD/Intel 均可）
2. **nvidia-smi** — 作为注册表方案的补充/校验
3. **手动映射表兜底** — 保留现有 `_DEFAULT_VRAM_MAP`，作为最后手段

---

## 文件变更

### 1. `config/config.example.yaml`
新增字段：`mem_name`、`gpu_model`

### 2. `src/services/system_service.py`
- 新增 `_get_mem_name_override()`、`_get_gpu_model_override()`
- 重构 `_fetch_static_hardware()`：读取 SMBIOSMemoryType 用于 DDR 型号
- 新增 `_detect_gpu_vram()`：通过注册表 `qwMemorySize` 获取显存
- 修正 `_collect_system_info()`：内存总值从 psutil 读取，增加 mem_name

### 3. `src/static/dashboard.js`
- 使用 `m.name` 显示内存标签，fallback 到 `m.type + m.freq`
