"""Add owner_id to accounts and create refresh_tokens table."""
from semabridge.repository.orm.session_factory import db_manager
from sqlalchemy import text

engine = db_manager.get_engine()

with engine.connect() as conn:
    # 1. Add owner_id to accounts
    result = conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='accounts'"
    ))
    existing = {r[0] for r in result}
    
    if "owner_id" not in existing:
        conn.execute(text("ALTER TABLE accounts ADD COLUMN owner_id INTEGER REFERENCES users(id)"))
        print("Added owner_id column to accounts")
    else:
        print("owner_id already exists")

    # 2. Create refresh_tokens table
    result = conn.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_name='refresh_tokens'"
    ))
    if not result.fetchone():
        conn.execute(text("""
            CREATE TABLE refresh_tokens (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                expires_at TIMESTAMPTZ NOT NULL,
                is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens(user_id)"))
        conn.execute(text("CREATE UNIQUE INDEX ix_refresh_tokens_token_hash ON refresh_tokens(token_hash)"))
        print("Created refresh_tokens table")
    else:
        print("refresh_tokens table already exists")

    conn.commit()
    print("Migration complete!")
