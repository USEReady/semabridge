"""Tests for notification system."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from semabridge.repository.orm.base import Base


@pytest.fixture
def redis_client():
    """Redis test client using fakeredis."""
    import fakeredis
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def db_session():
    """Database test session using in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
