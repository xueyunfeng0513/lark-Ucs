"""Download child entrypoint: credentials arrive on stdin, never argv or logs."""
import json
import sys
import time
from pathlib import Path

from .feishu import FeishuClient, FeishuError


def main():
    try:
        config = json.load(sys.stdin)
        api = FeishuClient(config["app_id"], config["app_secret"],
                           base_url=config["base_url"], max_file_bytes=config["max_file_bytes"])
        api._token = config["token"]
        api._expires = time.monotonic() + config["remaining"]
        api._download_stream(config["message_id"], config["file_key"], Path(config["path"]))
        print(json.dumps({"ok": True}))
    except FeishuError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        return 1
    except Exception:
        print(json.dumps({"error": "无法下载附件，请稍后重新发送。"}, ensure_ascii=True))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
