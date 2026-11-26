"""Phase 3.5 probe fixtures: tiny synthetic Power BI models with known answers.

Each fixture is a small model (tables with inline rows, relationships and DAX
measures) that goes through the PRODUCTION path -- TMSL -> converter (Databricks
rules) -> table binding -> planner -> writer -- so a probe tests exactly what
SemaBridge emits. Expected results are hand-computed from the rows (and
re-derived independently by Tests/target_backends/test_databricks_probes.py),
never read from Databricks.

No customer data: every value here is defined in this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from semabridge.target_backends.databricks.planner.model import Capability

SUITE_REVISION = "1"

ONE_TO_MANY = Capability.ONE_TO_MANY_JOINS
MIXED = Capability.MIXED_ROOT_VIEWS
COALESCE_CONST = Capability.CONSTANT_COALESCE

D = Decimal
NULL = None


@dataclass(frozen=True)
class Column:
    name: str            # Power BI column name
    tmsl_type: str       # TMSL dataType: int64 | string | decimal | double | dateTime
    sql_type: str        # the inline data's Databricks type


@dataclass(frozen=True)
class Table:
    name: str
    columns: Tuple[Column, ...]
    rows: Tuple[Tuple[Any, ...], ...]

    def real(self, column: str) -> str:
        """The probe table's physical column name (lowercase snake of the model name)."""
        return column.lower().replace(" ", "_").replace("$", "dollars").replace("(", "").replace(")", "")


@dataclass(frozen=True)
class Measure:
    table: str
    name: str
    dax: str
    format_string: Optional[str] = None


