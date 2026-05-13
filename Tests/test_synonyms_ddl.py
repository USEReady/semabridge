import pytest
from semabridge.utils.synonyms import merge_synonyms, generate_auto_synonyms
from semabridge.connectors.synonym_clause import synonyms_clause

class TestMergeSynonyms:
    @pytest.mark.parametrize("user, auto, expected", [
        (["A", "B"], [], ["A", "B"]),                    # 1: user_defined_only
        ([], ["X", "Y"], ["X", "Y"]),                    # 2: auto_only
        (["A"], ["B", "C"], ["A", "B", "C"]),           # 3: user_first_then_auto
        (["Sales"], ["sales"], ["Sales"]),               # 4: deduplicate_case_insensitive
        (["Rev", "Rev"], [], ["Rev"]),                    # 5: deduplicate_identical_strings
        (["B", "A"], ["C"], ["B", "A", "C"]),            # 6: user_defined_priority_order
        ([], ["A", "B", "C", "D", "E"], ["A", "B", "C"]), # 7: auto_capped_at_default_3
        (["A", "B"], [" ", "C"], ["A", "B", "C"]),        # 12: strip_and_dedupe_empty
        (None, ["X"], ["X"]),                            # 17: user_None_handled
        (["A"], None, ["A"]),                            # 18: auto_None_handled
    ])
    def test_merge_basic_cases(self, user, auto, expected):
        assert merge_synonyms(user, auto) == expected

    def test_auto_capped_at_custom_value(self):
        # 8: auto_capped_at_custom_value
        assert merge_synonyms([], ["A", "B"], max_auto=1) == ["A"]

    def test_whitespace_trimming(self):
        # 10: whitespace_trimming
        assert merge_synonyms(["  Rev  "], []) == ["Rev"]
        assert merge_synonyms([], ["  Sale  "]) == ["Sale"]

    def test_empty_string_in_user(self):
        # 11: empty_string_in_user
        assert merge_synonyms(["", "Rev"], []) == ["Rev"]

    def test_preserve_casing_of_user(self):
        # 13: preserve_casing_of_user
        assert merge_synonyms(["Revenue"], ["revenue"]) == ["Revenue"]

    def test_max_auto_zero(self):
        # 15: max_auto_zero
        assert merge_synonyms(["A"], ["B"], max_auto=0) == ["A"]

    def test_unicode_normalization(self):
        # 20: unicode_normalization
        # Café vs Cafe + combining accent
        user = ["Café"]
        auto = ["Cafe\u0301"]
        # merge_synonyms currently uses .lower() which might not normalize NFKC automatically 
        # unless implemented. Standardizing on lowe() for now.
        result = merge_synonyms(user, auto)
        assert len(result) == 1
        assert result[0] == "Café"

class TestAutoSynonymsHeuristics:
    # Coverage for 41, 42
    @pytest.mark.parametrize("name, expected_contains", [
        ("customer_id", "Customer Id"), # 41: snake_case
        ("totalSales", "Total Sales"),   # 42: camelCase
        ("sale_amount", "Sale Amount"), 
    ])
    def test_heuristics(self, name, expected_contains):
        res = generate_auto_synonyms(name)
        assert expected_contains in res

class TestSynonymsClause:
    @pytest.mark.parametrize("syns, expected", [
        ([], ""),                                         # 21: empty_list
        (["Sales"], " WITH SYNONYMS = ('Sales')"),        # 22: single_synonym
        (["A", "B"], " WITH SYNONYMS = ('A', 'B')"),      # 23: multiple_synonyms
        (["O'Brien"], " WITH SYNONYMS = ('O''Brien')"),   # 24: escape_single_quote
        (["It's 'fine'"], " WITH SYNONYMS = ('It''s ''fine''')"), # 25: multiple_quotes
        (["München"], " WITH SYNONYMS = ('München')"),    # 27: unicode
        (["  Sales  "], " WITH SYNONYMS = ('Sales')"),    # 29: whitespace
    ])
    def test_clause_generation(self, syns, expected):
        assert synonyms_clause(syns) == expected
