# -*- coding: utf-8 -*-
"""对比 灰度 / 形状 / 融合 三种策略，在 gradient 与 textured 两类背景上的命中情况。"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

rng_global = np.random.default_rng(7)


def make_slider(textured: bool, gap_x=180, bg_size=(320, 160), piece=50, darken=True):
    w, h = bg_size
    rng = np.random.default_rng(42)
    if textured:
        base = rng.integers(0, 255, (h // 8, w // 8, 3), dtype=np.uint8)
        base = cv2.resize(base, (w, h), interpolation=cv2.INTER_LINEAR)
        img = Image.fromarray(base).filter(ImageFilter.GaussianBlur(1.2))
        bg_arr = np.array(img)
    else:
        base = np.zeros((h, w, 3), dtype=np.uint8)
        for y in range(h):
            base[y, :, :] = (40 + y, 80, 200 - y)
        base = (base.astype(np.int16) + rng.integers(-25, 25, (h, w, 3))).clip(0, 255).astype(np.uint8)
        bg_arr = np.array(Image.fromarray(base).filter(ImageFilter.GaussianBlur(1)))

    gap_y = 55
    cut = bg_arr[gap_y:gap_y + piece, gap_x:gap_x + piece].copy()
    if darken:
        bg_arr[gap_y:gap_y + piece, gap_x:gap_x + piece] = (cut * 0.35).astype(np.uint8)

    canvas = Image.new("RGBA", (piece + 60, piece + 40), (0, 0, 0, 0))
    mask = Image.new("L", (piece, piece), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, piece - 1, piece - 1], radius=8, fill=255)
    canvas.paste(Image.fromarray(cut).convert("RGBA"), (40, 20), mask)
    return Image.fromarray(bg_arr), canvas


def prep(bg_img, piece_img):
    bg = np.array(bg_img)[:, :, ::-1].copy()
    t = np.array(piece_img)
    mask = t[:, :, 3]
    bgr = t[:, :, :3].copy()
    bgr[mask <= 10] = 0
    ys, xs = np.where(mask > 10)
    return bg, bgr[ys.min():ys.max() + 1, xs.min():xs.max() + 1], mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def norm(m):
    m = np.nan_to_num(m, nan=-1, posinf=-1, neginf=-1).astype(np.float32)
    lo, hi = float(m.min()), float(m.max())
    return (m - lo) / (hi - lo) if hi > lo else np.zeros_like(m)


def top5(m, k=5):
    s = m.copy()
    out = []
    for _ in range(k):
        _, mx, _, loc = cv2.minMaxLoc(s)
        out.append(int(loc[0]))
        s[:, max(0, loc[0] - 6):loc[0] + 7] = -1e9
    return out


def evaluate(textured: bool) -> None:
    print("=" * 74)
    print("背景类型：", "textured（有纹理，接近真实）" if textured else "gradient（平滑渐变）")
    ok_g = ok_gb = ok_s = ok_e = 0
    for gap_x in (90, 140, 180, 230):
        bg_img, piece_img = make_slider(textured, gap_x=gap_x)
        bg, piece, mask = prep(bg_img, piece_img)
        bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
        p_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        bg_edge = cv2.Canny(cv2.GaussianBlur(bg_gray, (5, 5), 0), 100, 200)

        m = mask.astype(np.float32) / 255.0
        g_raw = cv2.matchTemplate(bg_gray, p_gray, cv2.TM_CCORR_NORMED, mask=m)
        gb_raw = cv2.matchTemplate(cv2.GaussianBlur(bg_gray, (5, 5), 0), p_gray,
                                   cv2.TM_CCORR_NORMED, mask=m)
        s_raw = cv2.matchTemplate(bg_edge.astype(np.float32), (m * 255).astype(np.float32),
                                  cv2.TM_CCORR_NORMED)
        g = norm(g_raw)
        gb = norm(gb_raw)
        s = norm(s_raw)
        cut = piece.shape[1]
        for arr in (g, gb, s):
            arr[:, :cut] = -1.0
        ens = g + s

        picks = {"gray": top5(g)[0], "gray+blur": top5(gb)[0],
                 "shape": top5(s)[0], "ensemble": top5(ens)[0]}
        ok_g += abs(picks["gray"] - gap_x) <= 4
        ok_gb += abs(picks["gray+blur"] - gap_x) <= 4
        ok_s += abs(picks["shape"] - gap_x) <= 4
        ok_e += abs(picks["ensemble"] - gap_x) <= 4
        print(f"  gap={gap_x:3d} | gray={picks['gray']:4d} gray+blur={picks['gray+blur']:4d} "
              f"shape={picks['shape']:4d} ensemble={picks['ensemble']:4d}")
    print(f"  --> 命中率  gray={ok_g}/4  gray+blur={ok_gb}/4  shape={ok_s}/4  ensemble={ok_e}/4")


if __name__ == "__main__":
    evaluate(False)
    evaluate(True)
