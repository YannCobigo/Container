from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import cv2
import fitz
import numpy as np

from .models import NormalizedPage, Word
from .utils import words_to_lines


class LayoutBackend(Protocol):
    name: str

    def analyze(self, pdf_path: str | Path, page: NormalizedPage) -> list[dict]: ...


class HeuristicLayoutBackend:
    """Deterministic region labeling based on geometry and lexical cues."""

    name = "heuristic"

    def analyze(self, pdf_path: str | Path, page: NormalizedPage) -> list[dict]:
        regions: list[dict] = []
        height = max(1.0, page.height_pt)
        for line in page.lines:
            y_mid = (line.bbox[1] + line.bbox[3]) / 2
            lower = line.text.lower()
            label = "body"
            if y_mid < height * 0.12:
                label = "header"
            elif y_mid > height * 0.92:
                label = "footer"
            if any(k in lower for k in ["statement period", "account number", "account #", "routing"]):
                label = "account_information"
            if any(k in lower for k in ["opening balance", "closing balance", "ending balance", "summary"]):
                label = "summary"
            if any(k in lower for k in ["date", "description", "amount", "debit", "credit", "balance"]):
                label = "table_header"
            regions.append({"label": label, "bbox": list(line.bbox), "text": line.text, "confidence": 0.75})
        return regions


class OnnxLayoutBackend:
    """Optional offline ONNX detector with an explicit, auditable adapter config.

    Two common output contracts are supported:
      * ``xyxy_score_class``: rows are [x0, y0, x1, y1, score, class_id]
      * ``yolo_v8``: tensor is [1, 4 + classes, N] or [1, N, 4 + classes],
        first four values are center-x, center-y, width, height and remaining
        values are class probabilities.

    Coordinates may be ``pixels`` (default) or ``normalized``. The input is
    RGB NCHW float32. Mean/std/scale and thresholds are declared in JSON.
    No model or weights are downloaded at runtime.
    """

    name = "onnx"

    def __init__(self, model_path: str | Path, config_path: str | Path) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("onnxruntime is required for the optional ONNX layout backend") from exc
        self.model_path = Path(model_path)
        self.config_path = Path(config_path)
        if not self.model_path.exists():
            raise FileNotFoundError(self.model_path)
        if not self.config_path.exists():
            raise FileNotFoundError(self.config_path)
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.labels = list(self.config.get("labels", []))
        if not self.labels:
            raise ValueError("ONNX layout config requires a non-empty 'labels' list")
        self.input_width = int(self.config.get("input_width", 1024))
        self.input_height = int(self.config.get("input_height", 1024))
        self.output_format = str(self.config.get("output_format", "xyxy_score_class"))
        self.coord_mode = str(self.config.get("box_coordinates", "pixels"))
        self.confidence_threshold = float(self.config.get("confidence_threshold", 0.35))
        self.iou_threshold = float(self.config.get("iou_threshold", 0.45))
        self.scale = float(self.config.get("scale", 1.0 / 255.0))
        self.mean = np.asarray(self.config.get("mean", [0.0, 0.0, 0.0]), dtype=np.float32).reshape(1, 3, 1, 1)
        self.std = np.asarray(self.config.get("std", [1.0, 1.0, 1.0]), dtype=np.float32).reshape(1, 3, 1, 1)
        self.ort = ort
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        inputs = self.session.get_inputs()
        if not inputs:
            raise ValueError("ONNX model has no inputs")
        self.input_name = str(self.config.get("input_name") or inputs[0].name)
        self.output_index = int(self.config.get("output_index", 0))

    def _render_input(self, pdf_path: str | Path, page_number: int) -> np.ndarray:
        with fitz.open(str(pdf_path)) as doc:
            src = doc[page_number - 1]
            pix = src.get_pixmap(alpha=False)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                image = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
            elif pix.n == 1:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
            else:
                image = image[:, :, :3]
        image = cv2.resize(image, (self.input_width, self.input_height), interpolation=cv2.INTER_AREA)
        tensor = image.astype(np.float32).transpose(2, 0, 1)[None, ...]
        tensor = (tensor * self.scale - self.mean) / np.maximum(self.std, 1e-12)
        return np.ascontiguousarray(tensor, dtype=np.float32)

    def _decode(self, raw: np.ndarray) -> list[tuple[float, float, float, float, float, int]]:
        arr = np.asarray(raw)
        while arr.ndim > 2 and arr.shape[0] == 1:
            arr = arr[0]
        detections: list[tuple[float, float, float, float, float, int]] = []
        if self.output_format == "xyxy_score_class":
            arr = np.asarray(arr).reshape(-1, arr.shape[-1])
            if arr.shape[1] < 6:
                raise ValueError("xyxy_score_class output requires at least 6 columns")
            for row in arr:
                x0, y0, x1, y1, score, cls = row[:6]
                if float(score) >= self.confidence_threshold:
                    detections.append((float(x0), float(y0), float(x1), float(y1), float(score), int(cls)))
        elif self.output_format == "yolo_v8":
            if arr.ndim != 2:
                raise ValueError("yolo_v8 output must reduce to a 2-D tensor")
            expected = 4 + len(self.labels)
            if arr.shape[0] == expected and arr.shape[1] != expected:
                arr = arr.T
            if arr.shape[1] < expected:
                raise ValueError(f"yolo_v8 output needs at least {expected} values per detection")
            for row in arr:
                cx, cy, w, h = map(float, row[:4])
                probs = row[4:4 + len(self.labels)]
                cls = int(np.argmax(probs))
                score = float(probs[cls])
                if score < self.confidence_threshold:
                    continue
                detections.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, score, cls))
        else:
            raise ValueError(f"Unsupported ONNX layout output_format: {self.output_format}")
        return detections

    def _nms(self, dets: list[tuple[float, float, float, float, float, int]]) -> list[tuple[float, float, float, float, float, int]]:
        kept: list[tuple[float, float, float, float, float, int]] = []
        for cls in sorted({d[5] for d in dets}):
            group = [d for d in dets if d[5] == cls]
            boxes = [[d[0], d[1], max(0.0, d[2] - d[0]), max(0.0, d[3] - d[1])] for d in group]
            scores = [d[4] for d in group]
            if not boxes:
                continue
            indices = cv2.dnn.NMSBoxes(boxes, scores, self.confidence_threshold, self.iou_threshold)
            for idx in np.asarray(indices).reshape(-1).tolist() if len(indices) else []:
                kept.append(group[int(idx)])
        return kept

    def analyze(self, pdf_path: str | Path, page: NormalizedPage) -> list[dict]:
        tensor = self._render_input(pdf_path, page.page_number)
        outputs = self.session.run(None, {self.input_name: tensor})
        if self.output_index >= len(outputs):
            raise IndexError("ONNX layout output_index is outside model outputs")
        detections = self._nms(self._decode(outputs[self.output_index]))
        sx = page.width_pt / self.input_width
        sy = page.height_pt / self.input_height
        regions: list[dict] = []
        for x0, y0, x1, y1, score, cls in detections:
            if self.coord_mode == "normalized":
                x0, x1 = x0 * self.input_width, x1 * self.input_width
                y0, y1 = y0 * self.input_height, y1 * self.input_height
            bbox = [
                max(0.0, min(page.width_pt, x0 * sx)),
                max(0.0, min(page.height_pt, y0 * sy)),
                max(0.0, min(page.width_pt, x1 * sx)),
                max(0.0, min(page.height_pt, y1 * sy)),
            ]
            label = self.labels[cls] if 0 <= cls < len(self.labels) else f"class_{cls}"
            regions.append({"label": label, "bbox": bbox, "confidence": round(score, 6), "backend": self.name})
        return sorted(regions, key=lambda r: (r["bbox"][1], r["bbox"][0]))


