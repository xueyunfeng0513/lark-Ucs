import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def test_real_entrypoint_config_and_http_service(tmp_path):
    env = dict(os.environ, APP_ID="cli_smoke", APP_SECRET="smoke-secret-not-real",
               VERIFICATION_TOKEN="smoke-verify", ENCRYPT_KEY="smoke-encrypt",
               JOB_DB=str(tmp_path / "smoke.sqlite3"), ALLOWED_OPEN_IDS="", PYTHONIOENCODING="utf-8")
    checked = subprocess.run([sys.executable, "-m", "excel_counter", "--check-config"],
                             env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert checked.returncode == 0, checked.stderr
    assert "smoke-secret" not in checked.stdout + checked.stderr
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        env["PORT"] = str(sock.getsockname()[1])
    process = subprocess.Popen([sys.executable, "-m", "excel_counter"], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, process.communicate()[0].decode(errors="replace")
            try:
                with opener.open(f"http://127.0.0.1:{env['PORT']}/healthz", timeout=1) as response:
                    assert json.load(response) == {"status": "ok"}
                    break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            raise AssertionError("HTTP service did not start")
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