@dataclass(frozen=True)
class Query:
    """SELECT <group by fields>, MEASURE(<measures>) FROM <view> [WHERE field = value] GROUP BY ...

    ``group_by`` / ``where`` name model columns as (table, column); ``expected`` maps
    a tuple of group values (() when ungrouped) to {measure source name: value}."""

    qid: str
    view_of: str                                   # a measure placed in the view to query
    measures: Tuple[str, ...]
    group_by: Tuple[Tuple[str, str], ...] = ()
    where: Optional[Tuple[Tuple[str, str], Any]] = None
    expected: Dict[Tuple[Any, ...], Dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Fixture:
    fid: str
    probe: str                                     # P0 .. P8
    purpose: str
    tables: Tuple[Table, ...]
    relationships: Tuple[Tuple[str, str, str, str], ...]     # (many table, many col, one table, one col)
    measures: Tuple[Measure, ...]
    capabilities: FrozenSet[str] = frozenset()                # provisionally enabled for this probe only
    placement: Dict[str, Optional[str]] = field(default_factory=dict)   # measure -> "A" | "B" | None (left out)
    queries: Tuple[Query, ...] = ()
    observation: bool = False                     # P4: recorded, never gates anything
    # Measures SemaBridge may itself decline (the planner's safety checks, e.g. an
    # aggregate whose SQL does not match its DAX): recorded as "not placed by
    # SemaBridge", never a platform result. Every other placement must match.
    optional: FrozenSet[str] = frozenset()


# --- shared data ------------------------------------------------------------------------------

def _store(rows=((1, "East", 10, "A", 1), (2, "West", 20, "B", 2), (3, "East", 30, "C", 1))) -> Table:
    return Table("Store", (Column("StoreID", "int64", "INT"), Column("Region", "string", "STRING"),
                           Column("Capacity", "int64", "INT"), Column("StoreName", "string", "STRING"),
                           Column("DistrictID", "int64", "INT")), rows)


DISTRICT = Table("District", (Column("DistrictID", "int64", "INT"), Column("DistrictName", "string", "STRING")),
                 ((1, "North"), (2, "South")))

# Sales rows: (SaleID, StoreID, Amount, Budget, CustomerID). Store 3 (C) has no sales.
SALES = Table("Sales", (Column("SaleID", "int64", "INT"), Column("StoreID", "int64", "INT"),
                        Column("Amount", "decimal", "DECIMAL(10,2)"), Column("Budget", "decimal", "DECIMAL(10,2)"),
                        Column("CustomerID", "string", "STRING")),
              ((1, 1, D("100.00"), D("80.00"), "X"),
               (2, 1, D("200.00"), NULL, "Y"),
               (3, 2, D("50.00"), D("70.00"), "X"),
               (4, 2, NULL, D("30.00"), "Z"),
               (5, 2, NULL, NULL, "W"),
               (6, 1, D("40.00"), D("0.00"), "V")))

SALES_TO_STORE = ("Sales", "StoreID", "Store", "StoreID")
STORE_TO_DISTRICT = ("Store", "DistrictID", "District", "DistrictID")

# Aggregate families the planner currently permits (planner/semantics.py
# COLUMN_AGGREGATES | ITERATORS | ROW_COUNTERS) -> a DAX measure over SALES.
# Test: every family the planner allows has an entry here (drift is caught).
AGGREGATE_FAMILIES: Dict[str, str] = {
    "SUM": "SUM(Sales[Amount])",
    "AVERAGE": "AVERAGE(Sales[Amount])",
    "AVG": "AVERAGE(Sales[Amount])",                  # alias of AVERAGE in DAX analysis
    "MIN": "MIN(Sales[Amount])",
    "MAX": "MAX(Sales[Amount])",
    "COUNT": "COUNT(Sales[Amount])",
    "COUNTA": "COUNTA(Sales[CustomerID])",
    "COUNTBLANK": "COUNTBLANK(Sales[Budget])",
    "DISTINCTCOUNT": "DISTINCTCOUNT(Sales[CustomerID])",
    "DISTINCTCOUNTNOBLANK": "DISTINCTCOUNTNOBLANK(Sales[CustomerID])",
    "SUMX": "SUMX(Sales, Sales[Amount] * 2)",
    "AVERAGEX": "AVERAGEX(Sales, Sales[Amount])",
    "MINX": "MINX(Sales, Sales[Amount])",
    "MAXX": "MAXX(Sales, Sales[Amount])",
    "COUNTX": "COUNTX(Sales, Sales[Amount])",
    "COUNTAX": "COUNTAX(Sales, Sales[CustomerID])",
    "COUNTROWS": "COUNTROWS(Sales)",
}
_FAMILY_NAMES = {f: f"Agg {f}" for f in AGGREGATE_FAMILIES if f != "AVG"}

# Expected per family: ungrouped and by Store[Region] (East = stores 1, 3; West = store 2).
_AGG_EXPECTED = {
    "SUM": (D("390"), {"East": D("340"), "West": D("50")}),
    "AVERAGE": (D("97.5"), {"East": D("340") / 3, "West": D("50")}),
    "MIN": (D("40"), {"East": D("40"), "West": D("50")}),
    "MAX": (D("200"), {"East": D("200"), "West": D("50")}),
    "COUNT": (4, {"East": 3, "West": 1}),
    "COUNTA": (6, {"East": 3, "West": 3}),
    "COUNTBLANK": (2, {"East": 1, "West": 1}),
    "DISTINCTCOUNT": (5, {"East": 3, "West": 3}),          # X is in both regions: 5 overall, not 6
    "DISTINCTCOUNTNOBLANK": (5, {"East": 3, "West": 3}),
    "SUMX": (D("780"), {"East": D("680"), "West": D("100")}),
    "AVERAGEX": (D("97.5"), {"East": D("340") / 3, "West": D("50")}),
    "MINX": (D("40"), {"East": D("40"), "West": D("50")}),
    "MAXX": (D("200"), {"East": D("200"), "West": D("50")}),
    "COUNTX": (4, {"East": 3, "West": 1}),
    "COUNTAX": (6, {"East": 3, "West": 3}),
    "COUNTROWS": (6, {"East": 3, "West": 3}),
}


def _exp(rows: Dict[Tuple[Any, ...], Dict[str, Any]]) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    return rows


# --- P0 baseline: YAML 1.1, grouping, many-to-one and nested many-to-one joins ---------------

P0 = Fixture(
    fid="p0", probe="P0", purpose="baseline: YAML 1.1 metadata, grouped MEASURE queries, nested many-to-one joins",
    tables=(SALES, _store(), DISTRICT),
    relationships=(SALES_TO_STORE, STORE_TO_DISTRICT),
    measures=(Measure("Sales", "Total Sales", "SUM(Sales[Amount])", "#,0.00"),),
    placement={"Total Sales": "A"},
    queries=(
        Query("p0_total", "Total Sales", ("Total Sales",), expected=_exp({(): {"Total Sales": D("390")}})),
        Query("p0_region", "Total Sales", ("Total Sales",), group_by=(("Store", "Region"),),
              expected=_exp({("East",): {"Total Sales": D("340")}, ("West",): {"Total Sales": D("50")}})),
        Query("p0_district", "Total Sales", ("Total Sales",), group_by=(("District", "DistrictName"),),
              expected=_exp({("North",): {"Total Sales": D("340")}, ("South",): {"Total Sales": D("50")}})),
        Query("p0_filtered", "Total Sales", ("Total Sales",), where=(("Store", "Region"), "West"),
              expected=_exp({(): {"Total Sales": D("50")}})),
    ),
)

# --- P6 arithmetic with BLANK (Placement A), grouped by customer -----------------------------
# X: 150 / 150, Y: 200 / BLANK, Z: BLANK / 30, W: BLANK / BLANK, V: 40 / 0; total 390 / 180.
P6_MEASURES = (
    Measure("Sales", "Rev", "SUM(Sales[Amount])"),
    Measure("Sales", "Bud", "SUM(Sales[Budget])"),
    Measure("Sales", "Op Add", "[Rev] + [Bud]"),
    Measure("Sales", "Op Sub", "[Rev] - [Bud]"),
    Measure("Sales", "Op Mul", "[Rev] * [Bud]"),
    Measure("Sales", "Op Div", "DIVIDE([Rev], [Bud])"),
    Measure("Sales", "Op Div Alt", "DIVIDE([Rev], [Bud], 0)"),
)
_P6_ROWS = {
    ("X",): {"Op Add": D("300"), "Op Sub": D("0"), "Op Mul": D("22500"), "Op Div": D("1"), "Op Div Alt": D("1")},
    ("Y",): {"Op Add": D("200"), "Op Sub": D("200"), "Op Mul": NULL, "Op Div": NULL, "Op Div Alt": D("0")},
    ("Z",): {"Op Add": D("30"), "Op Sub": D("-30"), "Op Mul": NULL, "Op Div": NULL, "Op Div Alt": NULL},
    ("W",): {"Op Add": NULL, "Op Sub": NULL, "Op Mul": NULL, "Op Div": NULL, "Op Div Alt": D("0")},
    ("V",): {"Op Add": D("40"), "Op Sub": D("40"), "Op Mul": D("0"), "Op Div": NULL, "Op Div Alt": D("0")},
}
P6 = Fixture(
    fid="p6", probe="P6", purpose="arithmetic and BLANK: + - * DIVIDE, DIVIDE with alternate, zero denominators",
    tables=(SALES, _store()), relationships=(SALES_TO_STORE,), measures=P6_MEASURES,
    placement={m.name: "A" for m in P6_MEASURES},
    queries=(
        Query("p6_by_customer", "Op Add", ("Op Add", "Op Sub", "Op Mul", "Op Div", "Op Div Alt"),
              group_by=(("Sales", "CustomerID"),), expected=_exp(_P6_ROWS)),
        Query("p6_total", "Op Add", ("Op Add", "Op Sub", "Op Mul", "Op Div", "Op Div Alt"),
              expected=_exp({(): {"Op Add": D("570"), "Op Sub": D("210"), "Op Mul": D("70200"),
                                  "Op Div": D("390") / D("180"), "Op Div Alt": D("390") / D("180")}})),
    ),
)

# --- P7 aggregate families (Placement A), ungrouped and by region (unequal groups) ------------
P7_MEASURES = tuple(Measure("Sales", _FAMILY_NAMES[f], dax) for f, dax in AGGREGATE_FAMILIES.items() if f != "AVG")
P7 = Fixture(
    fid="p7", probe="P7", purpose="aggregate families the planner permits, unequal group sizes",
    tables=(SALES, _store()), relationships=(SALES_TO_STORE,), measures=P7_MEASURES,
    placement={m.name: "A" for m in P7_MEASURES}, optional=frozenset(m.name for m in P7_MEASURES),
    queries=(
        Query("p7_total", P7_MEASURES[0].name, tuple(m.name for m in P7_MEASURES),
              expected=_exp({(): {_FAMILY_NAMES[f]: e[0] for f, e in _AGG_EXPECTED.items()}})),
        Query("p7_region", P7_MEASURES[0].name, tuple(m.name for m in P7_MEASURES), group_by=(("Store", "Region"),),
              expected=_exp({(r,): {_FAMILY_NAMES[f]: e[1][r] for f, e in _AGG_EXPECTED.items()} for r in ("East", "West")})),
    ),
)

# --- P8 naming and metadata ---------------------------------------------------------------------
P8_STORE = Table("Store Info", (Column("Store ID", "int64", "INT"), Column("Région – été", "string", "STRING"),
                                Column("Capacity ($)", "int64", "INT")),
                 ((1, "Est", 10), (2, "Ouest", 20)))
P8 = Fixture(
    fid="p8", probe="P8", purpose="names with spaces, punctuation and Unicode; display names, synonyms, formats",
    tables=(P8_STORE,), relationships=(),
    measures=(Measure("Store Info", "Capacity %", "SUM('Store Info'[Capacity ($)])", "0.0%"),
              Measure("Store Info", "Capacity Total ($)", "SUM('Store Info'[Capacity ($)])", "€#,##0.00")),
    placement={"Capacity %": "A", "Capacity Total ($)": "A"},
    queries=(Query("p8_by_region", "Capacity %", ("Capacity %", "Capacity Total ($)"),
                   group_by=(("Store Info", "Région – été"),),
                   expected=_exp({("Est",): {"Capacity %": D("10"), "Capacity Total ($)": D("10")},
                                  ("Ouest",): {"Capacity %": D("20"), "Capacity Total ($)": D("20")}})),),
)

# --- P1 one-to-many, one child branch (Placement B, rooted at Store) ---------------------------
# Per store: A sales 340 / cap 10 / 3 sale rows; B 50 / 20 / 3; C no sales / 30.
P1_MEASURES = (
    Measure("Store", "Sales per Store", "DIVIDE(SUM(Sales[Amount]), COUNTROWS(Store))"),
    Measure("Store", "Sales minus Capacity", "SUM(Sales[Amount]) - SUM(Store[Capacity])"),
    # Counts only the child: written NULLIF(COUNT(<sales key>), 0) in the one-to-many view.
    # (Counting both tables in one measure is declined by the planner: the converted
    # SQL cannot tell the two COUNT(*) apart.)
    Measure("Store", "Sale Rows per Capacity", "DIVIDE(COUNTROWS(Sales), SUM(Store[Capacity]))"),
)
_P1_NAMES = tuple(m.name for m in P1_MEASURES)
P1 = Fixture(
    fid="p1", probe="P1", purpose="one-to-many join: root counted once, child aggregated independently",
    tables=(_store(), SALES), relationships=(SALES_TO_STORE,), measures=P1_MEASURES,
    capabilities=frozenset({ONE_TO_MANY}), placement={n: "B" for n in _P1_NAMES},
    queries=(
        Query("p1_by_store", _P1_NAMES[0], _P1_NAMES, group_by=(("Store", "StoreName"),), expected=_exp({
            ("A",): {"Sales per Store": D("340"), "Sales minus Capacity": D("330"), "Sale Rows per Capacity": D("0.3")},
            ("B",): {"Sales per Store": D("50"), "Sales minus Capacity": D("30"), "Sale Rows per Capacity": D("0.15")},
            ("C",): {"Sales per Store": NULL, "Sales minus Capacity": D("-30"), "Sale Rows per Capacity": NULL},
        })),
        Query("p1_by_region", _P1_NAMES[0], _P1_NAMES, group_by=(("Store", "Region"),), expected=_exp({
            ("East",): {"Sales per Store": D("170"), "Sales minus Capacity": D("300"), "Sale Rows per Capacity": D("0.075")},
            ("West",): {"Sales per Store": D("50"), "Sales minus Capacity": D("30"), "Sale Rows per Capacity": D("0.15")},
        })),
        Query("p1_total", _P1_NAMES[0], _P1_NAMES, expected=_exp({
            (): {"Sales per Store": D("130"), "Sales minus Capacity": D("330"), "Sale Rows per Capacity": D("0.1")}})),
        Query("p1_root_filter", _P1_NAMES[0], _P1_NAMES, where=(("Store", "Region"), "East"), expected=_exp({
            (): {"Sales per Store": D("170"), "Sales minus Capacity": D("300"), "Sale Rows per Capacity": D("0.075")}})),
    ),
)

# --- P6 / P7 in a Placement B context (part of the one-to-many gate) ---------------------------
PB_MEASURES = (
    Measure("Store", "B Avg plus Capacity", "AVERAGE(Sales[Amount]) + SUM(Store[Capacity])"),
    Measure("Store", "B Customers per Store", "DIVIDE(DISTINCTCOUNT(Sales[CustomerID]), COUNTROWS(Store))"),
    Measure("Store", "B Max minus Capacity", "MAX(Sales[Amount]) - SUM(Store[Capacity])"),
    Measure("Store", "B Budget times Stores", "SUM(Sales[Budget]) * COUNTROWS(Store)"),
    Measure("Store", "B Budget per Capacity", "DIVIDE(SUM(Sales[Budget]), SUM(Store[Capacity]), 0)"),
)
_PB_NAMES = tuple(m.name for m in PB_MEASURES)
PB = Fixture(
    fid="pb", probe="P6/P7-B", purpose="arithmetic and aggregate families inside a one-to-many view",
    tables=(_store(), SALES), relationships=(SALES_TO_STORE,), measures=PB_MEASURES,
    capabilities=frozenset({ONE_TO_MANY}), placement={n: "B" for n in _PB_NAMES},
    queries=(
        Query("pb_by_region", _PB_NAMES[0], _PB_NAMES, group_by=(("Store", "Region"),), expected=_exp({
            ("East",): {"B Avg plus Capacity": D("340") / 3 + 40, "B Customers per Store": D("1.5"),
                        "B Max minus Capacity": D("160"), "B Budget times Stores": D("160"),
                        "B Budget per Capacity": D("2")},
            ("West",): {"B Avg plus Capacity": D("70"), "B Customers per Store": D("3"),
                        "B Max minus Capacity": D("30"), "B Budget times Stores": D("100"),
                        "B Budget per Capacity": D("5")},
        })),
        Query("pb_total", _PB_NAMES[0], _PB_NAMES, expected=_exp({(): {
            "B Avg plus Capacity": D("157.5"), "B Customers per Store": D(5) / 3, "B Max minus Capacity": D("140"),
            "B Budget times Stores": D("540"), "B Budget per Capacity": D("3")}})),
    ),
)

# --- P5 sibling fact branches (Placement B rooted at Month) -----------------------------------
MONTH = Table("Month", (Column("MonthID", "int64", "INT"), Column("MonthName", "string", "STRING")),
              ((1, "Jan"), (2, "Feb"), (3, "Mar")))
ACTUAL = Table("Actual", (Column("ActualID", "int64", "INT"), Column("MonthID", "int64", "INT"),
                          Column("Amount", "decimal", "DECIMAL(10,2)")),
               ((1, 1, D("100")), (2, 1, D("200")), (3, 2, D("150"))))
BUDGET = Table("Budget", (Column("BudgetID", "int64", "INT"), Column("MonthID", "int64", "INT"),
                          Column("Amount", "decimal", "DECIMAL(10,2)")),
               ((1, 1, D("150")), (2, 1, D("100")), (3, 2, D("200")), (4, 3, D("50"))))
P5_MEASURES = (
    Measure("Month", "Variance", "SUM(Actual[Amount]) - SUM(Budget[Amount])"),
    Measure("Month", "Combined", "SUM(Actual[Amount]) + SUM(Budget[Amount])"),
)
P5 = Fixture(
    fid="p5", probe="P5", purpose="two fact tables one-to-many under one root: independent aggregation, no doubling",
    tables=(MONTH, ACTUAL, BUDGET),
    relationships=(("Actual", "MonthID", "Month", "MonthID"), ("Budget", "MonthID", "Month", "MonthID")),
    measures=P5_MEASURES, capabilities=frozenset({ONE_TO_MANY}), placement={"Variance": "B", "Combined": "B"},
    queries=(
        Query("p5_by_month", "Variance", ("Variance", "Combined"), group_by=(("Month", "MonthName"),), expected=_exp({
            ("Jan",): {"Variance": D("50"), "Combined": D("550")},
            ("Feb",): {"Variance": D("-50"), "Combined": D("350")},
            ("Mar",): {"Variance": D("-50"), "Combined": D("50")},
        })),
        Query("p5_total", "Variance", ("Variance", "Combined"),
              expected=_exp({(): {"Variance": D("-50"), "Combined": D("950")}})),
        Query("p5_filter", "Variance", ("Variance", "Combined"), where=(("Month", "MonthName"), "Mar"),
              expected=_exp({(): {"Variance": D("-50"), "Combined": D("50")}})),
    ),
)

# --- P2 mixed Placement A and B in one view (rooted at Store) ---------------------------------
P2_MEASURES = (
    Measure("Store", "Total Capacity", "SUM(Store[Capacity])"),
    Measure("Store", "Store Count", "COUNTROWS(Store)"),
    *P1_MEASURES,
)
P2 = Fixture(
    fid="p2", probe="P2", purpose="single-grain and composed measures in the same view",
    tables=(_store(), SALES), relationships=(SALES_TO_STORE,), measures=P2_MEASURES,
    capabilities=frozenset({ONE_TO_MANY, MIXED}),
    placement={"Total Capacity": "A", "Store Count": "A", **{n: "B" for n in _P1_NAMES}},
    queries=(
        Query("p2_a_only", "Total Capacity", ("Total Capacity", "Store Count"), group_by=(("Store", "Region"),),
              expected=_exp({("East",): {"Total Capacity": D("40"), "Store Count": 2},
                             ("West",): {"Total Capacity": D("20"), "Store Count": 1}})),
        Query("p2_a_and_b", "Total Capacity", ("Total Capacity", "Store Count", "Sales minus Capacity"),
              group_by=(("Store", "Region"),),
              expected=_exp({("East",): {"Total Capacity": D("40"), "Store Count": 2, "Sales minus Capacity": D("300")},
                             ("West",): {"Total Capacity": D("20"), "Store Count": 1, "Sales minus Capacity": D("30")}})),
        Query("p2_total", "Total Capacity", ("Total Capacity", "Store Count", "Sales per Store"),
              expected=_exp({(): {"Total Capacity": D("60"), "Store Count": 3, "Sales per Store": D("130")}})),
    ),
)

# --- P3 constants -----------------------------------------------------------------------------------
P3 = Fixture(
    fid="p3", probe="P3", purpose="constant measure returns its value on empty input (COALESCE)",
    tables=(_store(),), relationships=(), measures=(Measure("Store", "Target", "15"),),
    capabilities=frozenset({COALESCE_CONST}), placement={"Target": "A"},
    queries=(
        Query("p3_total", "Target", ("Target",), expected=_exp({(): {"Target": 15}})),
        Query("p3_empty", "Target", ("Target",), where=(("Store", "Region"), "Nowhere"),
              expected=_exp({(): {"Target": 15}})),
    ),
)
P3_PLAIN = Fixture(
    fid="p3plain", probe="P3", purpose="observation: plain MAX(15) on empty input (why the capability matters)",
    tables=(_store(),), relationships=(), measures=(Measure("Store", "Target", "15"),),
    placement={"Target": "A"}, observation=True,
    queries=(Query("p3plain_empty", "Target", ("Target",), where=(("Store", "Region"), "Nowhere"),
                   expected=_exp({(): {"Target": NULL}})),),
)

# --- P4 invalid many-to-one cardinality (observation only) -------------------------------------
P4 = Fixture(
    fid="p4", probe="P4", purpose="observation: a many-to-one join whose 'one' side has a duplicate key",
    tables=(SALES, _store(rows=((1, "East", 10, "A", 1), (1, "West", 10, "A2", 1), (2, "West", 20, "B", 2)))),
    relationships=(SALES_TO_STORE,), measures=(Measure("Sales", "Total Sales", "SUM(Sales[Amount])"),),
    placement={"Total Sales": "A"}, observation=True,
    queries=(Query("p4_region", "Total Sales", ("Total Sales",), group_by=(("Store", "Region"),), expected={}),),
)

BASELINE = (P0, P6, P7, P8)
CAPABILITY = {ONE_TO_MANY: (P1, P5, PB), MIXED: (P2,), COALESCE_CONST: (P3,)}
OBSERVATIONS = (P3_PLAIN, P4)
ALL: Tuple[Fixture, ...] = BASELINE + (P1, P5, PB, P2, P3) + OBSERVATIONS


def table_of(fixture: Fixture, name: str) -> Table:
    return next(t for t in fixture.tables if t.name == name)
