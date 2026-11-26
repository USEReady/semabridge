"""Power BI names for the DAX side of a parity check."""
from __future__ import annotations

from semabridge.reconciliation import pbi_names
from semabridge.reconciliation.golden_verifier import GoldenQueryVerifier


def _runner(measures, columns, fail=False):
    def run(q):
        if fail:
            raise RuntimeError("INFO functions not allowed")
        if "MEASURES" in q:
            return [{"[Name]": m} for m in measures]
        return [{"[Table]": t, "[Name]": c} for t, c in columns]
    return run


def test_identifier_safe_names_map_to_model_names():
    run = _runner(["Total VanArsdel Units YTD", "Total Units"], [("Product", "Manufacturer"), ("Product", "isVanArsdel")])
    measure, dims, ok = pbi_names.resolve(run, "Total_VanArsdel_Units_YTD", ["PRODUCT.MANUFACTURER"])
    assert ok and measure == "Total VanArsdel Units YTD"
    assert dims == ["Product.Manufacturer"]


def test_unreachable_model_keeps_stored_names():
    measure, dims, ok = pbi_names.resolve(_runner([], [], fail=True), "Total_Units", ["PRODUCT.MANUFACTURER"])
    assert (measure, dims, ok) == ("Total_Units", ["PRODUCT.MANUFACTURER"], False)


def test_dax_query_uses_model_measure_but_keeps_label():
    q = GoldenQueryVerifier.generate_dax_query(
        metric_name="Total_VanArsdel_Units_YTD", dimensions=["Product.Manufacturer"],
        measure_name="Total VanArsdel Units YTD",
    )
    assert '"Total_VanArsdel_Units_YTD", [Total VanArsdel Units YTD]' in q
    assert "'Product'[Manufacturer]" in q
