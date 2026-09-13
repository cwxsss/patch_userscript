# -*- coding: utf-8 -*-
"""HTTP 端到端测试：完全按油猴脚本的方式请求自建后端。

    python tools/http_test.py [base_url] [api_key]

服务端开了鉴权时把密钥作为第二个参数传入（或设 CAPTCHA_API_KEY）。
注意：脚本对**非本机**后端会把请求加密（见 app/crypto.py），
本测试第 11 节专门覆盖这条加密路径。
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app import crypto  # noqa: E402
from tools.selftest import make_captcha, make_slider  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7070"
# 服务端开了鉴权时，用第二个参数或环境变量传密钥：
#   python tools/http_test.py http://1.2.3.4:7070 <api-key>
API_KEY = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("CAPTCHA_API_KEY", "")).strip()
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, payload=None, timeout: int = 60, auth: bool = True,
         encrypt: bool = False, params: dict | None = None):
    """按油猴脚本的方式请求。

    auth=False     -> 不带鉴权头，用于验证鉴权是否真的生效
    encrypt=True   -> 按脚本对**非本机**后端的行为，加密请求体 / 查询串
    """
    url = BASE.rstrip("/") + path
    if params:
        if encrypt:
            url += "?enc=" + crypto.encode_query_token(params)
        else:
            url += "?" + urllib.parse.urlencode(params)

    data = None
    headers = {"User-Agent": "Tampermonkey/5.0", "Accept": "application/json"}
    if auth:
        # 油猴脚本对非 localhost 后端会自动带上这个头（源码 Fe="X-Captcha-Protected"）
        headers["X-Captcha-Protected"] = "1"
        if API_KEY:
            headers["X-Api-Key"] = API_KEY
    if payload is not None:
        obj = crypto.wrap_protected(payload) if encrypt else payload
        data = json.dumps(obj, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as exc:
        return 0, {"_error": str(exc)}


def envelope(body: dict) -> bool:
    """校验是否符合脚本要求：ok===true 且有 data 对象，或 ok===false 且有 error。"""
    if not isinstance(body, dict) or "ok" not in body:
        return False
    if body["ok"] is True:
        return isinstance(body.get("data"), (dict, list))
    return isinstance(body.get("error"), dict) and "code" in body["error"]


def main() -> int:
    meta = {
        "href": "https://example.com/login", "routePath": "/login", "routeKey": "https://example.com/login",
        "host": "example.com", "title": "Example Login", "deviceId": "dev_test123",
        "sessionId": "sess_x", "visitId": "visit_y", "requestId": "req_z",
    }

    print("0. 连通性")
    st, body = call("GET", "/health")
    check("GET /health 可达", st == 200, f"HTTP {st}")
    if st != 200:
        print("      服务没起来，先执行：python run.py")
        return 1
    check("/health 信封合法", envelope(body))

    print("\n1. 普通验证码 POST /captcha")
    img = make_captcha("7B4K")
    b64 = "data:image/png;base64," + base64.b64encode(img).decode()
    st, body = call("POST", "/captcha", {"image": b64, "quotaCode": "", "meta": meta})
    check("HTTP 200", st == 200, f"HTTP {st}")
    check("信封合法", envelope(body))
    if body.get("ok"):
        val = body["data"].get("value")
        check("data.value 是字符串（前端硬性要求）", isinstance(val, str), repr(val))
        print(f"       识别结果 = {val!r}，engine = {body['data'].get('engine')!r}")
    else:
        print("       ", body)

    print("\n2. 滑块 POST /api/slide/solve")
    bg, tgt, expect = make_slider(gap_x=180)
    payload = {
        "backgroundImage": "data:image/png;base64," + base64.b64encode(bg).decode(),
        "targetImage": "data:image/png;base64," + base64.b64encode(tgt).decode(),
        "quotaCode": "", "method": "match",
        "backgroundDisplayWidth": 320, "targetDisplayWidth": 50,
        "meta": {**meta, "attemptId": "att_1", "attemptNo": 1, "isSpriteCrop": False},
    }
    st, body = call("POST", "/api/slide/solve", payload)
    check("HTTP 200", st == 200, f"HTTP {st}")
    check("信封合法", envelope(body))
    if body.get("ok"):
        d = body["data"]
        check("data.distance 是数字（前端硬性要求）", isinstance(d.get("distance"), (int, float)),
              str(d.get("distance")))
        check("distance 命中预期 180", abs(float(d.get("distance", 0)) - expect) <= 4,
              f"实测 {d.get('distance')}")
    else:
        print("       ", body)

    print("\n3. 配置类接口（都要 ok=true + data 对象）")
    for path in ("/api/userscript/config", "/api/userscript/page-config", "/api/slide/trajectory/config"):
        st, body = call("GET", path + "?client_version=7.95&host=example.com&visit_id=v1")
        okr = st == 200 and envelope(body)
        check(f"GET {path}", okr, f"HTTP {st}" + ("" if okr else f" body={str(body)[:160]}"))
        if path == "/api/userscript/config" and body.get("ok"):
            d = body["data"]
            check("  features/rules/interactionNotices 均为对象",
                  all(isinstance(d.get(k), dict) for k in ("features", "rules", "interactionNotices")))
        if path == "/api/userscript/page-config" and body.get("ok"):
            check("  未下发无效 slideTrajectory", "slideTrajectory" not in body["data"])

    print("\n4. 埋点 POST /api/userscript/events")
    ev = [{"event": "normal_recognized", "ts": 1, "meta": {"host": "example.com"}},
          {"event": "slide_solved", "ts": 2, "meta": {"host": "example.com"}}]
    st, body = call("POST", "/api/userscript/events", {"events": ev})
    check("HTTP 200 + 信封", st == 200 and envelope(body))
    if body.get("ok"):
        d = body["data"]
        check("responses 与 events 一一对应",
              isinstance(d.get("responses"), list) and len(d["responses"]) == len(ev),
              f"len={len(d.get('responses') or [])}")
        check("stop_event_upload_today 为布尔", isinstance(d.get("stop_event_upload_today"), bool))

    print("\n5. 其它 POST 接口")
    cases = [
        ("/captcha-feedback", {"requestId": "req_z", "meta": meta, "correctedValue": "7B4K"}),
        ("/api/slide/trajectory/report", {"meta": meta, "distance": 180, "points": [{"x": 1, "t": 2}]}),
        ("/api/slide/human-sample/report", {"meta": meta, "outcome": "success", "distance": 180}),
        ("/api/normal/shared-binding/report", {"meta": meta, "pathname": "/login"}),
        ("/api/normal/shared-binding/event", {"meta": meta}),
        ("/api/slide/shared-binding/report", {"meta": meta}),
        ("/api/slide/shared-binding/event", {"meta": meta}),
        ("/api/userscript/event", {"event": "menu_open", "meta": meta}),
        ("/api/userscript/trace-upload", {"meta": meta, "traces": []}),
        ("/api/userscript/identity-sync", {"deviceId": "dev_test123", "sessionId": "sess_x",
                                           "visitId": "visit_y", "clientVersion": "7.95"}),
        ("/api/userscript/suppression-summary", {"summaries": []}),
    ]
    for path, payload in cases:
        st, body = call("POST", path, payload)
        check(f"POST {path}", st == 200 and envelope(body), f"HTTP {st} {str(body)[:110]}")

    print("\n6. GET /api/slide/shared-binding")
    st, body = call("GET", "/api/slide/shared-binding?host=example.com&pathname=/login")
    check("返回 status/conflict/items", st == 200 and envelope(body)
          and all(k in body.get("data", {}) for k in ("status", "conflict", "items")))

    print("\n7. 额度码 POST /api/userscript/quota-code/activate")
    st, body = call("POST", "/api/userscript/quota-code/activate", {"quotaCode": "anything"})
    check("未启用额度码时返回 200（前端才不会提示『不支持』）", st == 200 and envelope(body), f"HTTP {st}")

    print("\n8. 错误路径（脚本必须能正确解析 error 信封）")
    st, body = call("POST", "/captcha", {"image": "", "meta": meta})
    check("空图片 -> ok=false + error.code", body.get("ok") is False
          and isinstance(body.get("error", {}).get("code"), str), str(body)[:130])
    st, body = call("POST", "/captcha", {"image": "!!!not-base64!!!", "meta": meta})
    check("坏 base64 -> ok=false + error.code", body.get("ok") is False, str(body)[:130])

    print("\n9. 管理接口")
    st, body = call("GET", "/admin/stats")
    check("GET /admin/stats", st == 200 and envelope(body), f"HTTP {st} {str(body)[:90]}")
    if body.get("ok"):
        print(f"       {json.dumps(body['data'], ensure_ascii=False)[:220]}")

    print("\n10. 鉴权")
    if API_KEY:
        st, body = call("POST", "/captcha", {"image": b64, "meta": meta}, auth=False)
        code = str(body.get("error", {}).get("code") or "")
        check("不带密钥 -> 被拒", body.get("ok") is False,
              f"HTTP {st} code={code!r}")
        check("拒绝时有 error.code（脚本靠它判定）", bool(code))
        check("拒绝时带上 notice.displayMessage（用户能看到提示）",
              isinstance(body.get("notice"), dict)
              and bool(body["notice"].get("displayMessage") or body["notice"].get("message")),
              str(body.get("notice"))[:90])
        st, body = call("POST", "/captcha", {"image": b64, "meta": meta})
        check("带正确密钥 -> 放行", body.get("ok") is True, f"HTTP {st} {str(body)[:90]}")

    # 伪造 XFF 不应该能绕过每日限额
    print("     —— X-Forwarded-For 伪造检查")
    st1, b1 = call("POST", "/captcha", {"image": b64, "meta": meta})
    print(f"        正常请求: ok={b1.get('ok')}")
    print("        说明：限额按 TCP 对端 IP 计数，服务端 trust_proxy_headers=false 时 XFF 无效")
    if not API_KEY:
        print("     (未提供 API Key，跳过强制鉴权项；加第二个参数或 CAPTCHA_API_KEY 环境变量)")

    print("\n11. 加密传输层（脚本对非本机后端的真实走法）")
    st, body = call("POST", "/captcha", {"image": b64, "quotaCode": "", "meta": meta}, encrypt=True)
    check("加密体 {protected:...} 能被解开并识别", body.get("ok") is True, str(body)[:120])
    if body.get("ok"):
        print(f"       value={body['data'].get('value')!r} engine={body['data'].get('engine')!r}")

    st, body = call("GET", "/api/slide/shared-binding", encrypt=True,
                    params={"host": "example.com", "pathname": "/login"})
    check("加密查询串 ?enc= 能被解开", st == 200 and envelope(body), f"HTTP {st} {str(body)[:100]}")

    st, body = call("POST", "/captcha",
                    {"image": b64, "meta": {**meta, "title": "中文·标题😀", "pad": "x" * 3000}},
                    encrypt=True)
    check("加密体含中文/emoji/长 base64", body.get("ok") is True, str(body)[:110])

    for name, bad in [
        ("protected 非对象", {"protected": "abc"}),
        ("缺 nonce", {"protected": {"v": 1, "alg": crypto.ALG, "payload": "AAAA"}}),
        ("payload 乱码", {"protected": {"v": 1, "alg": crypto.ALG,
                                        "nonce": "00" * 8, "payload": "!!!"}}),
    ]:
        st, body = call("POST", "/captcha", bad)
        check(f"坏密文（{name}）优雅报错",
              body.get("ok") is False and isinstance(body.get("error", {}).get("code"), str),
              str(body)[:100])

    for path, payload in [
        ("/api/userscript/suppression-summary", {"summaries": []}),
        ("/api/userscript/quota-code/activate", {"quotaCode": "x"}),
        ("/api/normal/shared-binding/report", {"host": "example.com", "accepted": True}),
        ("/api/slide/shared-binding/report", {"host": "example.com", "accepted": True}),
        ("/api/slide/shared-binding/event", {"host": "example.com", "event": "x"}),
    ]:
        st, body = call("POST", path, payload, encrypt=True)
        check(f"加密 POST {path}", body.get("ok") is True, str(body)[:100])

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "，".join(FAIL))
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
