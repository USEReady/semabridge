"""Patch: add source-table guard to the infer loop in tmsl_to_osi.py."""
p = "src/semabridge/converter/tmsl_to_osi.py"
content = open(p, "rb").read().decode("utf-8")

# Find the exact location of "added = 0\r\n                    for from_ds in osi_model.datasets:"
old = "                    added = 0\r\n                    for from_ds in osi_model.datasets:\r\n                        for col in from_ds.columns:"

new = (
    "                    # Only infer from datasets that are known fact/bridge tables:\r\n"
    "                    # (a) already a 'from_table' in existing explicit relationships, OR\r\n"
    "                    # (b) has >= 2 FK-suffix columns (wide/fact-like table).\r\n"
    "                    # This prevents dimension tables from generating false reverse joins.\r\n"
    "                    known_from_tables = {r.from_dataset.casefold() for r in osi_model.relationships}\r\n"
    "                    _fk_sfx = (\"_KEY\", \"_ID\", \"_FK\", \"_CODE\")\r\n"
    "                    def _fk_count(ds):\r\n"
    "                        return sum(1 for c in ds.columns if any(_n(c.unique_name).endswith(s) for s in _fk_sfx))\r\n"
    "\r\n"
    "                    added = 0\r\n"
    "                    for from_ds in osi_model.datasets:\r\n"
    "                        if not (from_ds.unique_name.casefold() in known_from_tables or _fk_count(from_ds) >= 2):\r\n"
    "                            continue\r\n"
    "                        for col in from_ds.columns:"
)

if old in content:
    content = content.replace(old, new, 1)
    open(p, "wb").write(content.encode("utf-8"))
    print("DONE")
else:
    print("NOT FOUND")
    idx = content.find("added = 0\r\n                    for from_ds in")
    if idx >= 0:
        print(repr(content[idx:idx+100]))
