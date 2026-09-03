import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://ozon_app:ozon_app@localhost:5432/ozon_analytics_test")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("APP_ENCRYPTION_KEY", "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm8=")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("AI_PROVIDER", "demo")
os.environ.setdefault("DEMO_MODE", "true")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.membership import StoreMembership, StoreRole
from app.models.store import Store
from app.models.user import User

TEST_DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(TEST_DATABASE_URL, future=True)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db_session():
    """A session joined to an outer transaction that is always rolled back,
    even if application code under test calls session.commit() (via a SAVEPOINT
    that is restarted after every commit) — standard SQLAlchemy 2.0 test isolation."""
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def two_stores_with_users(db_session):
    """Two stores, each with an owner user who has NO access to the other store,
    plus a platform admin who can see both. Returns a dict of everything a test
    might need."""
    store_a = Store(name="Магазин А")
    store_b = Store(name="Магазин Б")
    db_session.add_all([store_a, store_b])
    db_session.flush()

    admin = User(email="admin@example.com", full_name="Admin", password_hash=hash_password("adminpass123"), is_admin=True)
    owner_a = User(email="owner_a@example.com", full_name="Owner A", password_hash=hash_password("password123"))
    owner_b = User(email="owner_b@example.com", full_name="Owner B", password_hash=hash_password("password123"))
    db_session.add_all([admin, owner_a, owner_b])
    db_session.flush()

    db_session.add_all([
        StoreMembership(user_id=owner_a.id, store_id=store_a.id, role=StoreRole.OWNER),
        StoreMembership(user_id=owner_b.id, store_id=store_b.id, role=StoreRole.OWNER),
    ])
    db_session.commit()

    return {
        "store_a": store_a, "store_b": store_b,
        "admin": admin, "owner_a": owner_a, "owner_b": owner_b,
    }


def login(client: TestClient, email: str, password: str) -> TestClient:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client
