import json
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from excel_counter.config import Settings
from excel_counter.jobs import JobStore
from test_workflow import event, FakeFeishu
from excel_counter.worker import Workers


def test_websocket_config_needs_only_app_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ID", "cli_test")
    monkeypatch.setenv("APP_SECRET", "not-real")
    monkeypatch.delenv("VERIFICATION_TOKEN", raising=False)
    monkeypatch.delenv("ENCRYPT_KEY", raising=False)
    monkeypatch.setenv("JOB_DB", str(tmp_path / "jobs.sqlite3"))
    config = Settings.from_env(transport="websocket")
    assert config.transport == "websocket"
    with pytest.raises(ValueError, match="VERIFICATION_TOKEN"):
        Settings.from_env(transport="webhook")


def test_socket_sdk_event_runs_the_shared_workflow(tmp_path):
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    from excel_counter.socket_entry import enqueue_sdk_event
    config = Settings("cli_test", "not-real", "", "", tmp_path / "jobs.sqlite3", transport="websocket")
    store = JobStore(config.db_path)
    sdk_event = P2ImMessageReceiveV1(event())
    enqueue_sdk_event(sdk_event, config, store)
    enqueue_sdk_event(sdk_event, config, store)
    api = FakeFeishu()
    workers = Workers(config, store, api)
    assert not workers.count_once()
    assert workers.notify_once()
    assert workers.count_once()
    assert workers.notify_once()
    assert "总行数：3" in api.messages[-1][0]
    assert not workers.notify_once()


@pytest.mark.parametrize("change", ["group", "app", "other_app"])
def test_socket_events_obey_scope_and_app_identity(tmp_path, change):
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    from excel_counter.socket_entry import enqueue_sdk_event
    config = Settings("cli_test", "not-real", "", "", tmp_path / "jobs.sqlite3", transport="websocket")
    store = JobStore(config.db_path)
    body = event()
    if change == "group":
        body["event"]["message"]["chat_type"] = "group"
    elif change == "app":
        body["event"]["sender"]["sender_type"] = "app"
    else:
        body["header"]["app_id"] = "other-app"
    enqueue_sdk_event(P2ImMessageReceiveV1(body), config, store)
    assert store.claim(("receipt", "result")) is None


def test_official_dispatcher_registration_accepts_raw_event(tmp_path):
    from excel_counter.socket_entry import build_client
    config = Settings("cli_test", "not-real", "", "", tmp_path / "jobs.sqlite3", transport="websocket")
    store = JobStore(config.db_path)
    client = build_client(config, store)
    try:
        client._event_handler._do_without_validation(json.dumps(event()).encode())
        assert store.claim(("receipt",)) is not None
    finally:
        task = client._cache._cron
        task.cancel()
        task.get_loop().run_until_complete(asyncio.gather(task, return_exceptions=True))


def test_cli_websocket_config_without_webhook_secrets(tmp_path):
    env = dict(os.environ, APP_ID="cli_test", APP_SECRET="not-real",
               VERIFICATION_TOKEN="", ENCRYPT_KEY="", JOB_DB=str(tmp_path / "jobs.sqlite3"),
               PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-m", "excel_counter", "--transport", "websocket", "--check-config"],
                            env=env, capture_output=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", [None, "webhook"])
def test_bootstrap_selects_mode_and_passes_arguments(tmp_path, mode):
    bash = shutil.which("bash") if os.name != "nt" else None
    if os.name == "nt" and shutil.which("git"):
        candidate = Path(shutil.which("git")).resolve().parent.parent / "bin/bash.exe"
        if candidate.exists():
            bash = str(candidate)
    if not bash:
        pytest.skip("bash unavailable")
    stub = tmp_path / "python-stub.sh"
    stub.write_text('#!/usr/bin/env bash\nprintf "%s|%s\\n" "${TRANSPORT-unset}" "$*" >> "$BOOTSTRAP_LOG"\n', encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "bootstrap.log"
    env = dict(os.environ, PYTHON_BIN=stub.as_posix(), BOOTSTRAP_LOG=log.as_posix())
    env.pop("TRANSPORT", None)
    if mode:
        env["TRANSPORT"] = mode
    script = Path(__file__).resolve().parents[1] / "bootstrap.sh"
    result = subprocess.run([bash, script.as_posix(), "--check-config"], env=env,
                            capture_output=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines[0].endswith("|-m pip install -r requirements.txt")
    assert lines[1] == f"{mode or 'websocket'}|-m excel_counter --check-config"
