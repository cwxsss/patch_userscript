# -*- coding: utf-8 -*-
"""滑块验证码缺口识别引擎。

实测结论（见 tools/slider_experiment.py，合成样本 4 个缺口位置 × 2 类背景）：
  1. **灰度 + alpha 掩码的归一化互相关**（TM_CCORR_NORMED）最准，两类背景均 4/4。
     原因：多数站点的"缺口"是拼图块内容变暗/降饱和的结果，
     而归一化互相关对整体亮度缩放天然不敏感 —— 变暗不影响匹配位置。
  2. 灰度图**不能先做高斯模糊**：平滑背景上会抹掉缺口纹理，
     产生大量并列峰值并选错位置（实测命中率 4/4 -> 0/4）。模糊只用于 Canny。
  3. 边缘 + 掩码组合在 OpenCV 里会因 0/0 产生 inf，结果不可用（已规避）。
  4. 纯形状匹配（拼图块轮廓 vs 背景边缘）在"缺口内容与拼图块不一致"时更可靠，
     作为灰度法的兜底。

默认 method=auto：先跑灰度法，得分低于 gray_score_gate 时切形状法。
所有参数都在 config.json 的 slider 段，可针对具体站点微调。
"""
from __future__ import annotations

from typing import Any

import numpy as np

try:
    import cv2
except Exception as exc:  # pragma: no cover
    cv2 = None  # type: ignore
    _CV_ERROR = str(exc)
else:
    _CV_ERROR = ""

from . import settings

_METHOD_ALIASES = {
    "": "auto",
    "match": "auto",
    "auto": "auto",
    "gray": "gray",
    "grayscale": "gray",
    "shape": "shape",
    "edge": "shape",
    "edges": "shape",
}


def available() -> bool:
    return cv2 is not None


def unavailable_reason() -> str:
    return _CV_ERROR or "opencv-python 未安装"


def _decode(data: bytes) -> np.ndarray:
    if cv2 is None:
        raise RuntimeError(f"图像库不可用：{unavailable_reason()}")
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("图片解码失败（格式不支持或数据损坏）")
    return img


def _odd(value, minimum: int = 1) -> int:
    v = max(minimum, int(value or minimum))
    return v + 1 if v % 2 == 0 else v


def _extract_target(tgt: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """返回 (拼图块 BGR, alpha 掩码或 None)，已裁剪到实体外接矩形。"""
    mask: np.ndarray | None = None
    if tgt.ndim == 3 and tgt.shape[2] == 4:
        mask = tgt[:, :, 3].copy()
        bgr = tgt[:, :, :3].copy()
        bgr[mask <= 10] = 0
    elif tgt.ndim == 3:
        bgr = tgt.copy()
    else:
        bgr = cv2.cvtColor(tgt, cv2.COLOR_GRAY2BGR)

    if mask is not None and int(mask.max()) > 10:
        ys, xs = np.where(mask > 10)
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        return bgr[y0:y1, x0:x1], mask[y0:y1, x0:x1]
    return bgr, None


def _sanitize(res: np.ndarray, cut: int) -> np.ndarray:
    """清掉 nan/inf（TM_CCORR_NORMED 的 0/0 会产生），并抑制左侧初始位置。"""
    clean = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0).astype(np.float32)
    if cut > 0 and cut < clean.shape[1]:
        clean[:, :cut] = -1.0
    return clean


def _peak(score: np.ndarray) -> tuple[int, int, float]:
    _, best, _, loc = cv2.minMaxLoc(score)
    return int(loc[0]), int(loc[1]), float(best)


def _score_gray(bg_gray, piece_gray, mask, cut):
    if mask is not None:
        m = (mask.astype(np.float32) / 255.0)
        res = cv2.matchTemplate(bg_gray, piece_gray, cv2.TM_CCORR_NORMED, mask=m)
    else:
        res = cv2.matchTemplate(bg_gray, piece_gray, cv2.TM_CCOEFF_NORMED)
    return _peak(_sanitize(res, cut))


def _score_shape(bg_edge, mask, cut):
    """拼图块轮廓 vs 背景边缘。需要 alpha 掩码。"""
    m = (mask.astype(np.float32) / 255.0) * 255.0
    res = cv2.matchTemplate(bg_edge.astype(np.float32), m.astype(np.float32), cv2.TM_CCORR_NORMED)
    return _peak(_sanitize(res, cut))


