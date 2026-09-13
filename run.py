# -*- coding: utf-8 -*-
"""本地启动脚本：python run.py

等价于：uvicorn app.main:app --host <config.server.host> --port <config.server.port>
"""
from __future__ import annotations

import sys

import uvicorn

from app import settings


def main() -> int:
    cfg = settings.get()["server"]
    host = str(cfg.get("host") or "0.0.0.0")
    port = int(cfg.get("port") or 7070)
    log_level = str(cfg.get("log_level") or "info")
    print(f"  服务地址: http://{host}:{port}")
    print(f"  健康检查: http://127.0.0.1:{port}/health")
    uvicorn.run("app.main:app", host=host, port=port, log_level=log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
