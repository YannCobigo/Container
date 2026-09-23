from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse


def serve_review(directory: str | Path, bind: str = "127.0.0.1", port: int = 8765) -> None:
    directory = Path(directory).resolve()
    report = directory / "extraction_report.html"
    annotations = directory / "annotations.jsonl"
    if not report.exists():
        raise FileNotFoundError(report)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, data: bytes):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/extraction_report.html"}:
                self._send(200, "text/html; charset=utf-8", report.read_bytes())
            elif path == "/annotations":
                data = annotations.read_bytes() if annotations.exists() else b""
                self._send(200, "application/x-ndjson", data)
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self):  # noqa: N802
            if urlparse(self.path).path != "/annotations":
                self._send(404, "text/plain", b"not found")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                self._send(400, "text/plain", b"invalid body")
                return
            try:
                obj = json.loads(self.rfile.read(length))
                if not isinstance(obj, dict) or "transaction_id" not in obj:
                    raise ValueError("missing transaction_id")
                with annotations.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
                self._send(201, "application/json", b'{"saved":true}')
            except Exception as exc:
                self._send(400, "application/json", json.dumps({"saved": False, "error": str(exc)}).encode())

        def log_message(self, format, *args):
            # Deliberately do not log query/body content containing financial data.
            print(f"review-server: {self.address_string()} {self.command} {urlparse(self.path).path}")

    server = ThreadingHTTPServer((bind, port), Handler)
    print(f"Review interface: http://{bind}:{port}/")
    server.serve_forever()
