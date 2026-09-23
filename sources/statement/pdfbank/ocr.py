from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import re

import cv2
import fitz
import numpy as np
import pytesseract
from pytesseract import Output

from .models import OCRCandidateMetric, OCRPageResult, Word
from .preprocess import generate_candidates
from .utils import clamp01


class OCRBackend(ABC):
    @abstractmethod
    def recognize_page(self, pdf_path: str | Path, page_number: int, preprocessing: str = "adaptive") -> OCRPageResult:
        raise NotImplementedError


class TesseractBackend(OCRBackend):
    def __init__(self, language: str = "eng", dpi: int = 300, psm: int = 6) -> None:
        self.language = language
        self.dpi = dpi
        self.psm = psm

    def _render(self, pdf_path: str | Path, page_number: int) -> tuple[np.ndarray, float, float, float, float]:
        doc = fitz.open(str(pdf_path))
        page = doc[page_number - 1]
        rect = page.rect
        scale = self.dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        width_pt, height_pt = float(rect.width), float(rect.height)
        doc.close()
        return arr, width_pt, height_pt, float(pix.width), float(pix.height)

    @staticmethod
    def _candidate_quality(data: dict[str, list[str]]) -> tuple[float, int, float, float, float]:
        confs: list[float] = []
        tokens: list[str] = []
        for text, conf in zip(data["text"], data["conf"]):
            text = str(text).strip()
            try:
                c = float(conf)
            except Exception:
                c = -1
            if text and c >= 0:
                tokens.append(text)
                confs.append(c / 100.0)
        mean = sum(confs) / len(confs) if confs else 0.0
        joined = "".join(tokens)
        alnum_ratio = sum(ch.isalnum() for ch in joined) / max(1, len(joined))
        financial = sum(bool(re.search(r"\d|[$€£]|(?:CR|DR)$", t, re.I)) for t in tokens) / max(1, len(tokens))
        token_bonus = min(len(tokens), 120) / 120.0
        score = clamp01(mean * 0.58 + alnum_ratio * 0.17 + financial * 0.15 + token_bonus * 0.10)
        return mean, len(tokens), alnum_ratio, financial, score

    def recognize_page(self, pdf_path: str | Path, page_number: int, preprocessing: str = "adaptive") -> OCRPageResult:
        image, width_pt, height_pt, pix_w, pix_h = self._render(pdf_path, page_number)
        candidates = generate_candidates(image, adaptive=preprocessing == "adaptive")
        config = f"--psm {self.psm} --oem 1"
        scored: list[tuple[float, str, dict[str, list[str]], OCRCandidateMetric]] = []
        for candidate in candidates:
            data = pytesseract.image_to_data(candidate.image, lang=self.language, config=config, output_type=Output.DICT)
            mean, n, alnum, financial, score = self._candidate_quality(data)
            metric = OCRCandidateMetric(
                name=candidate.name,
                mean_confidence=round(mean, 4),
                token_count=n,
                alnum_ratio=round(alnum, 4),
                financial_token_ratio=round(financial, 4),
                score=round(score, 4),
            )
            scored.append((score, candidate.name, data, metric))
        scored.sort(key=lambda x: x[0], reverse=True)
        _score, best_name, best, _metric = scored[0]

        words: list[Word] = []
        sx = width_pt / pix_w
        sy = height_pt / pix_h
        conf_values: list[float] = []
        for i, raw in enumerate(best["text"]):
            text = str(raw).strip()
            try:
                conf = max(0.0, float(best["conf"][i]) / 100.0)
            except Exception:
                conf = 0.0
            if not text or conf <= 0:
                continue
            x, y = float(best["left"][i]), float(best["top"][i])
            w, h = float(best["width"][i]), float(best["height"][i])
            words.append(
                Word(
                    text=text,
                    bbox=(x * sx, y * sy, (x + w) * sx, (y + h) * sy),
                    confidence=conf,
                    source="tesseract_ocr",
                    block_id=int(best.get("block_num", [0] * len(best["text"]))[i]),
                    line_id=int(best.get("line_num", [0] * len(best["text"]))[i]),
                )
            )
            conf_values.append(conf)
        text = " ".join(w.text for w in words)
        return OCRPageResult(
            page_number=page_number,
            backend="tesseract",
            preprocessing=best_name,
            words=words,
            text=text,
            mean_confidence=round(sum(conf_values) / len(conf_values), 4) if conf_values else 0.0,
            candidates=[item[3] for item in scored],
        )


class OCRService:
    def __init__(self, backend: OCRBackend | None = None) -> None:
        self.backends = {"tesseract": backend or TesseractBackend()}

    def ocr(self, pdf_path: str | Path, page_number: int, engine: str = "tesseract", preprocessing: str = "adaptive") -> OCRPageResult:
        if engine not in self.backends:
            raise ValueError(f"Unknown OCR engine: {engine}")
        return self.backends[engine].recognize_page(pdf_path, page_number, preprocessing)
