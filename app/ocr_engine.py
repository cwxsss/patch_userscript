# -*- coding: utf-8 -*-
"""普通验证码 OCR 引擎。

默认使用 ddddocr（内置神经网络模型，对数字+字母验证码开箱即用）。
若未安装 ddddocr，服务仍可启动，但 /captcha 会返回 OCR_UNAVAILABLE。
"""
from __future__ import annotations

import re
import threading
from typing import Any

from . import settings
from .imageutil import ImageDecodeError, to_bytes

_LOCK = threading.RLock()
_OCR_BASIC: Any = None
_OCR_BETA: Any = None
_IMPORT_ERROR: str | None = None

_ARITH_CHARS = "0123456789+-*/()."
_OP_MAP = {
    "×": "*", "✕": "*", "✖": "*", "x": "*", "X": "*", "＊": "*",
    "÷": "/", "／": "/", "➗": "/",
    "＋": "+", "－": "-", "−": "-", "—": "-", "=": "", "?": "", "？": "",
    "＝": "",
}


def _module():
    global _IMPORT_ERROR
    try:
        import ddddocr  # type: ignore
        return ddddocr
    except Exception as exc:  # pragma: no cover
        _IMPORT_ERROR = str(exc)
        return None


def available() -> bool:
    return _module() is not None


def unavailable_reason() -> str:
    _module()
    return _IMPORT_ERROR or "ddddocr 未安装"


def _engine(beta: bool):
    global _OCR_BASIC, _OCR_BETA
    mod = _module()
    if mod is None:
        return None
    with _LOCK:
        if beta:
            if _OCR_BETA is None:
                _OCR_BETA = mod.DdddOcr(beta=True, show_ad=False)
            return _OCR_BETA
        if _OCR_BASIC is None:
            _OCR_BASIC = mod.DdddOcr(show_ad=False)
        return _OCR_BASIC


def _normalize_for_eval(text: str) -> str:
    out = text.strip()
    for src, dst in _OP_MAP.items():
        out = out.replace(src, dst)
    out = re.sub(r"\s+", "", out)
    return out


def try_arithmetic(text: str) -> str | None:
    """把 "3+4" / "12-5=?" 这类表达式算成结果。失败返回 None。"""
    expr = _normalize_for_eval(text)
    if not expr or not re.fullmatch(r"[0-9+\-*/().]+", expr):
        return None
    if not re.search(r"\d", expr) or not re.search(r"[+\-*/]", expr):
        return None
    # 只允许**单层**简单算式，避免执行到奇怪的东西
    if expr.count("(") > 2 or len(expr) > 32:
        return None
    try:
        value = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - 已用白名单正则严格限制
    except Exception:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if abs(value) > 1e9:
            return None
        return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _clean(text: str) -> str:
    # 去掉常见干扰字符，保留字母数字
    return re.sub(r"[^0-9A-Za-z]", "", text or "")


def recognize(image: str) -> dict:
    """识别一张普通验证码图片。

    返回：{"value": 结果字符串, "confidence": 0~1, "raw_text": 模型原始输出, "engine": "ddddocr"}
    """
    cfg = settings.get()["ocr"]
    if cfg.get("provider") != "ddddocr":
        raise RuntimeError("普通验证码识别已通过配置关闭（ocr.provider != ddddocr）")

    data = to_bytes(image)
    if not data:
        raise ImageDecodeError("图片内容为空")

    engine = _engine(bool(cfg.get("beta_model")))
    if engine is None:
        raise RuntimeError(f"OCR 引擎不可用：{unavailable_reason()}")

    with _LOCK:
        try:
            raw = engine.classification(data)
        except Exception as exc:
            raise RuntimeError(f"OCR 识别失败：{exc}") from exc

    raw = str(raw or "").strip()
    result = raw
    confidence = 0.0

    if cfg.get("arithmetic", True):
        arith = try_arithmetic(raw)
        if arith is not None:
            return {"value": arith, "confidence": 0.95, "raw_text": raw, "engine": "ddddocr-arith"}

    cleaned = _clean(raw)
    result = cleaned or raw
    # ddddocr 不返回置信度，这里给出一个基于输出形态的粗略估计
    if not result:
        confidence = 0.0
    elif 3 <= len(result) <= 8:
        confidence = 0.85
    else:
        confidence = 0.6

    return {"value": result, "confidence": confidence, "raw_text": raw, "engine": "ddddocr"}


def warmup() -> None:
    """预加载模型，避免首个请求超时。"""
    try:
        _engine(False)
    except Exception as exc:
        print(f"[ocr] 预加载失败：{exc}")
