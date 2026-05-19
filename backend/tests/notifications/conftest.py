"""Tests for notification system."""
import pytest


@pytest.fixture
def redis_client():
    """Redis test client."""
    import redis
    return redis.Redis(host='localhost', port=6379, db=1)


@pytest.fixture
def db_session():
    """Database test session."""
    # TODO: Create test database session
    pass
