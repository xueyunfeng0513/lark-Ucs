import json
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request

from .security import AuthenticationError, decode_event


def _text(value, max_length=512):
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError("Missing or invalid event field")
    return value


def create_app(settings, store):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.post("/webhook/event")
    def callback():
        try:
            body = decode_event(request.get_data(), request.headers, settings)
            if body.get("type") == "url_verification":
                return jsonify(challenge=_text(body["challenge"]))
            header = body["header"]
            if header.get("event_type") != "im.message.receive_v1":
                return jsonify({})
            if body.get("schema") != "2.0":
                raise ValueError("Only v2 events are supported")
            sender = body["event"]["sender"]
            message = body["event"]["message"]
            if sender.get("sender_type") != "user" or message.get("chat_type") != "p2p":
                return jsonify({})
            open_id = _text(sender["sender_id"]["open_id"])
            if settings.allowed_open_ids and open_id not in settings.allowed_open_ids:
                return jsonify({})
            if message.get("message_type") != "file":
                return jsonify({})
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
            return jsonify({})
        except AuthenticationError:
            return jsonify(error="Invalid event authentication"), 403
        except (ValueError, KeyError, TypeError, AttributeError):
            return jsonify(error="Invalid event payload"), 400
        except sqlite3.OperationalError:
            # Do not ACK before durable acceptance; Feishu can retry delivery.
            return jsonify(error="Queue temporarily unavailable"), 503

    return app
