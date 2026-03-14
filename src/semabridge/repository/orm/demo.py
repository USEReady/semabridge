"""
Quick demo / smoke-test for the SemaBridge SQLAlchemy ORM layer.

Run with::

    python -m semabridge.repository.orm.demo

If ``DATABASE_URL`` is not set, a local DuckDB file at
``~/.semabridge/app.duckdb`` is used automatically.
"""

from __future__ import annotations

from semabridge.repository.db import setup_database
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Post, User


def main() -> None:
    """Create tables, insert sample data, and query it back."""
    engine, SessionLocal = setup_database()

    # Create all ORM tables (idempotent — safe to call repeatedly)
    Base.metadata.create_all(bind=engine)

    # ── Insert ────────────────────────────────────────────
    with SessionLocal() as session:
        alice = User(username="alice")
        alice.posts.append(Post(title="Hello from SemaBridge ORM!"))
        alice.posts.append(Post(title="DuckDB as default — neat."))

        session.add(alice)
        session.commit()

        print(f"✅ Created user: {alice}")
        print(f"   Posts: {alice.posts}")

    # ── Query ─────────────────────────────────────────────
    with SessionLocal() as session:
        user = session.query(User).filter_by(username="alice").first()
        if user:
            print(f"\n🔍 Queried user: {user}")
            for post in user.posts:
                print(f"   📝 {post}")
        else:
            print("⚠️  User 'alice' not found — something went wrong.")

    engine.dispose()
    print("\n🏁 Demo complete.")


if __name__ == "__main__":
    main()
