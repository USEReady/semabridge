"""Quick verification that all multi-user components are wired correctly."""
from semabridge.repository.orm.session_factory import db_manager
from semabridge.repository.orm.models import Account
from semabridge.repository.account_repository import AccountRepository
from semabridge.auth.token_refresher import ensure_valid_token, TokenExpiredError
from semabridge.auth.account_credential_resolver import scoped_account_env
from semabridge.core.execution_engine import ExecutionEngine, RunContext
from sqlalchemy import text

print("=== Module Imports OK ===")

# Verify DB columns
engine = db_manager.get_engine()
with engine.connect() as conn:
    r = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='accounts'"))
    cols = sorted([row[0] for row in r])
    print(f"Account columns: {cols}")
    assert "refresh_token" in cols, "refresh_token column missing!"
    assert "token_expires_at" in cols, "token_expires_at column missing!"
    assert "auth_type" in cols, "auth_type column missing!"

# Verify ExecutionEngine.execute accepts account_id
import inspect
sig = inspect.signature(ExecutionEngine.execute)
assert "account_id" in sig.parameters, "account_id not in execute() signature!"
print(f"ExecutionEngine.execute params: {list(sig.parameters.keys())}")

# Verify RunContext has account_id
ctx = RunContext(
    project_id="test", run_id="test", config=None, start_time=0,
    source_type="fabric", account_id="acct-123"
)
assert ctx.account_id == "acct-123"
print(f"RunContext.account_id = {ctx.account_id}")

# Verify AccountRepository methods
session = db_manager.get_session_factory()()
try:
    repo = AccountRepository(session)
    assert hasattr(repo, "get_account_by_id")
    assert hasattr(repo, "get_default_account")
    assert hasattr(repo, "upsert_account")
    print("AccountRepository methods: OK")
finally:
    session.close()

print("\n=== ALL VERIFICATIONS PASSED ===")
