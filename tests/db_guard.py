"""Single source of truth for whether DB-backed tests may run, and
against what. Integration tests write and delete rows; they must never
reach a remote database by accident just because DATABASE_URL happened
to be exported in the shell."""
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()  # same semantics as app.py: does not override a set var

_URL = str(os.getenv("DATABASE_URL") or "").strip()
_HOST = urlparse(_URL).hostname if _URL else None
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ALLOW_REMOTE = os.getenv("FOXBOT_ALLOW_REMOTE_TEST_DB") == "1"

DATABASE_CONFIGURED = bool(_URL) and (_HOST in _LOCAL_HOSTS or _ALLOW_REMOTE)

if not _URL:
    SKIP_REASON = (
        "DATABASE_URL not set -- these tests need a real Postgres database "
        "(a throwaway/dev one, not production)."
    )
elif DATABASE_CONFIGURED:
    SKIP_REASON = ""
else:
    SKIP_REASON = (
        f"DATABASE_URL points at host {_HOST!r}, which is not local. These "
        "tests write and delete rows. Point at local Docker Postgres "
        "(postgresql://postgres:dev@localhost:5432/foxbot_dev), or set "
        "FOXBOT_ALLOW_REMOTE_TEST_DB=1 if you genuinely mean to."
    )
