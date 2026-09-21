"""本地 GitHub API stub（nightly 通知测试用）：记录全部请求，按需返回既有 issue。

只 stub 真正的外部依赖（api.github.com），HTTP 语义真实（urllib 走 loopback），
测试断言落在"真的发了哪些请求、载荷是什么"。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class GithubStub:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.existing_issues: list[dict] = []
        self.created_issues: list[dict] = []
        self.comments: list[dict] = []
        self.patches: list[dict] = []
        self._server: ThreadingHTTPServer | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                pass

            def _handle(self) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length)) if length else None
                rec = {
                    "method": self.command,
                    "path": parsed.path,
                    "query": parsed.query,
                    "headers": dict(self.headers),
                    "json": payload,
                }
                stub.requests.append(rec)
                if self.command == "GET" and parsed.path.endswith("/issues"):
                    body = stub.existing_issues
                elif self.command == "POST" and parsed.path.endswith("/issues"):
                    stub.created_issues.append(rec)
                    body = {
                        "number": 99,
                        "html_url": "https://github.com/marcas-G/stock/issues/99",
                    }
                elif self.command == "POST" and parsed.path.endswith("/comments"):
                    stub.comments.append(rec)
                    num = parsed.path.split("/issues/")[1].split("/")[0]
                    body = {
                        "html_url": f"https://github.com/marcas-G/stock/issues/{num}#issuecomment-1"
                    }
                elif self.command == "PATCH":
                    stub.patches.append(rec)
                    body = {"number": 99, "state": (payload or {}).get("state")}
                else:
                    body = {}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = _handle
            do_POST = _handle
            do_PATCH = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
