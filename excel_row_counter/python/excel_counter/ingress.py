"""Transport-independent message filtering and durable acceptance."""
import json
from pathlib import Path


def _text(value, max_length=512):
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError("Missing or invalid event field")
    return value


def accept_message(body, settings, store):
    header = body["header"]
    if header.get("app_id") != settings.app_id:
        return
    if header.get("event_type") != "im.message.receive_v1":
        return
    if body.get("schema") != "2.0":
        raise ValueError("Only v2 events are supported")
    sender = body["event"]["sender"]
    message = body["event"]["message"]
    if sender.get("sender_type") != "user" or message.get("chat_type") != "p2p":
        return
    open_id = _text(sender["sender_id"]["open_id"])
    if settings.allowed_open_ids and open_id not in settings.allowed_open_ids:
        return
    if message.get("message_type") != "file":
        return
    content = json.loads(message["content"])
    if not isinstance(content, dict):
        raise ValueError("Invalid file content")
    filename = _text(content["file_name"])
    file_key = _text(content["file_key"])
    hint = None
    if Path(filename).suffix.lower() not in (".xlsx", ".xls"):
        hint = "请发送 .xlsx 或 .xls 格式的 Excel 文件。"
    store.enqueue(_text(header["event_id"]), _text(message["message_id"]),
                  _text(message["chat_id"]), file_key, filename, hint)
