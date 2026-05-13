import sys

sys.path.insert(0, "src")

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer


def main() -> None:
    parser = DaxAstParser()
    renderer = DaxSqlRenderer(table_alias="T", date_alias="CALENDAR")

    tests = [
        ("TOTALYTD", "TOTALYTD(SUM('Sales'[Amount]), 'Date'[Date])"),
        (
            "CALCULATE_SPLY",
            "CALCULATE(SUM('Sales'[Amount]), SAMEPERIODLASTYEAR('Date'[Date]))",
        ),
        ("CALCULATE_FILTER", "CALCULATE(SUM('Sales'[Amount]), 'Date'[Year] = 2024)"),
    ]

    for name, dax in tests:
        ast = parser.parse(dax)
        sql = renderer.render(ast)
        print(f"\n== {name} ==")
        print("DAX:", dax)
        print("SQL:", sql)


if __name__ == "__main__":
    main()
