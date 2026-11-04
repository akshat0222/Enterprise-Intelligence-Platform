import os
import tempfile

# Must be set before any `app` import so settings, engine and limiter pick them up.
from sqlalchemy.engine import make_url

_base = make_url(os.environ.get("DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/enterprise_intel"))
TEST_DB = "enterprise_intel_test"
os.environ["DATABASE_URL"] = _base.set(database=TEST_DB).render_as_string(hide_password=False)
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="uploads-")
os.environ["ENVIRONMENT"] = "test"

import fitz  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    admin = create_engine(_base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}):
            conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    admin.dispose()
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean_tables(database):
    from app.db.session import engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE users CASCADE"))
    yield


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def register(client: TestClient, email: str, password: str = "correct-horse-battery") -> dict:
    assert client.post("/auth/register", json={"email": email, "password": password}).status_code == 201
    token = client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def alice(client):
    return register(client, "alice@example.com")


@pytest.fixture
def bob(client):
    return register(client, "bob@example.com")


def make_pdf(
    heading: str = "Incident Report", body: str = "The primary database ran out of disk space on Monday."
) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), heading, fontsize=22)
    page.insert_text((72, 140), body, fontsize=11)
    page.insert_text((72, 160), "Engineers restored service after expanding the volume.", fontsize=11)
    return doc.tobytes()
