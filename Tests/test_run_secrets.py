"""Per-run secrets: concurrent runs of different users see only their own
LLM keys, do not wait for each other, and nothing is written to os.environ."""
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from semabridge.core.run_secrets import current_secrets, get_secret, install_in_process, run_secrets


def test_run_value_wins_and_env_is_untouched(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "server-key")
    with run_secrets({"OPENAI_API_KEY": "alice-key"}):
        assert get_secret("OPENAI_API_KEY") == "alice-key"
        assert os.environ["OPENAI_API_KEY"] == "server-key"
    assert get_secret("OPENAI_API_KEY") == "server-key"


def test_missing_values_fall_back_to_env_or_default(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with run_secrets({"GROQ_API_KEY": ""}):
        assert get_secret("GROQ_API_KEY", "none") == "none"
    assert get_secret(None, "d") == "d"


def test_concurrent_users_are_isolated_and_not_serialized():
    barrier = threading.Barrier(2, timeout=5)
    seen = {}

    def run(user, key):
        with run_secrets({"ANTHROPIC_API_KEY": key}):
            barrier.wait()  # both runs are inside their scopes at the same time
            time.sleep(0.05)
            seen[user] = get_secret("ANTHROPIC_API_KEY")

    threads = [threading.Thread(target=run, args=("alice", "a-key")), threading.Thread(target=run, args=("bob", "b-key"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"alice": "a-key", "bob": "b-key"}


def test_nested_scope_overlays():
    with run_secrets({"OPENAI_API_KEY": "a", "GROQ_API_KEY": "g"}):
        with run_secrets({"OPENAI_API_KEY": "b"}):
            assert current_secrets() == {"OPENAI_API_KEY": "b", "GROQ_API_KEY": "g"}
        assert get_secret("OPENAI_API_KEY") == "a"


def _child_key():
    return get_secret("MISTRAL_API_KEY"), os.environ.get("MISTRAL_API_KEY")


def test_process_pool_children_get_the_runs_keys():
    with ProcessPoolExecutor(max_workers=1, initializer=install_in_process,
                             initargs=({"MISTRAL_API_KEY": "child-key"},)) as pool:
        assert pool.submit(_child_key).result(timeout=60) == ("child-key", "child-key")


def test_tier5_settings_see_the_run_key(monkeypatch):
    from semabridge.dax_translation.tier5.config import ProviderSettings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ProviderSettings(enabled_env="OPENAI_API_KEY")
    assert not settings.is_enabled()
    with run_secrets({"OPENAI_API_KEY": "k"}):
        assert settings.is_enabled()