def solve(
    background: bytes,
    target: bytes,
    background_display_width: float = 0,
    method: str = "match",
) -> dict:
    """求解滑块缺口。

    返回 distance / rawDistance / modelDistance / confidence 等字段。
    """
    cfg = settings.get()["slider"]
    raw_bg = _decode(background)
    if raw_bg.ndim == 2:
        bg = cv2.cvtColor(raw_bg, cv2.COLOR_GRAY2BGR)
        bg_gray = raw_bg
    else:
        bg = raw_bg
        bg_gray = cv2.cvtColor(raw_bg, cv2.COLOR_BGR2GRAY)

    bg_h, bg_w = bg.shape[:2]
    piece, piece_mask = _extract_target(_decode(target))
    ph, pw = piece.shape[:2]
    if pw <= 0 or ph <= 0:
        raise ValueError("拼图块尺寸为 0")
    if pw >= bg_w or ph > bg_h:
        raise ValueError(f"拼图块({pw}x{ph}) 不小于背景图({bg_w}x{bg_h})，参数有误")

    has_alpha = piece_mask is not None
    k = _odd(cfg.get("blur_ksize", 5))
    # 注意：灰度互相关必须用「未模糊」的灰度图。
    # 实测（tools/slider_experiment.py）在平滑渐变背景上，先模糊会让缺口纹理消失，
    # 产生大量并列峰值导致选错位置（命中率 4/4 -> 0/4）。
    # 模糊只用于形状法的 Canny。
    bg_gray_blur = cv2.GaussianBlur(bg_gray, (k, k), 0) if k > 1 else bg_gray
    piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)

    # 左侧抑制：有 alpha（拼图块是独立小图）时几乎必然会误匹配到起点
    ratio = cfg.get("suppress_left_ratio", 1.0)
    if ratio is None:
        ratio = 1.0 if has_alpha else 0.0
    cut = int(pw * float(ratio))

    requested = _METHOD_ALIASES.get(str(cfg.get("method") or method).strip().lower())
    if requested is None:
        requested = _METHOD_ALIASES.get(str(method or "").strip().lower(), "auto")

    lo = int(cfg.get("edge_low", 100))
    hi = int(cfg.get("edge_high", 200))
    gate = float(cfg.get("gray_score_gate", 0.5))

    trials: list[dict] = []

    gray_peak = None
    if requested in ("auto", "gray"):
        try:
            gx, gy, gs = _score_gray(bg_gray, piece_gray, piece_mask, cut)
            gray_peak = {"name": "gray", "x": gx, "y": gy, "score": gs}
            trials.append(gray_peak)
        except Exception as exc:
            trials.append({"name": "gray", "error": str(exc)})

    shape_peak = None
    if requested in ("auto", "shape") and has_alpha:
        try:
            bg_edge = cv2.Canny(bg_gray_blur, lo, hi)
            sx, sy, ss = _score_shape(bg_edge, piece_mask, cut)
            shape_peak = {"name": "shape", "x": sx, "y": sy, "score": ss}
            trials.append(shape_peak)
        except Exception as exc:
            trials.append({"name": "shape", "error": str(exc)})

    # 选择策略
    chosen = None
    if requested == "gray":
        chosen = gray_peak
    elif requested == "shape":
        chosen = shape_peak
    else:  # auto
        if gray_peak and gray_peak["score"] >= gate:
            chosen = gray_peak
        elif shape_peak:
            chosen = shape_peak
        else:
            chosen = gray_peak or shape_peak

    if chosen is None:
        raise ValueError("滑块求解失败：所有匹配策略均未产出有效结果")

    raw_distance = int(chosen["x"])
    if raw_distance < int(cfg.get("min_distance", 10)):
        raise ValueError(f"缺口位置异常（x={raw_distance}），请调整 slider 参数")

    offset = float(cfg.get("offset", 0) or 0)
    mode = str(cfg.get("output_mode", "display")).lower()
    dw = float(background_display_width or 0)
    if mode == "display" and dw > 0 and abs(dw - bg_w) > 1:
        distance = (raw_distance + offset) * (dw / bg_w)
    else:
        distance = raw_distance + offset
    distance = round(max(0.0, float(distance)), 2)

    debug: dict[str, Any] = {
        "background_size": [bg_w, bg_h],
        "piece_size": [pw, ph],
        "piece_has_alpha": has_alpha,
        "requested_method": requested,
        "chosen_method": chosen["name"],
        "match_point": [raw_distance, int(chosen["y"])],
        "score": round(float(chosen["score"]), 4),
        "suppress_cut": cut,
        "output_mode": mode,
        "display_width": dw or bg_w,
        "trials": trials,
    }

    return {
        "distance": distance,
        "raw_distance": float(raw_distance),
        "model_distance": float(raw_distance),
        "confidence": round(float(max(0.0, min(1.0, chosen["score"]))), 4),
        "display_width": dw or bg_w,
        "solver_engine_label": f"opencv-{chosen['name']}-match",
        "debug": debug,
    }


def visualize(background: bytes, target: bytes, result: dict) -> bytes | None:
    """把匹配框画到背景图上，返回 PNG，用于调参。"""
    if cv2 is None:
        return None
    try:
        bg = _decode(background)
        if bg.ndim == 2:
            bg = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
        piece, _ = _extract_target(_decode(target))
        ph, pw = piece.shape[:2]
        mx, my = result.get("debug", {}).get("match_point", [0, 0])
        cv2.rectangle(bg, (int(mx), int(my)), (int(mx) + pw, int(my) + ph), (0, 0, 255), 2)
        ok, buf = cv2.imencode(".png", bg)
        return buf.tobytes() if ok else None
    except Exception:
        return None
