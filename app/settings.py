# -*- coding: utf-8 -*-
"""运行配置。

读取顺序：config.json -> 环境变量覆盖（CAPTCHA_* 前缀）。
线程安全，运行期可热重载（调用 reload()）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.json"

_LOCK = RLock()
_CACHE: dict | None = None

# ---------------------------------------------------------------------------
# 默认配置。全部字段都可被 config.json 覆盖。
# ---------------------------------------------------------------------------
DEFAULTS: dict = {
    "server": {
        "host": "0.0.0.0",
        "port": 7070,
        "log_level": "info",
    },
    "auth": {
        # 是否需要请求头 X-Api-Key。自建内网使用可保持 False。
        "require_api_key": False,
        "api_key": "",
        # 每个 IP 每日识别次数上限，0 = 不限。油猴脚本作者原服务是 50。
        "daily_limit_per_ip": 0,
        # 命中这些 host 的识别请求额外限流（与原脚本"敏感站点"策略对应）
        "sensitive_host_rate_limit": {"window_seconds": 60, "max_requests": 3},
    },
    "storage": {
        "enabled": True,
        "sqlite_path": "data/captcha.db",
        # 是否落库完整埋点 / 轨迹样本 / 人工样本
        "store_samples": True,
        "store_events": True,
    },
    "ocr": {
        # ddddocr = 内置神经网络 OCR（推荐）
        # none    = 关闭普通验证码识别
        "provider": "ddddocr",
        # 是否自动把 "3+4=?" 这类算术验证码算成结果
        "arithmetic": True,
        # 使用 beta 模型（对字母/算术更友好，略慢）
        "beta_model": False,
        # 是否允许 image 字段传远程 URL（关闭可避免 SSRF）
        "allow_remote_image_url": False,
    },
    "slider": {
        # auto = 灰度法优先，得分低于阈值自动切形状法
        # gray = 强制灰度互相关；shape = 强制拼图块轮廓匹配
        "method": "auto",
        # 灰度法得分低于该值时改用形状法（auto 模式下生效）
        "gray_score_gate": 0.5,
        # 形状法的 Canny 边缘双阈值
        "edge_low": 100,
        "edge_high": 200,
        # 高斯模糊核（奇数），仅用于形状法的 Canny 预处理。
        # 千万不要用在灰度互相关上——会把缺口纹理抹平导致选错位置。
        "blur_ksize": 5,
        # 抑制左侧初始位置的比例（x < ratio * 拼图块宽度 的匹配点被丢弃）
        # None 表示自动：有 alpha 用 1.0，无 alpha 用 0.0
        "suppress_left_ratio": None,
        # 低于该像素宽度的结果视为无效
        "min_distance": 10,
        # 统一加上/减去的像素修正量（按站点微调）
        "offset": 0,
        # display = 输出换算到背景图展示宽度；raw = 输出原图像素
        "output_mode": "display",
    },
    # 下发给油猴脚本的远程配置（GET /api/userscript/config）
    "userscript_config": {
        "config_version": "self-hosted-1",
        "features": {
            "slideEnabled": True,
        },
        "rules": {},
        "interactionNotices": {},
    },
    # 下发给油猴脚本的页面级配置（GET /api/userscript/page-config）
    "page_config": {
        "cache_ttl_seconds": 3600,
        # 服务端下发的"禁用识别图片"清单，元素形如
        # {"host":"","pathname":"","imageHash":"","reason":""}
        "normal_image_denylist": [],
    },
    # 下发给油猴脚本的滑块轨迹配置（GET /api/slide/trajectory/config）
    "trajectory_config": {
        "collect_enabled": True,
        # 是否允许服务端下发轨迹模板并驱动自动拖动
        "dispatch_enabled": False,
        "global_dispatch_enabled": False,
        "max_attempts_per_popup": 3,
        "config_version": "self-hosted-1",
        "templates": [],
        "default_template": None,
    },
    # 额度码：留空表示不启用。启用后 /captcha 与 /api/slide/solve 需要有效 code
    "quota_codes": [],
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_env(cfg: dict) -> dict:
    """支持 CAPTCHA_SERVER__PORT=8080 这类覆盖（双下划线表示层级）。"""
    prefix = "CAPTCHA_"
    for key, raw in os.environ.items():
        if not key.startswith(prefix) or "__" not in key:
            continue
        path = [p.lower() for p in key[len(prefix):].split("__") if p]
        if not path:
            continue
        node = cfg
        for seg in path[:-1]:
            node = node.setdefault(seg, {})
            if not isinstance(node, dict):
                break
        else:
            val: object = raw
            if raw.lower() in ("true", "false"):
                val = raw.lower() == "true"
            elif raw.isdigit():
                val = int(raw)
            node[path[-1]] = val
    return cfg


def config_path() -> Path:
    return Path(os.environ.get("CAPTCHA_CONFIG_FILE", str(DEFAULT_CONFIG_PATH)))


def reload() -> dict:
    global _CACHE
    with _LOCK:
        cfg = json.loads(json.dumps(DEFAULTS))  # 深拷贝
        p = config_path()
        if p.exists():
            try:
                user = json.loads(p.read_text(encoding="utf-8"))
                cfg = _deep_merge(cfg, user)
            except Exception as exc:  # 配置坏了也不能让服务起不来
                print(f"[config] 解析 {p} 失败，使用默认配置: {exc}")
        cfg = _apply_env(cfg)
        _CACHE = cfg
        return cfg


def get() -> dict:
    with _LOCK:
        if _CACHE is None:
            return reload()
        return _CACHE


def db_path() -> Path:
    raw = str(get()["storage"].get("sqlite_path") or "data/captcha.db")
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / p)
