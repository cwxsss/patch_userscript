# -*- coding: utf-8 -*-
"""把油猴脚本里的服务端地址改成你自建的地址。

用法：
    python tools/patch_userscript.py 原始脚本路径 -o 输出路径 --base http://192.168.1.10:7070

脚本里硬编码的后端地址是一串明文常量（形如 `var og="http://115.191.58.84:7070"`），
本工具做两件事：
  1. 优先把 URL 字面量整体替换掉；
  2. 若字面量没找到，再尝试匹配 `var <name>="<url>"` 的赋值形态。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

OLD_URL = "http://115.191.58.84:7070"
# 兼容任意变量名 + 任意 http(s) 裸地址的赋值
ASSIGN_RE = re.compile(r'(var\s+[A-Za-z_$][\w$]*\s*=\s*)(["\'])https?://\d{1,3}(?:\.\d{1,3}){3}:\d+(["\'])')


def patch(text: str, base: str) -> tuple[str, str]:
    base = base.rstrip("/")

    if OLD_URL in text:
        count = text.count(OLD_URL)
        return text.replace(OLD_URL, base), f"替换 URL 字面量 {count} 处"

    replaced, n = ASSIGN_RE.subn(lambda m: f"{m.group(1)}{m.group(2)}{base}{m.group(4)}", text)
    if n:
        return replaced, f"替换服务端赋值 {n} 处（字面量未命中）"

    return text, "未找到服务端地址，脚本可能已更换版本或已被修改"


def main() -> int:
    ap = argparse.ArgumentParser(description="改写油猴脚本的服务端地址")
    ap.add_argument("source", help="原始脚本路径")
    ap.add_argument("-o", "--output", help="输出路径，默认 <原名>.selfhost.user.js")
    ap.add_argument("--base", required=True, help="自建服务地址，例如 http://192.168.1.10:7070")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(f"[x] 找不到文件：{src}", file=sys.stderr)
        return 1

    text = src.read_text(encoding="utf-8", errors="replace")
    patched, note = patch(text, args.base)

    base = args.base.rstrip("/")
    # 顺手补一个 @connect，避免某些脚本管理器拦截跨域请求
    host = re.sub(r"^https?://", "", base).split(":")[0]
    if "@connect" in patched and host not in patched.split("// ==/UserScript==")[0]:
        patched = patched.replace("// @connect      *", f"// @connect      *\n// @connect      {host}", 1)

    out = Path(args.output) if args.output else src.with_suffix(".selfhost.user.js")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(patched, encoding="utf-8")

    print(f"[✓] {note}")
    print(f"[✓] 新地址：{base}")
    print(f"[✓] 已写出：{out}")
    print("    下一步：在 Tampermonkey 里导入这个新文件（先禁用/卸载原脚本）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
