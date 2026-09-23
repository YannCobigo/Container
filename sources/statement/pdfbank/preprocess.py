from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np


@dataclass
class ImageCandidate:
    name: str
    image: np.ndarray


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image.copy()
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def normalize_resolution(image: np.ndarray, source_dpi: float | None, target_dpi: int = 300) -> np.ndarray:
    if not source_dpi or source_dpi <= 0:
        return image
    scale = target_dpi / source_dpi
    if 0.85 <= scale <= 1.20:
        return image
    scale = max(0.5, min(scale, 4.0))
    interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)


def enhance_contrast(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def adaptive_threshold(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11)


def denoise(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)


def sharpen(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
    return cv2.addWeighted(gray, 1.55, blur, -0.55, 0)


def deskew(gray: np.ndarray, max_degrees: float = 12.0) -> np.ndarray:
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if coords.shape[0] < 100:
        return gray
    angle = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) > max_degrees:
        return gray
    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def remove_borders(gray: np.ndarray) -> np.ndarray:
    # Conservative: erase only long components touching page edges.
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = bw.shape[:2]
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
    mask = np.zeros_like(bw)
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        touches = x <= 2 or y <= 2 or x + cw >= w - 2 or y + ch >= h - 2
        long = cw > 0.80 * w or ch > 0.80 * h
        if touches and long and area > 100:
            mask[labels == i] = 255
    out = gray.copy()
    out[mask > 0] = 255
    return out


def perspective_correct(gray: np.ndarray) -> np.ndarray:
    """Conservative page-quad correction; returns input unless a dominant page contour is credible."""
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    h, w = gray.shape[:2]
    page_area = h * w
    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) != 4 or cv2.contourArea(approx) < 0.65 * page_area:
            continue
        pts = approx.reshape(4, 2).astype(np.float32)
        s = pts.sum(axis=1)
        d = np.diff(pts, axis=1).ravel()
        ordered = np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)
        dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        matrix = cv2.getPerspectiveTransform(ordered, dst)
        return cv2.warpPerspective(gray, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    return gray


def generate_candidates(image: np.ndarray, adaptive: bool = True) -> list[ImageCandidate]:
    gray = to_gray(image)
    base = deskew(remove_borders(gray))
    candidates = [ImageCandidate("gray_deskew", base)]
    if not adaptive:
        return candidates
    contrast = enhance_contrast(base)
    candidates.extend(
        [
            ImageCandidate("contrast", contrast),
            ImageCandidate("adaptive_threshold", adaptive_threshold(contrast)),
            ImageCandidate("denoise_sharpen", sharpen(denoise(contrast))),
        ]
    )
    return candidates
