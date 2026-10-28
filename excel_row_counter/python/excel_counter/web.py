import sqlite3

from flask import Flask, jsonify, request

from .security import AuthenticationError, decode_event
from .ingress import _text, accept_message


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
            accept_message(body, settings, store)
            return jsonify({})
        except AuthenticationError:
            return jsonify(error="Invalid event authentication"), 403
        except (ValueError, KeyError, TypeError, AttributeError):
            return jsonify(error="Invalid event payload"), 400
        except sqlite3.OperationalError:
            # Do not ACK before durable acceptance; Feishu can retry delivery.
            return jsonify(error="Queue temporarily unavailable"), 503

    return app
