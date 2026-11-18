"""Testumgebung: eigene Datenbank `<name>_test` im db-Container, jeder Test in einer Transaktion."""
import os
import tempfile

from sqlalchemy.engine import make_url

# Muss vor dem Import der App passieren (Engine und Einstellungen werden beim Import gebaut)
_basis = make_url(os.environ["DATABASE_URL"])
_test_url = _basis.set(database=f"{_basis.database}_test")
os.environ["DATABASE_URL"] = _test_url.render_as_string(hide_password=False)
os.environ["ADMIN_USER"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-passwort"
os.environ["SECRET_KEY"] = "test-schluessel"
os.environ["COOKIE_SECURE"] = "false"  # TestClient spricht http://testserver
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="ladekosten-test-")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def test_datenbank():
    admin = create_engine(_basis.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_test_url.database}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{_test_url.database}"'))
    cfg = Config("alembic.ini")
    cfg.attributes["logging"] = False
    # Hin- und Rückweg der Migrationen mittesten
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_test_url.database}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def db():
    """Session in einer äußeren Transaktion; commit() der App wird zum Savepoint, am Ende Rollback."""
    with engine.connect() as conn:
        trans = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        yield session
        session.close()
        trans.rollback()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def angemeldet(client):
    antwort = client.post("/login", data={"benutzer": "admin", "passwort": "test-passwort", "weiter_zu": "/"})
    assert antwort.status_code == 200
    return client
