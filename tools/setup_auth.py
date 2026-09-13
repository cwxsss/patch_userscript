# -*- coding: utf-8 -*-
"""生成 / 轮换 API Key，并写进 config.json。

用法：
    python tools/setup_auth.py                 # 生成新密钥并开启鉴权
    python tools/setup_auth.py --show          # 只打印当前配置里的密钥
    python tools/setup_auth.py --key <指定值>   # 用指定密钥
    python tools/setup_auth.py --disable       # 关闭鉴权（恢复裸奔）

做的事：
  1. 生成一个高熵密钥（secrets.token_urlsafe）
  2. 写回 config.json：require_api_key / require_protected_header 置为 true
  3. 把密钥单独落在 .api_key 文件里，方便部署脚本读取（已在 .gitignore 中）

拿到密钥后，用它去重新生成油猴脚本：
    python tools/patch_userscript.py 原脚本 -o 输出.user.js \\
        --base http://<你的地址> --api-key <密钥>
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
KEY_FILE = ROOT / ".api_key"


def load_config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[x] config.json 解析失败：{exc}", file=sys.stderr)
            raise SystemExit(1)
    return {}


def save_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 / 轮换 API Key 并写入 config.json")
    ap.add_argument("--key", default="", help="指定密钥，不给则随机生成")
    ap.add_argument("--show", action="store_true", help="只显示当前密钥，不改配置")
    ap.add_argument("--disable", action="store_true", help="关闭鉴权")
    args = ap.parse_args()

    cfg = load_config()
    auth = cfg.setdefault("auth", {})

    if args.show:
        print(f"require_api_key        : {auth.get('require_api_key')}")
        print(f"require_protected_header: {auth.get('require_protected_header')}")
        print(f"api_key                : {auth.get('api_key') or '(空)'}")
        print(f"admin_key              : {auth.get('admin_key') or '(空，复用 api_key)'}")
        return 0

    if args.disable:
        auth["require_api_key"] = False
        auth["require_protected_header"] = False
        save_config(cfg)
        if KEY_FILE.exists():
            KEY_FILE.unlink()
        print("[✓] 已关闭鉴权（require_api_key=false, require_protected_header=false）")
        return 0

    key = args.key.strip() or secrets.token_urlsafe(32)
    auth["require_api_key"] = True
    auth["require_protected_header"] = True
    auth["api_key"] = key
    save_config(cfg)
    KEY_FILE.write_text(key, encoding="utf-8")

    print("[✓] 鉴权已开启")
    print(f"    require_api_key         = true")
    print(f"    require_protected_header = true")
    print(f"    api_key = {key}")
    print(f"[✓] 密钥已写入 {KEY_FILE.name}（已 gitignore，勿提交）")
    print()
    print("下一步，用它重新生成油猴脚本：")
    print(f"    python tools/patch_userscript.py <原脚本> -o <输出.user.js> \\")
    print(f"        --base http://<你的地址>:7070 --api-key {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
