# -*- coding: utf-8 -*-
"""响应信封与通用小工具。

油猴脚本侧统一按 `{ok:true,data:{...}}` / `{ok:false,error:{code,message}}` 解析，
所有接口都必须遵守这个约定，否则前端会报 "Unknown response structure"。
"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def ok(data: Any = None) -> dict:
    """成功信封。data 必须能 JSON 序列化。"""
    return {"ok": True, "data": {} if data is None else data}


def fail(code: str, message: str, http_status: int = 200, **extra) -> JSONResponse:
    """失败信封。

    注意：刻意用 HTTP 200 返回业务错误，前端才能读到 error.code / error.message。
    只有额度码接口依赖 404/405 判定"服务端不支持"，我们在那里返回 200 表示支持。
    """
    err: dict = {"code": str(code), "message": str(message)}
    err.update(extra)
    return JSONResponse(status_code=http_status, content={"ok": False, "error": err})


def client_ip(request: Request) -> str:
    """取真实客户端 IP，兼容反向代理。"""
    for header in ("x-forwarded-for", "x-real-ip"):
        raw = request.headers.get(header)
        if raw:
            return raw.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def q(request: Request, name: str, default: str = "") -> str:
    return str(request.query_params.get(name, default) or "").strip()
