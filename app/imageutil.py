# -*- coding: utf-8 -*-
"""图片解码工具。

油猴脚本上传的图片字段可能是：
  - 完整 data URL：`data:image/png;base64,iVBOR...`
  - 纯 base64：`iVBOR...`（可能含换行 / 空格 / URL-safe 字符）
  - 远程 URL（默认禁止，避免 SSRF）
"""
from __future__ import annotations

import base64
import binascii
import re

_DATA_URL_RE = re.compile(r"^data:[^;,]*;base64,", re.I)


class ImageDecodeError(ValueError):
    pass


def looks_like_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value or "", re.I))


def to_bytes(value) -> bytes:
    """把脚本传来的字符串还原成图片字节。"""
    raw = str(value or "").strip()
    if not raw:
        raise ImageDecodeError("图片字段为空")

    if looks_like_url(raw):
        raise ImageDecodeError(
            "不允许远程图片 URL（如需开启请设置 ocr.allow_remote_image_url=true）"
        )

    if _DATA_URL_RE.match(raw):
        raw = raw.split(",", 1)[1] if "," in raw else ""

    raw = re.sub(r"\s+", "", raw)
    # 兼容 URL-safe base64
    raw = raw.replace("-", "+").replace("_", "/")
    pad = len(raw) % 4
    if pad:
        raw += "=" * (4 - pad)

    try:
        return base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageDecodeError(f"base64 解码失败: {exc}") from exc


def to_bytes_optional(value) -> bytes | None:
    try:
        return to_bytes(value)
    except ImageDecodeError:
        return None
