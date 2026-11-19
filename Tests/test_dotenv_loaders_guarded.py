"""Every .env loader in src honours SEMABRIDGE_DISABLE_DOTENV.

An unguarded loader leaked a developer's .env (SEMABRIDGE_MOCK_PARITY,
SNOWFLAKE_AUTHENTICATOR, real credentials) into the whole test run as soon
as one module was imported during collection.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "semabridge"


def test_every_dotenv_loader_checks_the_disable_switch():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "load_dotenv(" not in text:
            continue
        if "SEMABRIDGE_DISABLE_DOTENV" not in text and "dotenv_disabled" not in text:
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f".env loaders ignoring SEMABRIDGE_DISABLE_DOTENV: {offenders}"
