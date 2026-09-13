# -*- coding: utf-8 -*-
"""校验 app/crypto.py 与油猴脚本的加密层是否一致。

脚本对**非本机**后端会把请求加密（详见 app/crypto.py 顶部注释）。
本工具做两件事：

  1. 纯 Python 往返自测（不需要 node，随时可跑）
  2. 若给了 `--js-script`，就从真实脚本里切出原版 `An/mt/Jr/Z/be` 函数，
     用 Node 跑一遍真加密，再让 Python 解密比对 —— 这是权威验证，
     能确认我们对算法的理解没有偏差。

用法：
    python tools/crypto_check.py
    python tools/crypto_check.py --js-script D:/桌面/验证码识别
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import crypto  # noqa: E402

# 从脚本原文件里切函数用的偏移（v7.95）。切出来会先校验首尾，不对就跳过。
CUTS = [
    ("primitives", 120302, 120952, "function An("),   # An / mt / Jr
    ("Z", 120952, 121407, "function Z("),
    ("be", 121407, 121632, "function be("),
]

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def py_roundtrip() -> None:
    print("1. Python 自身加解密往返")
    cases = [
        ("空对象", {}),
        ("只有 image", {"image": "data:image/png;base64,iVBORw0KGgo="}),
        ("中文 + 特殊符号", {"host": "www.nicept.net", "title": "登录 / 注册", "s": "<>&\"'"}),
        ("emoji", {"s": "😀🎉🇨🇳"}),
        ("嵌套结构", {"a": [[1, 2], [3, {"b": None, "c": True, "d": 1.5}]]}),
        ("超长 payload", {"image": "x" * 20000}),
        ("负数/大数", {"n": -1, "big": 2**53 - 1}),
    ]
    for name, obj in cases:
        z = crypto.encrypt_payload(obj, nonce="0011223344556677")
        back = crypto.unwrap_protected({"protected": z})
        check(name, back == obj, "" if back == obj else str(back)[:80])

    print("2. ?enc= 编码往返")
    obj = {"host": "www.nicept.net", "pathname": "/login.php", "n": 1}
    token = crypto.encode_query_token(obj, nonce="aabbccddeeff0011")
    back = crypto.decrypt_query(token)
    check("encode_query_token -> decrypt_query", back == obj, str(back)[:80])
    check("token 是 base64url 无 padding",
          all(c.isalnum() or c in "-_" for c in token) and "=" not in token)

    print("3. 容错（脏数据不能把服务打挂）")
    check("protected 非对象", crypto.unwrap_protected({"protected": "abc"}) is None)
    check("缺 nonce", crypto.decrypt_payload("", "AAAA") is None)
    check("缺 payload", crypto.decrypt_payload("0011", "") is None)
    check("payload 非法 base64", crypto.decrypt_payload("0011", "!!!") is None)
    check("乱码解不出合法 JSON",
          crypto.decrypt_payload("0011", "QUJD") is None)
    check("enc 乱码", crypto.decrypt_query("!!!!") is None)
    check("enc 空串", crypto.decrypt_query("") is None)
    check("明文对象不误判", crypto.unwrap_protected({"image": "x"}) is None)


def js_crosscheck(script: Path, node: str) -> None:
    print(f"\n4. 与脚本原版函数交叉验证  ({script})")
    if not script.exists():
        print(f"  [skip] 找不到脚本：{script}")
        return
    if not shutil.which(node) and not Path(node).exists():
        print(f"  [skip] 找不到 node：{node}")
        return

    text = script.read_text(encoding="utf-8", errors="replace")
    blocks = {}
    for name, a, b, head in CUTS:
        blk = text[a:b]
        if not blk.startswith(head):
            print(f"  [!] 偏移已失效，切不出 {name}（脚本版本可能变了），跳过交叉验证")
            return
        blocks[name] = blk

    obj = {
        "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
        "quotaCode": "",
        "meta": {"href": "https://www.nicept.net/login.php", "host": "www.nicept.net",
                 "title": "登录 / 注册", "deviceId": "dev-测试-123", "zoom": 1.5},
        "nested": [1, 2, {"k": "中文·特殊符号<>&", "emoji": "😀"}],
    }

    js = f"""
const Ve = "ctw_2026_slide_mask_v1";
{blocks['primitives']}
{blocks['Z']}
{blocks['be']}
const obj = {json.dumps(obj, ensure_ascii=False)};
const z = Z(obj);
const encTok = be(obj);
console.log(JSON.stringify({{ z, encTok }}));
"""
    tmp = ROOT / "data" / "_crypto_check.js"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(js, encoding="utf-8")
    try:
        r = subprocess.run([node, str(tmp)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if r.returncode != 0:
        check("Node 执行脚本原版加密", False, (r.stderr or r.stdout)[:200])
        return

    got = json.loads(r.stdout.strip().splitlines()[-1])
    z = got["z"]
    print(f"       JS 产出 nonce={z['nonce']} alg={z['alg']}")

    check("解 JS 的 {protected:...}（POST 方向）",
          crypto.unwrap_protected({"protected": z}) == obj)
    check("解 JS 的 enc token（GET 方向）", crypto.decrypt_query(got["encTok"]) == obj)

    # 反向：Python 加密，交给脚本原版 Jr() 解
    py_z = crypto.encrypt_payload(obj, nonce="deadbeefcafe0001")
    js2 = f"""
const Ve = "ctw_2026_slide_mask_v1";
{blocks['primitives']}
const out = Jr({json.dumps(py_z, ensure_ascii=False)});
console.log(JSON.stringify(out));
"""
    tmp2 = ROOT / "data" / "_crypto_check2.js"
    tmp2.write_text(js2, encoding="utf-8")
    try:
        r2 = subprocess.run([node, str(tmp2)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
    finally:
        try:
            tmp2.unlink()
        except OSError:
            pass
    if r2.returncode != 0:
        check("脚本原版 Jr() 解 Python 密文", False, (r2.stderr or r2.stdout)[:200])
    else:
        back = json.loads(r2.stdout.strip().splitlines()[-1])
        check("脚本原版 Jr() 解 Python 密文", back == obj)


def main() -> int:
    ap = argparse.ArgumentParser(description="校验加解密层与油猴脚本是否一致")
    ap.add_argument("--js-script", default="", help="原始油猴脚本路径，给了就做 Node 交叉验证")
    ap.add_argument("--node", default="node", help="node 可执行文件路径")
    args = ap.parse_args()

    py_roundtrip()
    if args.js_script:
        js_crosscheck(Path(args.js_script), args.node)

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败：" + "，".join(FAIL))
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
