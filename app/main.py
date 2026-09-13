# -*- coding: utf-8 -*-
"""自建验证码识别后端 —— 兼容《哈士奇的网页验证码识别工具》油猴脚本。

统一响应信封：{"ok":true,"data":{...}} / {"ok":false,"error":{"code","message"}}
启动：uvicorn app.main:app --host 0.0.0.0 --port 7070
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from . import ocr_engine, settings, slider_engine, store
from .imageutil import ImageDecodeError, to_bytes
from .protocol import as_dict, client_ip, fail, is_private_ip, ok, peer_ip, q

app = FastAPI(
    title="Self-hosted Captcha Recognition Backend",
    description="兼容哈士奇油猴验证码脚本的自建后端",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _startup() -> None:
    settings.reload()
    store.init()
    if settings.get()["ocr"].get("provider") == "ddddocr":
        ocr_engine.warmup()
    print("[startup] 服务已就绪")
    if not slider_engine.available():
        print(f"[startup] 警告：滑块识别不可用（{slider_engine.unavailable_reason()}）")
    if not ocr_engine.available():
        print(f"[startup] 警告：普通验证码识别不可用（{ocr_engine.unavailable_reason()}）")
    _log_auth_posture()


def _log_auth_posture() -> None:
    """把鉴权配置打印出来，配置改了不用猜到底生效没有。"""
    cfg = settings.get()["auth"]
    layers = []
    if cfg.get("require_protected_header"):
        layers.append("X-Captcha-Protected")
    if cfg.get("require_api_key"):
        layers.append("X-Api-Key")
    print(f"[startup] 鉴权: {' + '.join(layers) if layers else '未启用（裸奔！）'}")
    if cfg.get("trust_localhost"):
        print("[startup] 鉴权: 环回/内网请求免密钥（trust_localhost=true）")
    if cfg.get("trust_proxy_headers"):
        print("[startup] 警告：信任 X-Forwarded-For 作为限额 IP，该头可被伪造")
    print(f"[startup] 每日限额: {int(cfg.get('daily_limit_per_ip') or 0) or '不限'} 次/IP")


# ---------------------------------------------------------------------------
# 通用依赖
# ---------------------------------------------------------------------------
async def read_json(request: Request) -> dict:
    try:
        raw = await request.body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def guard(request: Request) -> str:
    """鉴权 + 每日限额。返回用于限额计数的客户端 IP。

    鉴权是两层的，各自可单独关闭：
      1. require_protected_header —— 要求 `X-Captcha-Protected: 1`。
         油猴脚本对非 localhost 的后端地址会自动带上（源码 Fe="X-Captcha-Protected"），
         所以这一层不挡正常脚本，只挡 curl / 扫描器 / 直接打接口的人。
      2. require_api_key —— 要求 `X-Api-Key`（或 `?api_key=`）等于配置的密钥。
         脚本原生不发这个头，密钥由 tools/patch_userscript.py --api-key 注入，
         因此只有你自己的脚本副本持有它。

    trust_localhost 打开时，来自环回/内网网段的请求免密钥 —— 判定用的是
    **不可伪造的 TCP 对端**（peer_ip），公网请求拿不到这个豁免。
    """
    cfg = settings.get()["auth"]
    ip = _client_ip(request)

    if not _is_trusted_local(request):
        if cfg.get("require_protected_header"):
            if request.headers.get("x-captcha-protected") != "1":
                raise PermissionError("PROTECTED_HEADER_REQUIRED")
        if cfg.get("require_api_key"):
            if not _key_matches(request, cfg.get("api_key")):
                raise PermissionError("API_KEY_INVALID")

    limit = int(cfg.get("daily_limit_per_ip") or 0)
    if limit > 0 and store.quota_peek(ip) >= limit:
        raise RuntimeError("DAILY_LIMIT_EXCEEDED")

    return ip


def admin_guard(request: Request) -> str:
    """管理接口鉴权：本机/内网放行，否则必须带 admin_key（留空则复用 api_key）。"""
    cfg = settings.get()["auth"]
    if _is_trusted_local(request):
        return peer_ip(request)
    key = str(cfg.get("admin_key") or cfg.get("api_key") or "")
    if key and _key_matches(request, key):
        return peer_ip(request)
    raise PermissionError("ADMIN_KEY_INVALID")


def _client_ip(request: Request) -> str:
    """限额计数用的 IP。默认只认 TCP 对端，避免 XFF 伪造绕过每日限额。"""
    cfg = settings.get()["auth"]
    return client_ip(request, trust_proxy=bool(cfg.get("trust_proxy_headers")))


def _is_trusted_local(request: Request) -> bool:
    cfg = settings.get()["auth"]
    return bool(cfg.get("trust_localhost")) and is_private_ip(peer_ip(request))


def _key_matches(request: Request, expected: Any) -> bool:
    """恒定时间比较，避免时序侧信道。"""
    expected = str(expected or "")
    if not expected:
        return False
    given = str(request.headers.get("x-api-key") or q(request, "api_key") or "")
    return secrets.compare_digest(given, expected)


# 鉴权失败时下发的提示。key 故意不用 "rate_limited" —— 脚本见到那个 key
# 会把当前 host 标记为已限流，属于副作用，鉴权场景不需要。
_AUTH_NOTICE = {
    "key": "auth_required",
    "message": "该识别服务需要授权，请使用配套的油猴脚本",
    "displayMessage": "该识别服务需要授权，请使用配套的油猴脚本",
}


def _guard_error(exc: Exception):
    if isinstance(exc, PermissionError):
        code = str(exc) or "UNAUTHORIZED"
        if code == "API_KEY_INVALID":
            msg = "API Key 无效或缺失，请在脚本配置中填入正确的密钥"
        elif code == "PROTECTED_HEADER_REQUIRED":
            msg = "拒绝直连调用：请使用配套的油猴脚本访问"
        elif code == "ADMIN_KEY_INVALID":
            msg = "管理接口需要管理员密钥"
        else:
            msg = "未授权"
        return fail(code, msg, notice=_AUTH_NOTICE)
    if isinstance(exc, RuntimeError) and "DAILY_LIMIT_EXCEEDED" in str(exc):
        return fail("DAILY_LIMIT_EXCEEDED", "今日识别次数已达上限，请明天再试")
    return fail("GUARD_ERROR", str(exc))


def check_quota_code(code: str) -> str | None:
    """返回 None 表示通过，否则返回错误说明。"""
    codes = settings.get().get("quota_codes") or []
    if not codes:
        return None
    if not code:
        return "缺少额度码"
    if code not in codes:
        return "额度码无效"
    return None


# ---------------------------------------------------------------------------
# 健康检查 / 首页
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> dict:
    return ok(
        {
            "service": "captcha-recognition-backend",
            "version": app.version,
            "ocr": ocr_engine.available(),
            "slider": slider_engine.available(),
            "endpoints": [
                "POST /captcha",
                "POST /captcha-feedback",
                "POST /api/slide/solve",
                "GET  /api/slide/trajectory/config",
                "POST /api/slide/trajectory/report",
                "POST /api/slide/human-sample/report",
                "POST /api/normal/shared-binding/report",
                "POST /api/normal/shared-binding/event",
                "POST /api/slide/shared-binding/report",
                "POST /api/slide/shared-binding/event",
                "GET  /api/slide/shared-binding",
                "GET  /api/userscript/config",
                "GET  /api/userscript/page-config",
                "POST /api/userscript/events",
                "POST /api/userscript/event",
                "POST /api/userscript/identity-sync",
                "POST /api/userscript/trace-upload",
                "POST /api/userscript/suppression-summary",
                "POST /api/userscript/quota-code/activate",
            ],
        }
    )


@app.get("/health")
def health() -> dict:
    return ok(
        {
            "status": "up",
            "ocr": ocr_engine.available(),
            "ocr_error": "" if ocr_engine.available() else ocr_engine.unavailable_reason(),
            "slider": slider_engine.available(),
            "slider_error": "" if slider_engine.available() else slider_engine.unavailable_reason(),
            "time": store.now_iso(),
        }
    )


# ---------------------------------------------------------------------------
# 1. 普通验证码识别
# ---------------------------------------------------------------------------
@app.post("/captcha")
async def captcha(request: Request):
    t0 = time.time()
    try:
        ip = guard(request)
    except Exception as exc:
        return _guard_error(exc)

    body = await read_json(request)
    meta = as_dict(body.get("meta"))

    err = check_quota_code(str(body.get("quotaCode") or body.get("quota_code") or ""))
    if err:
        return fail("QUOTA_CODE_INVALID", err)

    try:
        image_bytes = to_bytes(body.get("image"))
    except ImageDecodeError as exc:
        store.log_recognition(ip=ip, kind="normal", host=meta.get("host", ""), ok=False,
                              error_code="INVALID_IMAGE", duration_ms=int((time.time() - t0) * 1000))
        return fail("INVALID_IMAGE", str(exc))

    try:
        result = ocr_engine.recognize(str(body.get("image") or ""))
    except RuntimeError as exc:
        store.log_recognition(ip=ip, kind="normal", host=meta.get("host", ""), ok=False,
                              error_code="OCR_UNAVAILABLE", duration_ms=int((time.time() - t0) * 1000))
        return fail("OCR_UNAVAILABLE", str(exc))
    except Exception as exc:
        store.log_recognition(ip=ip, kind="normal", host=meta.get("host", ""), ok=False,
                              error_code="RECOGNIZE_FAILED", duration_ms=int((time.time() - t0) * 1000))
        return fail("RECOGNIZE_FAILED", f"识别失败：{exc}")

    duration_ms = int((time.time() - t0) * 1000)
    request_id = str(meta.get("requestId") or "")

    store.quota_consume(ip)
    store.log_recognition(
        ip=ip, kind="normal",
        host=meta.get("host", ""), href=meta.get("href", ""), route_path=meta.get("routePath", ""),
        device_id=meta.get("deviceId", ""), visit_id=meta.get("visitId", ""), request_id=request_id,
        image_sha256=store.sha256_hex(image_bytes),
        result=result["value"], confidence=result["confidence"], ok=True, duration_ms=duration_ms,
    )

    return ok(
        {
            "value": result["value"],
            "confidence": result["confidence"],
            "result": result["value"],
            "engine": result["engine"],
            "rawText": result["raw_text"],
            "requestLogId": request_id,
            "durationMs": duration_ms,
        }
    )


# ---------------------------------------------------------------------------
# 2. 识别结果反馈
# ---------------------------------------------------------------------------
@app.post("/captcha-feedback")
async def captcha_feedback(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    meta = as_dict(body.get("meta"))
    store.log_feedback(ip, str(meta.get("host") or ""), body)
    return ok({"accepted": True})


# ---------------------------------------------------------------------------
# 3. 滑块求解
# ---------------------------------------------------------------------------
@app.post("/api/slide/solve")
@app.post("/api/test/slide-captcha/solve")
async def slide_solve(request: Request):
    t0 = time.time()
    try:
        ip = guard(request)
    except Exception as exc:
        return _guard_error(exc)

    body = await read_json(request)
    meta = as_dict(body.get("meta"))
    method = str(body.get("method") or "match")

    err = check_quota_code(str(body.get("quotaCode") or body.get("quota_code") or ""))
    if err:
        return fail("QUOTA_CODE_INVALID", err)

    if not slider_engine.available():
        return fail("SLIDE_UNAVAILABLE", f"滑块识别不可用：{slider_engine.unavailable_reason()}")

    try:
        bg = to_bytes(body.get("backgroundImage") or body.get("background_image"))
        tgt = to_bytes(body.get("targetImage") or body.get("target_image"))
    except ImageDecodeError as exc:
        return fail("INVALID_IMAGE", f"滑块图片无效：{exc}")

    try:
        display_width = float(body.get("backgroundDisplayWidth") or body.get("background_display_width") or 0)
    except Exception:
        display_width = 0.0

    try:
        result = slider_engine.solve(bg, tgt, display_width, method)
    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        store.log_recognition(ip=ip, kind="slide", host=meta.get("host", ""), ok=False,
                              error_code="SLIDE_SOLVE_FAILED", duration_ms=duration_ms)
        return fail("SLIDE_SOLVE_FAILED", f"滑块求解失败：{exc}")

    duration_ms = int((time.time() - t0) * 1000)
    request_id = str(meta.get("requestId") or "")

    store.quota_consume(ip)
    store.log_recognition(
        ip=ip, kind="slide",
        host=meta.get("host", ""), href=meta.get("href", ""), route_path=meta.get("routePath", ""),
        device_id=meta.get("deviceId", ""), visit_id=meta.get("visitId", ""), request_id=request_id,
        image_sha256=store.sha256_hex(bg),
        result=str(result["distance"]), confidence=result["confidence"],
        raw_distance=result["raw_distance"], distance=result["distance"],
        ok=True, duration_ms=duration_ms,
    )

    data = {
        "distance": result["distance"],
        "rawDistance": result["raw_distance"],
        "modelDistance": result["model_distance"],
        "confidence": result["confidence"],
        "result": str(result["distance"]),
        "solverEngineLabel": result["solver_engine_label"],
        "requestLogId": request_id,
    }
    return ok(data)


# ---------------------------------------------------------------------------
# 4. 滑块轨迹配置 / 上报 / 人工样本
# ---------------------------------------------------------------------------
@app.get("/api/slide/trajectory/config")
def slide_trajectory_config(request: Request):
    cfg = dict(settings.get()["trajectory_config"])
    cfg.setdefault("config_version", "self-hosted-1")
    cfg.setdefault("updated_at", int(time.time() * 1000))
    cfg.setdefault("templates", [])
    cfg.setdefault("route_dispatch_overrides", [])
    cfg.setdefault("default_template", None)
    return ok(cfg)


@app.post("/api/slide/trajectory/report")
async def slide_trajectory_report(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    store.log_slide_sample(ip, "trajectory", body)
    return ok({"accepted": True})


@app.post("/api/slide/human-sample/report")
async def slide_human_sample_report(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    store.log_slide_sample(ip, "human", body)
    return ok({"ok": True, "saved": True, "accepted": True})


# ---------------------------------------------------------------------------
# 5. 共享绑定
# ---------------------------------------------------------------------------
def _binding_ack():
    return ok({"accepted": True})


@app.post("/api/normal/shared-binding/report")
async def normal_binding_report(request: Request):
    ip = _client_ip(request)
    store.log_binding(ip, "normal", "report", await read_json(request))
    return _binding_ack()


@app.post("/api/normal/shared-binding/event")
async def normal_binding_event(request: Request):
    ip = _client_ip(request)
    store.log_binding(ip, "normal", "event", await read_json(request))
    return _binding_ack()


@app.post("/api/slide/shared-binding/report")
async def slide_binding_report(request: Request):
    ip = _client_ip(request)
    store.log_binding(ip, "slide", "report", await read_json(request))
    return _binding_ack()


@app.post("/api/slide/shared-binding/event")
async def slide_binding_event(request: Request):
    ip = _client_ip(request)
    store.log_binding(ip, "slide", "event", await read_json(request))
    return _binding_ack()


@app.get("/api/slide/shared-binding")
def slide_binding_fetch(request: Request):
    # 自建服务不做"跨用户共享绑定"，统一返回空结果，脚本会退回本地绑定
    return ok({"status": "empty", "conflict": False, "items": []})


@app.post("/api/slide/sensitive-limit/check")
async def slide_sensitive_check(request: Request):
    return ok({"blocked": False, "accepted": False, "reason": ""})


@app.post("/api/slide/sensitive-limit/success")
async def slide_sensitive_success(request: Request):
    return ok({"accepted": True})


# ---------------------------------------------------------------------------
# 6. 远程配置
# ---------------------------------------------------------------------------
@app.get("/api/userscript/config")
def userscript_config(request: Request):
    cfg = dict(settings.get()["userscript_config"])
    # 前端 bo() 要求 features / rules / interactionNotices 必须是对象
    for key in ("features", "rules", "interactionNotices"):
        if not isinstance(cfg.get(key), dict):
            cfg[key] = {}
    cfg["configVersion"] = str(cfg.get("config_version") or "self-hosted-1")
    return ok(cfg)


@app.get("/api/userscript/page-config")
def userscript_page_config(request: Request):
    cfg = settings.get()["page_config"]
    payload = {
        "cache_ttl_seconds": int(cfg.get("cache_ttl_seconds") or 3600),
        "host": q(request, "host").lower(),
        "pathname": q(request, "pathname") or q(request, "route_path"),
        "normal_image_denylist": cfg.get("normal_image_denylist") or [],
        "config_version": "self-hosted-1",
    }
    # 注意：不要返回无效的 slideTrajectory，否则前端 Kn() 会判定整个配置无效
    return ok(payload)


# ---------------------------------------------------------------------------
# 7. 埋点 / 链路 / 身份 / 额度码
# ---------------------------------------------------------------------------
@app.post("/api/userscript/events")
async def userscript_events(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    events = body.get("events")
    if not isinstance(events, list):
        events = [events] if events is not None else []

    host = ""
    for item in events:
        if not isinstance(item, dict):
            continue
        meta = as_dict(item.get("meta"))
        host = host or str(meta.get("host") or item.get("host") or "")
        store.log_event(ip, host, str(item.get("event") or item.get("eventName") or ""), item)

    # responses 必须与 events 一一对应
    return ok(
        {
            "accepted": True,
            "accepted_count": len(events),
            "responses": [{"accepted": True} for _ in events],
            "stop_event_upload_today": False,
            "reason": "",
        }
    )


@app.post("/api/userscript/event")
async def userscript_event(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    meta = as_dict(body.get("meta"))
    store.log_event(ip, str(meta.get("host") or ""), str(body.get("event") or ""), body)
    return ok({"accepted": True})


@app.post("/api/userscript/trace-upload")
async def userscript_trace_upload(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    store.log_event(ip, "", "trace-upload", body)
    return ok({"accepted": True})


@app.post("/api/userscript/identity-sync")
async def userscript_identity_sync(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    store.log_identity(ip, body)
    return ok({"accepted": True, "status": "ok"})


@app.post("/api/userscript/suppression-summary")
async def userscript_suppression_summary(request: Request):
    ip = _client_ip(request)
    body = await read_json(request)
    store.log_event(ip, "", "suppression-summary", body)
    return ok({"accepted": True})


@app.post("/api/userscript/quota-code/activate")
async def quota_code_activate(request: Request):
    body = await read_json(request)
    code = str(body.get("quotaCode") or body.get("quota_code") or "").strip()
    err = check_quota_code(code)
    if err:
        return fail("QUOTA_CODE_INVALID", err)
    return ok({"ok": True, "accepted": True, "quotaCode": code, "remaining": None})


# ---------------------------------------------------------------------------
# 8. 管理接口（需要管理员密钥；本机/内网免密钥）
# ---------------------------------------------------------------------------
@app.get("/admin/stats")
def admin_stats(request: Request):
    try:
        admin_guard(request)
    except Exception as exc:
        return _guard_error(exc)
    return ok(store.stats())


@app.post("/admin/reload")
def admin_reload(request: Request):
    try:
        admin_guard(request)
    except Exception as exc:
        return _guard_error(exc)
    settings.reload()
    store.reset_connection()
    store.init()
    return ok({"reloaded": True})


@app.get("/admin/slider-debug")
def admin_slider_debug(request: Request):
    """调滑块参数用：POST 图片到 /admin/slider-debug，这里只提示用法。"""
    try:
        admin_guard(request)
    except Exception as exc:
        return _guard_error(exc)
    return ok({"usage": "POST /admin/slider-debug，body 同 /api/slide/solve，返回叠加了匹配框的 PNG"})


@app.post("/admin/slider-debug")
async def admin_slider_debug_post(request: Request):
    try:
        admin_guard(request)
    except Exception as exc:
        return _guard_error(exc)
    body = await read_json(request)
    try:
        bg = to_bytes(body.get("backgroundImage"))
        tgt = to_bytes(body.get("targetImage"))
    except ImageDecodeError as exc:
        return fail("INVALID_IMAGE", str(exc))
    try:
        dw = float(body.get("backgroundDisplayWidth") or 0)
    except Exception:
        dw = 0.0
    try:
        result = slider_engine.solve(bg, tgt, dw, str(body.get("method") or "match"))
    except Exception as exc:
        return fail("SLIDE_SOLVE_FAILED", str(exc))
    png = slider_engine.visualize(bg, tgt, result)
    if not png:
        return fail("VISUALIZE_FAILED", "可视化失败")
    headers = {
        "X-Distance": str(result["distance"]),
        "X-Raw-Distance": str(result["raw_distance"]),
        "X-Confidence": str(result["confidence"]),
    }
    return Response(content=png, media_type="image/png", headers=headers)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=200, content={"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}})
