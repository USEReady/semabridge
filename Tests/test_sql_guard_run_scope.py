"""The SQL guard allowlists are scoped to one run: concurrent runs in other
threads neither see nor widen nor clear each other's allowlist."""
import threading

from semabridge.connectors.sql_guard import (
    clear_allowed_absent_tables,
    get_allowed_absent_tables,
    get_allowed_model_objects,
    reset_guard_scope,
    set_allowed_absent_tables,
    set_allowed_model_objects,
)


def _in_thread(fn):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("v", fn()))
    t.start()
    t.join()
    return out["v"]


def test_allowlist_is_not_visible_to_another_run():
    reset_guard_scope()
    set_allowed_model_objects({'"sales_sv"'})
    assert get_allowed_model_objects() == {"SALES_SV"}
    assert _in_thread(get_allowed_model_objects) == set()
    reset_guard_scope()


def test_other_run_cannot_widen_or_clear_this_runs_allowlist():
    reset_guard_scope()
    set_allowed_absent_tables({"ACCOUNTS"})

    def other_run():
        set_allowed_absent_tables({"PAYROLL"})
        clear_allowed_absent_tables()
        set_allowed_model_objects({"OTHER_SV"})
        return get_allowed_model_objects()

    assert _in_thread(other_run) == {"OTHER_SV"}
    assert get_allowed_absent_tables() == {"ACCOUNTS"}
    assert get_allowed_model_objects() == set()
    reset_guard_scope()


def test_get_returns_a_copy():
    reset_guard_scope()
    set_allowed_model_objects({"A"})
    get_allowed_model_objects().add("B")
    assert get_allowed_model_objects() == {"A"}
    reset_guard_scope()
