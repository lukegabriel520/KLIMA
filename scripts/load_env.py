"""Load .env into process env without printing secrets. Used by local runners."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


if __name__ == "__main__":
    load_dotenv()
    from urllib.parse import urlparse

    url = os.environ.get("SUPABASE_DB_URL", "")
    u = urlparse(url)
    print("scheme", u.scheme)
    print("host", u.hostname)
    print("port", u.port)
    print("db", u.path)
    print("user", u.username)
    print("has_password", bool(u.password))
    print("query", u.query)
