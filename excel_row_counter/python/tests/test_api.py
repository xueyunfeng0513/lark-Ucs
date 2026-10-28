import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from excel_counter.feishu import FeishuClient, FeishuError


@pytest.fixture
def http_api(monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    records = []
    state = {"tokens": 0, "large": False, "fail_business": False, "unauthorized_once": False, "trickle": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, status=200):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append((self.path, dict(self.headers), body))
            if self.path.endswith("/tenant_access_token/internal"):
                state["tokens"] += 1
                self.respond({"code": 0, "msg": "ok", "tenant_access_token": "t-test", "expire": 7200})
            elif state["unauthorized_once"]:
                state["unauthorized_once"] = False
                self.respond({"code": 99991663, "msg": "expired"})
            elif state["fail_business"]:
                self.respond({"code": 230001, "msg": "untrusted diagnostic"})
            else:
                self.respond({"code": 0, "msg": "ok", "data": {"message_id": "om_reply"}})

        def do_GET(self):
            records.append((self.path, dict(self.headers), None))
            if state["trickle"]:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", "60")
                self.end_headers()
                try:
                    for _ in range(60):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            payload = b"x" * 100 if state["large"] else b"workbook-bytes"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", records, state
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_real_http_contract_uses_message_resource_and_stable_uuid(http_api, tmp_path):
    base, records, state = http_api
    api = FeishuClient("cli_test", "not-real", base_url=base)
    api.send_text("oc_test", "已收到", "stable-uuid")
    path = tmp_path / "book.xlsx"
    api.download("om_test", "file_test", path)
    api.send_text("oc_test", "总行数：3", "different-uuid")
    assert state["tokens"] == 1
    assert records[0][2] == {"app_id": "cli_test", "app_secret": "not-real"}
    assert records[1][0] == "/open-apis/im/v1/messages?receive_id_type=chat_id"
    assert records[1][2]["receive_id"] == "oc_test"
    assert records[1][2]["uuid"] == "stable-uuid"
    assert json.loads(records[1][2]["content"]) == {"text": "已收到"}
    assert records[2][0] == "/open-apis/im/v1/messages/om_test/resources/file_test?type=file"
    assert records[2][1]["Authorization"] == "Bearer t-test"
    assert path.read_bytes() == b"workbook-bytes"


def test_oversized_download_is_rejected(http_api, tmp_path):
    base, _, state = http_api
    state["large"] = True
    api = FeishuClient("cli", "secret", base_url=base, max_file_bytes=20)
    with pytest.raises(FeishuError, match="大小"):
        api.download("om_test", "file_test", tmp_path / "large.xlsx")


def test_http_200_business_failure_is_not_success(http_api):
    base, _, state = http_api
    state["fail_business"] = True
    api = FeishuClient("cli", "secret", base_url=base)
    with pytest.raises(FeishuError, match="230001") as error:
        api.send_text("oc", "text", "uuid")
    assert "untrusted diagnostic" not in str(error.value)


def test_expired_token_refresh_keeps_same_message_uuid(http_api):
    base, records, state = http_api
    state["unauthorized_once"] = True
    api = FeishuClient("cli", "secret", base_url=base)
    api.send_text("oc", "hello", "one-uuid")
    sends = [record for record in records if "/im/v1/messages?" in record[0]]
    assert state["tokens"] == 2
    assert [record[2]["uuid"] for record in sends] == ["one-uuid", "one-uuid"]


def test_trickling_download_has_a_hard_deadline(http_api, tmp_path):
    base, _, state = http_api
    state["trickle"] = True
    api = FeishuClient("cli", "secret", base_url=base, download_timeout=1)
    started = time.monotonic()
    with pytest.raises(FeishuError, match="超时"):
        api.download("om", "file", tmp_path / "slow.xlsx")
    assert time.monotonic() - started < 3


def test_download_does_not_wait_for_parent_token_refresh_lock(http_api, tmp_path):
    base, _, _ = http_api
    api = FeishuClient("cli", "secret", base_url=base)
    path = tmp_path / "independent.xlsx"
    with ThreadPoolExecutor(max_workers=1) as executor:
        api._token_lock.acquire()
        try:
            future = executor.submit(api.download, "om", "file", path)
            future.result(timeout=5)
        finally:
            api._token_lock.release()
    assert path.read_bytes() == b"workbook-bytes"