def normalize_page(
    page_number: int,
    width_pt: float,
    height_pt: float,
    rotation: int,
    kind: str,
    native_words: list[Word],
    ocr_words: list[Word],
    ocr_result=None,
) -> NormalizedPage:
    # Prefer reliable native text. OCR is selected when native content is sparse; this
    # avoids duplicate text on image+OCR PDFs.
    native_chars = sum(len(w.text) for w in native_words)
    ocr_conf = ocr_result.mean_confidence if ocr_result else 0.0
    if native_chars >= 80:
        selected = native_words
        source = "native_pdf_text"
    elif ocr_words and ocr_conf >= 0.35:
        selected = ocr_words
        source = "tesseract_ocr"
    elif native_words:
        selected = native_words
        source = "native_pdf_text_low_density"
    else:
        selected = ocr_words
        source = "tesseract_ocr" if ocr_words else "none"

    lines = words_to_lines(selected, y_tolerance=4.0 if source.startswith("tesseract") else 3.0)
    text = "\n".join(line.text for line in lines)
    return NormalizedPage(
        page_number=page_number,
        width_pt=width_pt,
        height_pt=height_pt,
        rotation=rotation,
        kind=kind,
        selected_source=source,
        native_words=native_words,
        ocr_words=ocr_words,
        words=selected,
        lines=lines,
        text=text,
        ocr=ocr_result,
    )
