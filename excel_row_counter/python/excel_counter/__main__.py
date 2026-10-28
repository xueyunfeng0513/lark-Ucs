import argparse
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv
from waitress import create_server

from .config import Settings
from .feishu import FeishuClient
from .jobs import JobStore
from .web import create_app
from .worker import Workers


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser(description="飞书 Excel 行数机器人")
    parser.add_argument("--transport", choices=("webhook", "websocket"),
                        default=os.getenv("TRANSPORT", "webhook"), help="消息接入方式")
    parser.add_argument("--check-config", action="store_true", help="只检查配置，不启动服务")
    args = parser.parse_args()
    try:
        settings = Settings.from_env(transport=args.transport)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.check_config:
        print("配置检查通过。")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = JobStore(settings.db_path)
    api = FeishuClient(settings.app_id, settings.app_secret)
    workers = Workers(settings, store, api)
    if settings.transport == "websocket":
        from .socket_entry import build_client
        client = build_client(settings, store)

        def stop_socket(signum, frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop_socket)
        workers.start()
        logging.info("Excel bot starting in websocket mode; no public callback port required")
        try:
            client.start()
        except KeyboardInterrupt:
            pass
        finally:
            workers.shutdown()
        return
    server = create_server(create_app(settings, store), host="0.0.0.0", port=settings.port,
                           threads=4, max_request_body_size=1024 * 1024)

    def stop(signum, frame):
        workers.stop.set()
        server.close()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    workers.start()
    logging.info("Excel bot listening on port %s; endpoint /webhook/event", settings.port)
    try:
        server.run()
    finally:
        workers.shutdown()
        server.close()


if __name__ == "__main__":
    main()
