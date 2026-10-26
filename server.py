"""Loopback-only HTTP server for the local simulation dashboard."""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lab import SimulationLab

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, data_dir):
        super().__init__(address, handler)
        self.lab = SimulationLab(data_dir)
        self.csrf = secrets.token_urlsafe(32)
        self.worker_stop = threading.Event()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True, name="simulation-worker")
        self.worker_thread.start()

    def _worker(self):
        while not self.worker_stop.is_set():
            did_work = self.lab.tick()
            delay = 0.05
            if did_work:
                state = self.lab.public_state()
                active = next((r for r in state["runs"] if r["status"] == "running"), None)
                if active:
                    delay = max(0.01, active["config"]["delay_ms"] / 1000)
            else:
                delay = 0.25
            self.worker_stop.wait(delay)

    def server_close(self):
        self.worker_stop.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    protocol_version = "HTTP/1.1"
    server_version = "SimulationLab/1.0"

    def log_message(self, fmt, *args):
        # Avoid recording account-like data in the console logs.
        return

    def _headers(self, content_type="application/json; charset=utf-8", length=0):
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers(content_type, len(body))
        self.end_headers()
        self.wfile.write(body)

    def _host_allowed(self):
        # The socket is bound exclusively to loopback. Reject Host-based DNS rebinding.
        expected_port = self.server.server_address[1]
        return self.headers.get("Host", "").lower() in {f"127.0.0.1:{expected_port}", f"localhost:{expected_port}"}

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parts = urlsplit(origin)
        return parts.scheme == "http" and parts.netloc.lower() == self.headers.get("Host", "").lower()

    def do_GET(self):
        if not self._host_allowed():
            return self._send(403, {"error": "Chỉ chấp nhận kết nối loopback cục bộ"})
        parsed = urlsplit(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        try:
            if path == "/api/state":
                state = self.server.lab.public_state(query.get("run_id", [None])[0])
                state["csrf_token"] = self.server.csrf
                return self._send(200, state)
            if path == "/api/health":
                return self._send(200, {"ok": True, "simulation_only": True, "model_version": "1.0"})
            if path == "/api/compare":
                return self._send(200, self.server.lab.compare(query.get("left", [""])[0], query.get("right", [""])[0]))
            m = re.fullmatch(r"/api/runs/([0-9a-fA-F-]+)/accounts", path)
            if m:
                return self._send(200, self.server.lab.accounts(m.group(1), query.get("offset", [0])[0], query.get("limit", [50])[0], query.get("q", [""])[0], query.get("status", ["all"])[0]))
            m = re.fullmatch(r"/api/runs/([0-9a-fA-F-]+)/mailboxes/([0-9a-fA-F-]+)", path)
            if m:
                return self._send(200, self.server.lab.mailbox(*m.groups()))
            m = re.fullmatch(r"/api/runs/([0-9a-fA-F-]+)/report\.json", path)
            if m:
                return self._send(200, self.server.lab.report(m.group(1)))
            m = re.fullmatch(r"/api/runs/([0-9a-fA-F-]+)/events\.csv", path)
            if m:
                data = self.server.lab.events_csv(m.group(1)).encode("utf-8-sig")
                return self._send(200, data, "text/csv; charset=utf-8")
            m = re.fullmatch(r"/api/runs/([0-9a-fA-F-]+)/accounts\.csv", path)
            if m:
                data = self.server.lab.accounts_csv(m.group(1)).encode("utf-8-sig")
                return self._send(200, data, "text/csv; charset=utf-8")
            if path.startswith("/api/"):
                return self._send(404, {"error": "Không tìm thấy API nội bộ"})
            file = "index.html" if path in {"/", "/index.html"} else path.removeprefix("/")
            if file not in {"index.html", "app.js", "styles.css"}:
                return self._send(404, {"error": "Không tìm thấy tệp"})
            data = (STATIC / file).read_bytes()
            return self._send(200, data, mimetypes.guess_type(file)[0] + ("; charset=utf-8" if file.endswith((".html", ".js", ".css")) else ""))
        except KeyError as exc:
            return self._send(404, {"error": str(exc).strip("'")})
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})

    def do_POST(self):
        if not self._host_allowed():
            return self._send(403, {"error": "Chỉ chấp nhận kết nối loopback cục bộ"})
        if not self._same_origin():
            return self._send(403, {"error": "Từ chối yêu cầu khác nguồn"})
        if not secrets.compare_digest(self.headers.get("X-Lab-CSRF", ""), self.server.csrf):
            return self._send(403, {"error": "Thiếu mã bảo vệ phiên cục bộ. Tải lại trang."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 1_000_000:
                return self._send(413, {"error": "Nội dung yêu cầu quá lớn"})
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Nội dung yêu cầu phải là object JSON")
            path = urlsplit(self.path).path
            if path == "/api/runs":
                return self._send(201, self.server.lab.create_run(body))
            m = re.fullmatch(r"/api/runs/([0-9a-fA-F-]+)/(pause|resume|cancel|repeat)", path)
            if not m:
                return self._send(404, {"error": "Không tìm thấy API nội bộ"})
            run_id, action = m.groups()
            if action == "pause":
                result = self.server.lab.pause_run(run_id)
            elif action == "resume":
                result = self.server.lab.resume_run(run_id)
            elif action == "cancel":
                result = self.server.lab.cancel_run(run_id)
            else:
                result = self.server.lab.repeat_run(run_id, body.get("request_id", ""))
            return self._send(200, result)
        except json.JSONDecodeError:
            return self._send(400, {"error": "JSON không hợp lệ"})
        except KeyError as exc:
            return self._send(404, {"error": str(exc).strip("'")})
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except (TypeError, OverflowError) as exc:
            return self._send(400, {"error": f"Dữ liệu không hợp lệ: {exc}"})


def serve(host="127.0.0.1", port=8765, data_dir=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Máy chủ chỉ được phép bind vào loopback 127.0.0.1")
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    server = LocalServer(("127.0.0.1", port), Handler, data_dir)
    print(f"Phòng thử nghiệm mô phỏng nội bộ: http://127.0.0.1:{server.server_address[1]}")
    print(f"Cơ sở dữ liệu giả lập: {server.lab.db_path}")
    print("Không kết nối mạng ra ngoài. Nhấn Ctrl+C để dừng.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nĐang dừng máy chủ mô phỏng...")
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dashboard mô phỏng tài khoản và tương tác, chỉ chạy cục bộ.")
    parser.add_argument("--host", default="127.0.0.1", help="Chỉ hỗ trợ 127.0.0.1/localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=None, help="Thư mục SQLite nội bộ (mặc định ./data)")
    args = parser.parse_args()
    serve(args.host, args.port, args.data_dir)
