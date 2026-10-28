import json
import subprocess
import sys
import threading
import time
from urllib.parse import quote

import requests


class FeishuError(RuntimeError):
    pass


class FeishuClient:
    def __init__(self, app_id, app_secret, *, base_url="https://open.feishu.cn", max_file_bytes=20 * 1024 * 1024,
                 download_timeout=120):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.max_file_bytes = max_file_bytes
        self.download_timeout = download_timeout
        self._token = ""
        self._expires = 0
        self._token_lock = threading.Lock()

    @staticmethod
    def _json(response):
        try:
            body = response.json()
            if response.status_code != 200 or not isinstance(body, dict):
                raise ValueError
            code = int(body.get("code", -1))
            if code != 0:
                raise FeishuError(f"飞书接口返回错误码 {code}，请检查权限或稍后重试。")
            return body
        except (ValueError, TypeError) as exc:
            raise FeishuError("飞书接口返回了无效响应，请稍后重试。") from exc

    def _access_token(self):
        with self._token_lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            with requests.post(self.base_url + "/open-apis/auth/v3/tenant_access_token/internal",
                               json={"app_id": self.app_id, "app_secret": self.app_secret},
                               timeout=(5, 20), allow_redirects=False) as response:
                body = self._json(response)
            token = body.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                raise FeishuError("飞书应用令牌获取失败，请检查应用配置。")
            self._token = token
            self._expires = time.monotonic() + max(0, float(body.get("expire", 0)) - 120)
            return token

    def _request(self, method, path, **kwargs):
        for attempt in range(2):
            token = self._access_token()
            response = requests.request(method, self.base_url + path,
                                        headers={"Authorization": "Bearer " + token},
                                        timeout=(5, 20), allow_redirects=False, **kwargs)
            expired = response.status_code == 401
            if response.headers.get("Content-Type", "").startswith("application/json"):
                try:
                    body = response.json()
                    expired = expired or (isinstance(body, dict) and body.get("code") in (99991663, 99991664))
                except ValueError:
                    pass
            if expired and attempt == 0:
                response.close()
                with self._token_lock:
                    if self._token == token:
                        self._expires = 0
                continue
            return response
        raise FeishuError("飞书身份认证失败。")

    def send_text(self, chat_id, text, message_uuid):
        try:
            with self._request("POST", "/open-apis/im/v1/messages",
                               params={"receive_id_type": "chat_id"},
                               json={"receive_id": chat_id, "msg_type": "text",
                                     "content": json.dumps({"text": text}, ensure_ascii=False),
                                     "uuid": message_uuid}) as response:
                self._json(response)
        except requests.RequestException as exc:
            raise FeishuError("飞书消息发送暂时失败，请稍后重试。") from exc

    def download(self, message_id, file_key, path):
        # Socket read timeouts are not wall-clock deadlines. Terminate a separate
        # process even when a response trickles forever inside one chunk read.
        token, remaining = "", 0
        if self._token_lock.acquire(blocking=False):
            try:
                token = self._token
                remaining = max(0, self._expires - time.monotonic())
            finally:
                self._token_lock.release()
        config = {"app_id": self.app_id, "app_secret": self.app_secret,
                  "base_url": self.base_url, "max_file_bytes": self.max_file_bytes,
                  "token": token, "remaining": remaining, "message_id": message_id,
                  "file_key": file_key, "path": str(path)}
        try:
            result = subprocess.run([sys.executable, "-m", "excel_counter.download_process"],
                                    input=json.dumps(config), capture_output=True,
                                    encoding="utf-8", timeout=self.download_timeout)
        except subprocess.TimeoutExpired as exc:
            raise FeishuError("附件下载超时，请稍后重新发送。") from exc
        try:
            body = json.loads(result.stdout)
        except ValueError as exc:
            raise FeishuError("无法下载附件，请稍后重新发送。") from exc
        if result.returncode != 0 or body.get("error"):
            raise FeishuError(body.get("error", "无法下载附件，请稍后重新发送。"))

    def _download_stream(self, message_id, file_key, path):
        route = f"/open-apis/im/v1/messages/{quote(message_id, safe='')}/resources/{quote(file_key, safe='')}"
        started = time.monotonic()
        try:
            with self._request("GET", route, params={"type": "file"}, stream=True) as response:
                if response.status_code != 200 or response.headers.get("Content-Type", "").startswith("application/json"):
                    raise FeishuError("无法下载附件，请检查机器人读取消息及资源的权限，或重新发送文件。")
                try:
                    content_length = int(response.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                if content_length > self.max_file_bytes:
                    raise FeishuError("文件大小超过 20 MB，请拆分后发送。")
                received = 0
                with path.open("wb") as output:
                    for chunk in response.iter_content(64 * 1024):
                        received += len(chunk)
                        if received > self.max_file_bytes:
                            raise FeishuError("文件大小超过 20 MB，请拆分后发送。")
                        if time.monotonic() - started > 120:
                            raise FeishuError("附件下载超时，请稍后重新发送。")
                        output.write(chunk)
        except requests.RequestException as exc:
            raise FeishuError("无法下载附件，网络暂时不可用，请稍后重新发送。") from exc
