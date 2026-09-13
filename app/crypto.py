# -*- coding: utf-8 -*-
"""油猴脚本的请求加/解密层（逆向自 v7.95）。

脚本里有这么一段（`P()` 判断后端地址是否为本机）：

    function Ne(){return !P()}
    function he(k){ if(!Ne()) return JSON.stringify(k);
                    let y=Z(k); return JSON.stringify(y?{protected:y}:k) }
    function nt(k,y){ ... if(Ne()){ I.searchParams.set("enc", be(y)) } ... }

也就是说 —— **只要后端不是 127.0.0.1/localhost，脚本就会把请求加密**：

* POST 体 → `{"protected": {"v":1,"alg":"fnv1a32-xorshift32","nonce":"..","payload":"<base64>"}}`
* GET 查询串 → `?enc=<base64url(JSON(protected))>`（**替换掉**原来的明文字段）

响应方向是可选的：脚本的 `ve()` 只在响应顶层有 `protected` 时才解包，
没有就直接用原响应。所以后端返回普通信封即可，无需加密。

算法（纯自研、无第三方库）：

    seed   = FNV1a32("ctw_2026_slide_mask_v1|<nonce>")  or  0xA5A5A5A5
    state  = seed
    for i in range(len(data)):
        state = xorshift32(state)
        out[i] = data[i] ^ (state & 0xFF)

本模块同时提供加密方向，用于自测与端到端联调。
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
from typing import Any

# 脚本里的 Ve 常量（同一处还定义了 Fe="X-Captcha-Protected"）
MASK_SEED = "ctw_2026_slide_mask_v1"
# An(...) 返回 0（falsy）时的兜底取值，脚本里写死 2779096485 = 0xA5A5A5A5
FALLBACK_SEED = 2779096485
ALG = "fnv1a32-xorshift32"


# ---------------------------------------------------------------------------
# 原语
# ---------------------------------------------------------------------------
def fnv1a32(text: str) -> int:
    """FNV-1a 32 位，对应脚本的 An()。

    JS: R=2166136261; R^=byte; R=Math.imul(R,16777619)>>>0
    Math.imul 取有符号乘积的低 32 位，与 (h*prime)&0xFFFFFFFF 等价。
    """
    h = 2166136261
    for b in str(text).encode("utf-8"):
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def xorshift32(x: int) -> int:
    """xorshift32，对应脚本的 mt()。

    JS: y=k>>>0; y^=y<<13>>>0; y^=y>>>17; y^=y<<5>>>0; return y>>>0
    每一步都按 uint32 掩码，位模式与 JS 的 int32 异或再 >>>0 完全一致。
    """
    x &= 0xFFFFFFFF
    x ^= (x << 13) & 0xFFFFFFFF
    x &= 0xFFFFFFFF
    x ^= x >> 17
    x &= 0xFFFFFFFF
    x ^= (x << 5) & 0xFFFFFFFF
    return x & 0xFFFFFFFF


def _seed_for(nonce: str) -> int:
    return fnv1a32(f"{MASK_SEED}|{nonce}") or FALLBACK_SEED


def _xor_stream(data: bytes, nonce: str) -> bytes:
    state = _seed_for(nonce)
    out = bytearray(len(data))
    for i, b in enumerate(data):
        state = xorshift32(state)
        out[i] = b ^ (state & 0xFF)
    return bytes(out)


# ---------------------------------------------------------------------------
# 解密
# ---------------------------------------------------------------------------
def decrypt_payload(nonce: Any, payload: Any) -> dict | None:
    """解开 {nonce, payload}，返回明文 dict；失败返回 None。"""
    nonce = str(nonce or "")
    payload = str(payload or "")
    if not nonce or not payload:
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None
    try:
        plain = _xor_stream(raw, nonce).decode("utf-8")
        obj = json.loads(plain)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def unwrap_protected(body: Any) -> dict | None:
    """body 形如 {"protected": {...}} 时解开并返回明文；否则返回 None。"""
    if not isinstance(body, dict):
        return None
    protected = body.get("protected")
    if not isinstance(protected, dict):
        return None
    return decrypt_payload(protected.get("nonce"), protected.get("payload"))


def _b64url_decode(token: str) -> bytes | None:
    s = str(token or "").strip()
    if not s:
        return None
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)  # 补回 padding
    try:
        return base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None


def decrypt_query(token: str) -> dict | None:
    """解开 GET 的 ?enc=<base64url(JSON(protected))>。

    注意脚本 be() 是把 Z() 的产物**直接**序列化，没有再包一层 {"protected": ...}：
        Z(k) -> {v,alg,nonce,payload}   → btoa(JSON(它)) → base64url
    这里同时兼容 {protected:{...}} 这种形态。
    """
    raw = _b64url_decode(token)
    if not raw:
        return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    nested = unwrap_protected(obj)
    if isinstance(nested, dict):
        return nested
    return decrypt_payload(obj.get("nonce"), obj.get("payload"))


# ---------------------------------------------------------------------------
# 加密（仅用于自测 / 联调，服务端不需要）
# ---------------------------------------------------------------------------
def encrypt_payload(obj: dict, nonce: str | None = None) -> dict:
    """生成脚本 Z() 的产物：{v, alg, nonce, payload}。"""
    nonce = nonce or secrets.token_hex(8)[:16]
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(_xor_stream(data, nonce)).decode("ascii")
    return {"v": 1, "alg": ALG, "nonce": nonce, "payload": payload}


def wrap_protected(obj: dict, nonce: str | None = None) -> dict:
    """生成脚本 he() 的产物：POST 请求体。"""
    return {"protected": encrypt_payload(obj, nonce)}


def encode_query_token(obj: dict, nonce: str | None = None) -> str:
    """生成脚本 be() 的产物：GET 的 enc 参数值（base64url、无 padding）。"""
    blob = json.dumps(encrypt_payload(obj, nonce), ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(blob.encode("utf-8")).decode("ascii").rstrip("=")
