import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str
    db_path: Path
    allowed_open_ids: tuple[str, ...] = ()
    port: int = 3000
    transport: str = "webhook"

    @classmethod
    def from_env(cls, transport="webhook"):
        if transport not in ("webhook", "websocket"):
            raise ValueError("TRANSPORT 必须为 webhook 或 websocket。")
        names = ("APP_ID", "APP_SECRET", "VERIFICATION_TOKEN", "ENCRYPT_KEY")
        values = [os.getenv(name, "").strip() for name in names]
        required = names if transport == "webhook" else names[:2]
        missing = [name for name, value in zip(names, values)
                   if name in required and (not value or value.startswith("replace_"))]
        if missing:
            raise ValueError("请配置：" + ", ".join(missing))
        port = int(os.getenv("PORT", "3000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT 必须是 1–65535。")
        return cls(*values, db_path=Path(os.getenv("JOB_DB", ".runtime/jobs.sqlite3")).resolve(),
                   allowed_open_ids=tuple(item.strip() for item in os.getenv("ALLOWED_OPEN_IDS", "").split(",") if item.strip()),
                   port=port, transport=transport)
