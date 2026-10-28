import logging
import tempfile
import threading
import uuid
from pathlib import Path

from .excel import WorkbookError, count_rows_isolated
from .feishu import FeishuError

LOG = logging.getLogger(__name__)


def _display(value, limit=150):
    return value.replace("\r", " ").replace("\n", " ")[:limit]


class Workers:
    def __init__(self, settings, store, api):
        self.settings = settings
        self.store = store
        self.api = api
        self.stop = threading.Event()
        self.threads = []

    def notify_once(self, *, now=None):
        item = self.store.claim(("receipt", "result"), now=now)
        if item is None:
            return False
        text = (f"已收到 Excel：{_display(item['filename'])}\n正在统计行数，请稍候。\n任务：{item['id'][:8]}"
                if item["phase"] == "receipt" else item["result_text"])
        message_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.settings.app_id}/{item['id']}/{item['phase']}"))
        try:
            self.api.send_text(item["chat_id"], text, message_uuid)
        except Exception as exc:
            self.store.retry(item, type(exc).__name__, now=now)
            LOG.warning("Notification attempt failed: task=%s attempt=%s type=%s",
                        item["id"], item["attempts"], type(exc).__name__)
        else:
            self.store.advance(item, "count" if item["phase"] == "receipt" else "done")
        return True

    def count_once(self):
        item = self.store.claim(("count",))
        if item is None:
            return False
        filename = _display(item["filename"])
        try:
            with tempfile.TemporaryDirectory(prefix="feishu-excel-") as directory:
                # Never use user-supplied filenames as filesystem paths.
                path = Path(directory) / ("workbook" + Path(item["filename"]).suffix.lower())
                self.api.download(item["message_id"], item["file_key"], path)
                sheets = count_rows_isolated(path)
            details = "\n".join(f"• {_display(name, 60)}：{rows} 行" for name, rows in sheets.items())
            result = (f"Excel 统计完成：{filename}\n总行数：{sum(sheets.values())}\n"
                      f"统计口径：所有工作表非空行合计，包含表头，空白行不计。\n{details}\n任务：{item['id'][:8]}")
        except (WorkbookError, FeishuError) as exc:
            result = f"无法完成 Excel 统计：{filename}\n{exc}\n任务：{item['id'][:8]}"
        except Exception as exc:
            LOG.warning("Count failed: task=%s type=%s", item["id"], type(exc).__name__)
            result = f"无法完成 Excel 统计：{filename}\n请稍后重新发送，或联系管理员。\n任务：{item['id'][:8]}"
        self.store.advance(item, "result", result)
        return True

    def _loop(self, action):
        while not self.stop.is_set():
            try:
                if action():
                    continue
            except Exception as exc:
                LOG.error("Worker iteration failed: type=%s", type(exc).__name__)
            self.stop.wait(0.5)

    def start(self):
        for action in (self.notify_once, self.count_once):
            thread = threading.Thread(target=self._loop, args=(action,), daemon=True)
            thread.start()
            self.threads.append(thread)

    def shutdown(self):
        self.stop.set()
        for thread in self.threads:
            # Allow the bounded download (120s) and parser (45s) to finish and
            # clean their temporary directory on an ordinary graceful stop.
            thread.join(timeout=180)
