# -*- coding: utf-8 -*-
"""滑块算法调试：对比多种匹配策略在合成样本上的表现。"""
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

from tools.selftest import make_slider


def prep(bg_bytes, tgt_bytes):
    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    tgt = cv2.imdecode(np.frombuffer(tgt_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if bg.ndim == 2:
        bg = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)

    mask = None
    if tgt.ndim == 3 and tgt.shape[2] == 4:
        mask = tgt[:, :, 3]
        bgr = tgt[:, :, :3].copy()
        bgr[mask <= 10] = 0
    else:
        bgr = tgt[:, :, :3] if tgt.ndim == 3 else cv2.cvtColor(tgt, cv2.COLOR_GRAY2BGR)
    if mask is not None and int(mask.max()) > 10:
        ys, xs = np.where(mask > 10)
        bgr = bgr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    else:
        mask = None
    return bg, bgr, mask


def top_candidates(score, k=5):
    s = np.nan_to_num(score, nan=-1e9)
    out = []
    tmp = s.copy()
    for _ in range(k):
        _, mx, _, loc = cv2.minMaxLoc(tmp)
        out.append((int(loc[0]), float(mx)))
        x0 = max(0, loc[0] - 8)
        tmp[:, x0:loc[0] + 9] = -1e9
    return out


def main() -> None:
    for gap_x in (180, 90, 230):
        bg_b, tgt_b, expect = make_slider(gap_x=gap_x)
        bg, piece, mask = prep(bg_b, tgt_b)
        bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
        p_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
        print("=" * 70)
        print(f"期望 x = {expect}  拼图块 {piece.shape[1]}x{piece.shape[0]}  有alpha={mask is not None}")

        c1 = cv2.Canny(cv2.GaussianBlur(bg_gray, (5, 5), 0), 100, 200)
        c2p = cv2.Canny(p_gray, 100, 200)
        print(f"  bg 边缘像素={int(c1.sum()//255)}  拼图块边缘像素={int(c2p.sum()//255)}")

        m = (mask.astype(np.float32) / 255.0) if mask is not None else None

        tests: list[tuple[str, np.ndarray]] = []
        if m is not None:
            tests.append(("edges+MASK CCORR_NORMED",
                          cv2.matchTemplate(c1, c2p, cv2.TM_CCORR_NORMED, mask=m)))
        tests.append(("edges CCOEFF_NORMED", cv2.matchTemplate(c1, c2p, cv2.TM_CCOEFF_NORMED)))
        if m is not None:
            tests.append(("gray+MASK CCORR_NORMED",
                          cv2.matchTemplate(bg_gray, p_gray, cv2.TM_CCORR_NORMED, mask=m)))
            tests.append(("gray+MASK SQDIFF_NORMED",
                          cv2.matchTemplate(bg_gray, p_gray, cv2.TM_SQDIFF_NORMED, mask=m)))
        if m is not None:
            tests.append(("pieceMask vs bgEdges",
                          cv2.matchTemplate(c1.astype(np.float32), (m * 255), cv2.TM_CCORR_NORMED)))
        tests.append(("edges TM_CCORR (no mask)",
                      cv2.matchTemplate(c1.astype(np.float32), c2p.astype(np.float32), cv2.TM_CCORR_NORMED)))
        # Otsu 自动阈值 Canny
        otsu = int(cv2.threshold(cv2.GaussianBlur(bg_gray, (5, 5), 0), 0, 255,
                                 cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
        c1o = cv2.Canny(cv2.GaussianBlur(bg_gray, (5, 5), 0), max(10, otsu * 0.4), otsu)
        if m is not None:
            tests.append((f"edges(otsu={otsu})+MASK CCORR",
                          cv2.matchTemplate(c1o, c2p, cv2.TM_CCORR_NORMED, mask=m)))

        for name, score in tests:
            best_min = "SQDIFF" in name
            cands = top_candidates(-score if best_min else score)
            hit = [c for c in cands if abs(c[0] - expect) <= 6]
            tag = "✓" if hit else " "
            print(f"  {tag} {name:32s} top1_x={cands[0][0]:4d} score={cands[0][1]:.4f} "
                  f"top5={[c[0] for c in cands]}")

        if m is not None:
            for th in (40, 60, 80, 120):
                ci = cv2.Canny(cv2.GaussianBlur(bg_gray, (5, 5), 0), th, th * 2)
                sc = cv2.matchTemplate(ci, c2p, cv2.TM_CCORR_NORMED, mask=m)
                cc = top_candidates(sc)
                hit = [c for c in cc if abs(c[0] - expect) <= 6]
                print(f"    {'✓' if hit else ' '} edge_low={th:3d} -> top1_x={cc[0][0]:4d} "
                      f"score={cc[0][1]:.4f} top5={[c[0] for c in cc]}")
        print()


if __name__ == "__main__":
    main()
