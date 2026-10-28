import base64
import hashlib
import json
import time
from dataclasses import replace

import openpyxl
import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from excel_counter.config import Settings
from excel_counter.jobs import JobStore
from excel_counter.web import create_app
from excel_counter.worker import Workers


@pytest.fixture
def settings(tmp_path):
    return Settings(app_id="cli_test", app_secret="not-real", verification_token="verify-test",
                    encrypt_key="encrypt-test", db_path=tmp_path / "jobs.sqlite3")


def event(message_id="om_123", event_id="ev_123", filename="orders.xlsx"):
    return {"schema": "2.0", "header": {"event_id": event_id,
            "event_type": "im.message.receive_v1", "token": "verify-test", "app_id": "cli_test"},
            "event": {"sender": {"sender_type": "user", "sender_id": {"open_id": "ou_test"}},
                      "message": {"message_id": message_id, "chat_id": "oc_test", "chat_type": "p2p",
                                  "message_type": "file", "content": json.dumps({"file_key": "file_test", "file_name": filename})}}}


def signed_post(client, payload, encrypted=False, timestamp=None):
    raw = json.dumps(payload).encode()
    if encrypted:
        iv = b"1234567890123456"
        cipher = AES.new(hashlib.sha256(b"encrypt-test").digest(), AES.MODE_CBC, iv)
        raw = json.dumps({"encrypt": base64.b64encode(iv + cipher.encrypt(pad(raw, 16))).decode()}).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    nonce = "test-nonce"
    signature = hashlib.sha256((timestamp + nonce + "encrypt-test").encode() + raw).hexdigest()
    return client.post("/webhook/event", data=raw, content_type="application/json",
                       headers={"X-Lark-Request-Timestamp": timestamp, "X-Lark-Request-Nonce": nonce,
                                "X-Lark-Signature": signature})


@pytest.mark.parametrize("encrypted", [False, True])
def test_challenge_and_encrypted_events(settings, encrypted):
    store = JobStore(settings.db_path)
    client = create_app(settings, store).test_client()
    response = signed_post(client, {"type": "url_verification", "token": "verify-test", "challenge": "hello"}, encrypted)
    assert response.status_code == 200
    assert response.json == {"challenge": "hello"}
    assert signed_post(client, event(), encrypted).status_code == 200
    assert store.claim(("receipt",)) is not None


def test_forged_stale_wrong_token_or_app_does_not_enqueue(settings):
    store = JobStore(settings.db_path)
    client = create_app(settings, store).test_client()
    assert client.post("/webhook/event", json=event()).status_code == 403
    assert signed_post(client, event(), timestamp=int(time.time()) - 1000).status_code == 403
    bad = event()
    bad["header"]["token"] = "wrong"
    assert signed_post(client, bad).status_code == 403
    bad["header"]["token"] = "verify-test"
    bad["header"]["app_id"] = "other"
    assert signed_post(client, bad).status_code == 403
    assert store.claim(("receipt", "result", "count")) is None


def test_retries_dedupe_by_message_even_with_new_event_id(settings):
    store = JobStore(settings.db_path)
    client = create_app(settings, store).test_client()
    for item in (event(), event(), event(event_id="ev_new")):
        assert signed_post(client, item).status_code == 200
    first = store.claim(("receipt",))
    assert first is not None
    assert store.claim(("receipt",)) is None


@pytest.mark.parametrize("change", ["group", "bot", "unauthorized"])
def test_ignore_out_of_scope_messages(settings, change):
    settings = replace(settings, allowed_open_ids=("ou_allowed",) if change == "unauthorized" else ())
    store = JobStore(settings.db_path)
    client = create_app(settings, store).test_client()
    body = event()
    if change == "group":
        body["event"]["message"]["chat_type"] = "group"
    if change == "bot":
        body["event"]["sender"]["sender_type"] = "app"
    assert signed_post(client, body).status_code == 200
    assert store.claim(("receipt", "result")) is None


def test_unsupported_file_and_malformed_content(settings):
    store = JobStore(settings.db_path)
    client = create_app(settings, store).test_client()
    assert signed_post(client, event(filename="report.pdf")).status_code == 200
    item = store.claim(("result",))
    assert ".xlsx" in item["result_text"]
    broken = event("om_bad", "ev_bad")
    broken["event"]["message"]["content"] = "not-json"
    assert signed_post(client, broken).status_code == 400


def test_expired_claim_is_recovered_but_stale_owner_cannot_finish(settings):
    store = JobStore(settings.db_path)
    store.enqueue("ev", "om", "oc", "file", "orders.xlsx")
    first = store.claim(("receipt",), now=100, lease_seconds=5)
    reopened = JobStore(settings.db_path)
    assert reopened.claim(("receipt",), now=104) is None
    second = reopened.claim(("receipt",), now=106)
    assert second["id"] == first["id"]
    assert not store.advance(first, "count")
    assert reopened.advance(second, "count")


def test_notification_retries_are_bounded_and_result_is_preserved(settings):
    store = JobStore(settings.db_path)
    store.enqueue("ev", "om", "oc", "file", "orders.xlsx", "总行数：42")
    for attempt in range(5):
        job = store.claim(("result",), now=1000 + attempt * 100)
        assert job["result_text"] == "总行数：42"
        store.retry(job, "NetworkError", now=1000 + attempt * 100)
    assert store.claim(("result",), now=10000) is None
    with store.connection() as conn:
        saved = conn.execute("SELECT phase, result_text FROM jobs").fetchone()
        assert tuple(saved) == ("notification_failed", "总行数：42")


class FakeFeishu:
    def __init__(self):
        self.messages = []
        self.downloads = 0
        self.fail_final = False
        self.corrupt = False

    def send_text(self, chat_id, text, message_uuid):
        assert chat_id == "oc_test"
        if self.fail_final and "总行数" in text:
            raise OSError("simulated network failure")
        self.messages.append((text, message_uuid))

    def download(self, message_id, file_key, path):
        assert self.messages and "收到" in self.messages[0][0]
        assert message_id == "om_123" and file_key == "file_test"
        self.downloads += 1
        if self.corrupt:
            path.write_bytes(b"corrupt")
            return
        book = openpyxl.Workbook()
        book.active.append(["表头"])
        book.active.append(["第一条"])
        book.active.append(["第二条"])
        book.save(path)


def test_complete_workflow_and_retry_notification_without_recount(settings):
    store = JobStore(settings.db_path)
    client = create_app(settings, store).test_client()
    api = FakeFeishu()
    workers = Workers(settings, store, api)
    assert signed_post(client, event()).status_code == 200
    assert not workers.count_once()
    assert workers.notify_once()
    assert "收到" in api.messages[0][0]
    assert workers.count_once()
    api.fail_final = True
    assert workers.notify_once()
    assert not workers.count_once()
    api.fail_final = False
    # A later notifier recovers the persisted pending result, not the download.
    assert workers.notify_once(now=time.time() + 1000)
    assert api.downloads == 1
    assert len(api.messages) == 2
    assert "总行数：3" in api.messages[1][0]
    assert "包含表头" in api.messages[1][0]
    assert api.messages[0][1] != api.messages[1][1]
    assert not workers.notify_once(now=time.time() + 2000)


def test_corrupt_workbook_gets_final_failure(settings):
    store = JobStore(settings.db_path)
    api = FakeFeishu()
    api.corrupt = True
    client = create_app(settings, store).test_client()
    signed_post(client, event())
    worker = Workers(settings, store, api)
    worker.notify_once()
    worker.count_once()
    worker.notify_once()
    assert "无法" in api.messages[-1][0]
    assert "总行数" not in api.messages[-1][0]
