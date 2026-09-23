from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Iterable, Sequence

from .models import BBox, TextLine, Word

MONEY_RE = re.compile(
    r"(?<![A-Za-z0-9])\(?\s*[-+]?\s*(?:[$€£¥]\s*)?\d{1,3}(?:[ ,.]\d{3})*(?:[.,]\d{2})\)?(?:\s*(?:CR|DR))?(?!\d)",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def union_bbox(boxes: Iterable[BBox]) -> BBox | None:
    values = list(boxes)
    if not values:
        return None
    return (
        min(b[0] for b in values),
        min(b[1] for b in values),
        max(b[2] for b in values),
        max(b[3] for b in values),
    )


def bbox_area(b: BBox | None) -> float:
    if b is None:
        return 0.0
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def iou(a: BBox, b: BBox) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    denom = bbox_area(a) + bbox_area(b) - inter
    return inter / denom if denom else 0.0


def words_to_lines(words: Sequence[Word], y_tolerance: float = 3.2) -> list[TextLine]:
    """Cluster words by baseline/center while preserving spatial coordinates."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: ((w.bbox[1] + w.bbox[3]) / 2.0, w.bbox[0]))
    groups: list[list[Word]] = []
    centers: list[float] = []
    for word in ordered:
        cy = (word.bbox[1] + word.bbox[3]) / 2.0
        best = None
        best_delta = float("inf")
        for idx in range(max(0, len(groups) - 4), len(groups)):
            delta = abs(cy - centers[idx])
            if delta <= y_tolerance and delta < best_delta:
                best, best_delta = idx, delta
        if best is None:
            groups.append([word])
            centers.append(cy)
        else:
            groups[best].append(word)
            centers[best] = sum((w.bbox[1] + w.bbox[3]) / 2.0 for w in groups[best]) / len(groups[best])

    lines: list[TextLine] = []
    for group in groups:
        group = sorted(group, key=lambda w: w.bbox[0])
        text_parts: list[str] = []
        prev = None
        for w in group:
            if prev is not None:
                gap = w.bbox[0] - prev.bbox[2]
                avg_h = max(1.0, (prev.bbox[3] - prev.bbox[1] + w.bbox[3] - w.bbox[1]) / 2)
                if gap > avg_h * 0.20:
                    text_parts.append(" ")
            text_parts.append(w.text)
            prev = w
        conf = sum(w.confidence for w in group) / len(group)
        lines.append(TextLine(text="".join(text_parts).strip(), bbox=union_bbox(w.bbox for w in group), words=group, confidence=conf))
    return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))


def parse_money(raw: str | None) -> tuple[float | None, list[str]]:
    """Parse common bank amount notations without guessing ambiguous malformed values."""
    if raw is None:
        return None, ["missing_amount"]
    original = raw.strip()
    s = original.upper().replace("\u00a0", " ").strip()
    negative = False
    warnings: list[str] = []
    if s.endswith("DR"):
        negative = True
        s = s[:-2].strip()
    elif s.endswith("CR"):
        s = s[:-2].strip()
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    # Banks place signs before or after currency symbols; normalize in either order.
    s = s.strip()
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = re.sub(r"^[\s$€£¥]+", "", s)
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = s.replace(" ", "")

    # Decimal separator inference: if both are present, the final separator is decimal.
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        s = s.replace(thousands, "").replace(decimal, ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 2:
            s = "".join(parts[:-1]).replace(".", "") + "." + parts[-1]
        else:
            s = s.replace(",", "")
    else:
        if s.count(".") > 1:
            parts = s.split(".")
            if len(parts[-1]) == 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                return None, ["ambiguous_decimal_separators"]

    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", s):
        return None, ["malformed_amount"]
    try:
        value = float(s)
    except ValueError:
        return None, ["malformed_amount"]
    if negative:
        value = -abs(value)
    return round(value, 2), warnings


def financial_text_score(text: str) -> float:
    t = text.lower()
    keywords = [
        "statement", "account", "balance", "deposit", "withdrawal", "debit", "credit",
        "transaction", "checking", "savings", "interest", "fee", "amount", "date",
    ]
    hits = sum(1 for k in keywords if k in t)
    money = len(MONEY_RE.findall(text))
    dates = len(DATE_RE.findall(text))
    score = min(1.0, hits / 6.0 * 0.55 + min(money, 8) / 8 * 0.30 + min(dates, 6) / 6 * 0.15)
    return round(score, 4)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    from rapidfuzz.fuzz import ratio
    return ratio(a.lower(), b.lower()) / 100.0
