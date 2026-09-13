# -*- coding: utf-8 -*-
"""自检脚本：不启动 HTTP 服务，直接验证 OCR / 滑块 / 存储三条链路。

    python tools/selftest.py
"""
from __future__ import annotations

import io
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app import ocr_engine, settings, slider_engine, store

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 测试图生成
# ---------------------------------------------------------------------------
def make_captcha(text: str = "7B4K", size=(120, 40)) -> bytes:
    """生成一张干净的字符验证码，用于验证 OCR 链路是否通。"""
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    for i, ch in enumerate(text):
        d.text((10 + i * 26, 4), ch, fill=(10, 10, 10), font=font)
    for _ in range(3):  # 加几条干扰线
        d.line([(random.randint(0, size[0]), random.randint(0, size[1])),
                (random.randint(0, size[0]), random.randint(0, size[1]))], fill=(120, 120, 120))
    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_slider(bg_size=(320, 160), piece=50, gap_x=180, gap_y=55, darken=True):
    """生成一张合成滑块图，返回 (背景图PNG, 带alpha拼图块PNG, 期望距离)。"""
    w, h = bg_size
    rng = np.random.default_rng(42)
    base = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):                       # 渐变底
        base[y, :, :] = (40 + y, 80, 200 - y)
    base = (base.astype(np.int16) + rng.integers(-25, 25, (h, w, 3))).clip(0, 255).astype(np.uint8)
    bg = Image.fromarray(base).filter(ImageFilter.GaussianBlur(1))
    bg_arr = np.array(bg)

    # 把缺口处的真实内容抠出来作为"拼图块"
    cut = bg_arr[gap_y:gap_y + piece, gap_x:gap_x + piece].copy()

    if darken:                               # 缺口在原背景上表现为变暗
        bg_arr[gap_y:gap_y + piece, gap_x:gap_x + piece] = (
            cut.astype(np.float32) * 0.35
        ).astype(np.uint8)

    bg_out = Image.fromarray(bg_arr)

    # 拼图块画在一张透明画布上，左侧留白（模拟真实站点）
    canvas = Image.new("RGBA", (piece + 60, piece + 40), (0, 0, 0, 0))
    piece_img = Image.fromarray(cut).convert("RGBA")
    mask = Image.new("L", (piece, piece), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, piece - 1, piece - 1], radius=8, fill=255)
    canvas.paste(piece_img, (40, 20), mask)

    b1, b2 = io.BytesIO(), io.BytesIO()
    bg_out.save(b1, format="PNG")
    canvas.save(b2, format="PNG")
    return b1.getvalue(), b2.getvalue(), float(gap_x)


# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 62)
    print("1. 配置加载")
    cfg = settings.get()
    check("读取 config.json", isinstance(cfg, dict) and "ocr" in cfg)
    check("输出模式", cfg["slider"]["output_mode"] in ("display", "raw"), cfg["slider"]["output_mode"])

    print("\n2. 存储初始化")
    store.init()
    st = store.stats()
    check("SQLite 可写", st.get("enabled") is True, str(st.get("db")))
    store.log_recognition(ip="127.0.0.1", kind="normal", host="selftest.local",
                          result="TEST", confidence=0.9, ok=True, duration_ms=5)
    check("recognition_log 可写", store.stats().get("recognition_log", 0) >= 1)
    check("每日配额自增", store.quota_consume("127.0.0.1") >= 1)

    print("\n3. 普通验证码 OCR")
    check("ddddocr 可用", ocr_engine.available(), ocr_engine.unavailable_reason())
    if ocr_engine.available():
        import base64
        img = make_captcha("7B4K")
        (ROOT / "data").mkdir(exist_ok=True)
        (ROOT / "data" / "sample_captcha.png").write_bytes(img)
        b64 = "data:image/png;base64," + base64.b64encode(img).decode()
        res = ocr_engine.recognize(b64)
        print(f"       识别结果 = {res['value']!r}（原始输出 {res['raw_text']!r}）")
        check("OCR 返回非空结果", bool(res["value"]))
        check("dataURL 前缀被正确剥离", res["engine"].startswith("ddddocr"))

    print("\n4. 算术验证码")
    check("3+4 -> 7", ocr_engine.try_arithmetic("3+4=?") == "7", str(ocr_engine.try_arithmetic("3+4=?")))
    check("12-5 -> 7", ocr_engine.try_arithmetic("12-5") == "7")
    check("8÷2 -> 4", ocr_engine.try_arithmetic("8÷2=") == "4")
    check("普通字符不误判", ocr_engine.try_arithmetic("AB7K") is None)

    print("\n5. 滑块缺口识别")
    check("opencv 可用", slider_engine.available(), slider_engine.unavailable_reason())
    if slider_engine.available():
        (ROOT / "data").mkdir(exist_ok=True)
        all_ok = True
        for gap_x in (90, 180, 230):
            bg, tgt, expect = make_slider(gap_x=gap_x)
            (ROOT / "data" / f"sample_slider_bg_{gap_x}.png").write_bytes(bg)
            (ROOT / "data" / f"sample_slider_piece_{gap_x}.png").write_bytes(tgt)
            r = slider_engine.solve(bg, tgt, background_display_width=320, method="match")
            got = r["distance"]
            good = abs(got - expect) <= 4
            all_ok = all_ok and good
            print(f"       gap={gap_x:3d} 实测 {got:7.2f} 得分 {r['confidence']:.3f} "
                  f"策略 {r['debug']['chosen_method']} {'OK' if good else 'MISMATCH'}")
            if gap_x == 180:
                png = slider_engine.visualize(bg, tgt, r)
                check("可视化输出可用", bool(png))
                if png:
                    (ROOT / "data" / "sample_slider_debug.png").write_bytes(png)
        check("三个缺口位置全部命中（误差<=4px）", all_ok)

        bg, tgt, expect = make_slider(gap_x=180)
        r2 = slider_engine.solve(bg, tgt, background_display_width=640, method="match")
        check("display 模式按宽度换算", abs(r2["distance"] - expect * 2) <= 8,
              f"期望 {expect * 2:.0f}，实测 {r2['distance']:.2f}")
        r3 = slider_engine.solve(bg, tgt, background_display_width=320, method="shape")
        check("shape 强制模式可运行", r3["distance"] > 0, f"x={r3['distance']}")
        r4 = slider_engine.solve(make_slider(gap_x=180, darken=False)[0],
                                 make_slider(gap_x=180, darken=False)[1], 320, "match")
        check("未变暗缺口同样命中", abs(r4["distance"] - 180) <= 4, f"x={r4['distance']}")

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "，".join(FAIL))
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
