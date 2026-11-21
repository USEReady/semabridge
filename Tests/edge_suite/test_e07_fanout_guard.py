"""Ask AI join-path check: many->one is safe; many->one->many (fan-out) and many-to-many are not."""
import types

import pytest

from semabridge.ai.nl_query_engine import NLQueryEngine

check = NLQueryEngine._detect_fan_out_path


def R(a, b, card="N:1", active=True):
    return types.SimpleNamespace(from_concept=a, to_concept=b, cardinality=card, is_active=active)


STAR = [R("Sales", "Product"), R("Sales", "Date"), R("Sales", "Customer"), R("Customer", "Region"),
        R("Returns", "Product"), R("Returns", "Date"), R("Cases", "Account"), R("Opps", "Account"),
        R("Cases", "CaseCal"), R("Opps", "OppCal"), R("Product", "Category")]

SAFE = [("Sales", "Product"), ("Sales", "Date"), ("Sales", "Region"), ("Sales", "Category"), ("Returns", "Category"),
        ("Cases", "Account"), ("Opps", "Account"), ("Opps", "OppCal"), ("Cases", "CaseCal"), ("sales", "PRODUCT"),
        ("Sales", "Sales"), ("Customer", "Region")]
FANOUT = [("Opps", "CaseCal"), ("Cases", "OppCal"), ("Sales", "Returns"), ("Returns", "Customer"),
          ("Returns", "Region"), ("Opps", "Cases"), ("Cases", "Opps"), ("Product", "Sales"), ("Region", "Sales"),
          ("Category", "Returns"), ("Account", "Cases"), ("OPPS", "casecal")]


@pytest.mark.parametrize("m,d", SAFE)
def test_many_to_one_paths_are_safe(m, d):
    assert check(m, d, STAR)[0] is False


@pytest.mark.parametrize("m,d", FANOUT)
def test_fan_out_paths_are_flagged(m, d):
    flagged, msg = check(m, d, STAR)
    assert flagged is True and msg


@pytest.mark.parametrize("card", ["N:N", "MANY_TO_MANY", "many_to_many"])
def test_many_to_many_is_flagged(card):
    assert check("A", "B", [R("A", "B", card)])[0] is True


def test_inactive_relationship_is_ignored():
    rels = [R("Opps", "Account"), R("Cases", "Account", active=False), R("Cases", "CaseCal")]
    assert check("Opps", "CaseCal", rels)[0] is False or "no path" in str(check("Opps", "CaseCal", rels)[1]).lower()


@pytest.mark.parametrize("card", ["1:N", "ONE_TO_MANY"])
def test_reverse_declared_one_to_many_is_respected(card):
    rels = [R("Product", "Sales", card)]
    assert check("Sales", "Product", rels)[0] is False
    assert check("Product", "Sales", rels)[0] is True


@pytest.mark.parametrize("m,d", [("", "X"), ("X", ""), (None, "X")])
def test_missing_concepts_are_not_flagged(m, d):
    assert check(m, d, STAR)[0] is False
