# -*- coding: utf-8 -*-
"""响应信封与通用小工具。

油猴脚本侧统一按 `{ok:true,data:{...}}` / `{ok:false,error:{code,message}}` 解析，
所有接口都必须遵守这个约定，否则前端会报 "Unknown response structure"。
"""
from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def ok(data: Any = None) -> dict:
    """成功信封。data 必须能 JSON 序列化。"""
    return {"ok": True, "data": {} if data is None else data}


def fail(
    code: str,
    message: str,
    http_status: int = 200,
    notice: dict | None = None,
    **extra,
) -> JSONResponse:
    """失败信封。

    注意：刻意用 HTTP 200 返回业务错误，前端才能读到 error.code / error.message。
    只有额度码接口依赖 404/405 判定"服务端不支持"，我们在那里返回 200 表示支持。

    notice 是**信封顶层**字段（与 ok / error 平级）。油猴脚本会把它挂到抛出的
    Error 上，并用 `notice.displayMessage || notice.message` 弹提示；脚本还会读
    `notice.key`，值为 "rate_limited" 时会把该 host 标记为已限流，所以自定义
    场景不要用这个 key。
    """
    err: dict = {"code": str(code), "message": str(message)}
    err.update(extra)
    body: dict = {"ok": False, "error": err}
    if isinstance(notice, dict):
        body["notice"] = notice
    return JSONResponse(status_code=http_status, content=body)


def peer_ip(request: Request) -> str:
    """TCP 对端 IP。

    这个值来自 socket，客户端伪造不了，因此鉴权判定必须用它。
    """
    return request.client.host if request.client else "unknown"


def client_ip(request: Request, trust_proxy: bool = False) -> str:
    """取用于限额计数的客户端 IP。

    trust_proxy=False（默认）时只认 TCP 对端 —— 否则任何人加一个
    `X-Forwarded-For: 1.2.3.4` 就能换着 IP 绕过每日限额。
    只有服务确实部署在可信反向代理之后（例如本机 Caddy）才该打开。
    """
    if not trust_proxy:
        return peer_ip(request)
    for header in ("x-forwarded-for", "x-real-ip"):
        raw = request.headers.get(header)
        if raw:
            return raw.split(",")[0].strip()
    return peer_ip(request)


def is_private_ip(ip: str) -> bool:
    """环回 / 私有网段 / 链路本地地址判定。

    用于识别"来自本机或容器网关"的请求。公网请求的 TCP 对端一定是公网 IP，
    所以这里不可能被外部伪造。
    """
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    return bool(addr.is_loopback or addr.is_private or addr.is_link_local)


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def q(request: Request, name: str, default: str = "") -> str:
    return str(request.query_params.get(name, default) or "").strip()
