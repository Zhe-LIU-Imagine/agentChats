#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备身份 = 可持久化、可配置的稳定 ID（不再依赖网卡 MAC）。

为什么不用网卡 MAC：
- 笔记本/台式机网卡切换（WiFi ↔ 有线、en0 ↔ en1）会导致 uuid.getnode() 取到的
  MAC 变化 → 身份漂移、聊天记录断裂。故改为「首次自动生成 + 持久化 + 可手动改」。

ID 规则：`<系统名>_<随机3位数>`，系统名自动检测（Darwin→mac、Windows→win、Linux→linux）。
- 首次运行自动生成并写入 `data/device_id`，之后每次读同一个文件，身份稳定。
- 用户可手动改 `data/device_id`，或在 GUI 设置里改（set_device_id）。
- 复制目录到另一台机器：若复制了 data/，两机同 ID → 由 agent 的「同名冲突告警」兜底。
"""

import os
import platform
import random

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(_BASE_DIR, "data")

# ID 长度上限（防超长破坏日志/在线表可读性）
MAX_ID_LEN = 32


def _system_prefix() -> str:
    """系统名前缀：Darwin→mac、Windows→win、Linux→linux，其它退回原小写名。"""
    s = platform.system().lower()
    return {"darwin": "mac", "windows": "win", "linux": "linux"}.get(s, s or "sys")


def _is_valid_id(dev_id: str) -> bool:
    """校验 ID 合法性：非空、无空白字符、长度 ≤ MAX_ID_LEN。"""
    if not dev_id:
        return False
    if any(c.isspace() for c in dev_id):
        return False
    if len(dev_id) > MAX_ID_LEN:
        return False
    return True


def _generate_id() -> str:
    """生成默认 ID：`<系统名>_<随机3位数>`（如 mac_123、win_456、linux_789）。"""
    return f"{_system_prefix()}_{random.randint(0, 999):03d}"


def _path(data_dir: str = DEFAULT_DATA_DIR) -> str:
    return os.path.join(data_dir, "device_id")


def _save(dev_id: str, data_dir: str) -> None:
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(_path(data_dir), "w", encoding="utf-8") as f:
            f.write(dev_id)
    except Exception:
        pass  # 写失败不致命，下次仍会重新生成


def get_device_id(data_dir: str = DEFAULT_DATA_DIR) -> str:
    """返回本机稳定身份：读 data/device_id，不存在或非法则自动生成并持久化。"""
    try:
        if os.path.isfile(_path(data_dir)):
            with open(_path(data_dir), encoding="utf-8") as f:
                existing = f.read().strip()
            if _is_valid_id(existing):
                return existing
    except Exception:
        pass
    new_id = _generate_id()
    _save(new_id, data_dir)
    return new_id


def set_device_id(dev_id: str, data_dir: str = DEFAULT_DATA_DIR) -> bool:
    """用户手动设置身份（GUI 修改按钮用）。非法则返回 False 不保存。"""
    dev_id = (dev_id or "").strip()
    if not _is_valid_id(dev_id):
        return False
    _save(dev_id, data_dir)
    return True


def get_or_create_device_id(data_dir: str = DEFAULT_DATA_DIR) -> str:
    """兼容旧接口：返回本机稳定身份（同 get_device_id）。"""
    return get_device_id(data_dir)
