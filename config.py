#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置持久化（端口 / 广播地址 / peers），重启后自动恢复。"""

import json
import os

DEFAULT_CONFIG = {
    "port": 4568,   # GUI 查看器端口（统一 4568，避免 9000 被交易网关等占用）
    "broadcast": "255.255.255.255",
    "peers": {},  # {agent_id: [ip, port]}
    "max_rounds": 50,  # 每个话题最大对话轮次（发给 Hermes agent 生效）
}


def _path(data_dir: str = "data") -> str:
    return os.path.join(data_dir, "config.json")


def load_config(data_dir: str = "data") -> dict:
    """读取配置；不存在或损坏则返回默认值。"""
    path = _path(data_dir)
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            cfg.update(loaded)
        except Exception:
            pass  # 损坏则用默认
    # peers 的 list -> tuple
    peers = {}
    for k, v in (cfg.get("peers") or {}).items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            try:
                peers[str(k)] = (str(v[0]), int(v[1]))
            except (ValueError, TypeError):
                continue
    cfg["peers"] = peers
    return cfg


def save_config(config: dict, data_dir: str = "data"):
    """保存配置（peers 的 tuple -> list 以兼容 JSON）。"""
    os.makedirs(data_dir, exist_ok=True)
    cfg = dict(config)
    cfg["peers"] = {k: [v[0], v[1]] for k, v in (cfg.get("peers") or {}).items()}
    with open(_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
