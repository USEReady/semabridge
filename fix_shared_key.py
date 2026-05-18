"""Patch relationship_detector.py: extend shared-key heuristic to match column names (not just PKs)."""
p = "src/semabridge/connectors/relationship_detector.py"
content = open(p, "rb").read().decode("utf-8")

# Find and replace the shared-key block (lines 182-193 approx)
old = (
    "        # Shared-key heuristic: if this column is itself a PK in another table,\r\n"
    "        # treat it as a foreign-key reference to that table's PK.\r\n"
    "        # Catches cases like Fact.YearPeriod -> Calendar.YearPeriod where there\r\n"
    "        # is no FK suffix but both sides share the exact same column name.\r\n"
    "        for target_table, target_pks in self.primary_keys.items():\r\n"
    "            if target_table.upper() == from_table.upper():\r\n"
    "                continue\r\n"
    "            for pk in target_pks:\r\n"
    "                if pk.replace(\" \", \"_\").upper() == col_upper:\r\n"
    "                    return (target_table, pk)\r\n"
    "        \r\n"
    "        return None"
)

new = (
    "        # Shared-key heuristic: if this column name matches a PK -- or any column --\r\n"
    "        # in exactly one other table, infer a join.\r\n"
    "        # Priority 1: match against PKs (stronger signal)\r\n"
    "        for target_table, target_pks in self.primary_keys.items():\r\n"
    "            if target_table.upper() == from_table.upper():\r\n"
    "                continue\r\n"
    "            for pk in target_pks:\r\n"
    "                if pk.replace(\" \", \"_\").upper() == col_upper:\r\n"
    "                    return (target_table, pk)\r\n"
    "        \r\n"
    "        # Priority 2: match against any column in exactly one other table\r\n"
    "        # (e.g. Fact.YearPeriod -> Calendar.YearPeriod, where YearPeriod is not the Calendar PK)\r\n"
    "        # Only trigger when exactly one table has a matching column to avoid false positives.\r\n"
    "        shared_col_matches = []\r\n"
    "        for target_table_upper, target_cols in self._column_lookup.items():\r\n"
    "            if target_table_upper == from_table.upper():\r\n"
    "                continue\r\n"
    "            for orig_col in target_cols:\r\n"
    "                if orig_col.replace(\" \", \"_\").upper() == col_upper:\r\n"
    "                    shared_col_matches.append((target_table_upper, orig_col))\r\n"
    "                    break\r\n"
    "        if len(shared_col_matches) == 1:\r\n"
    "            target_table_upper, matched_col = shared_col_matches[0]\r\n"
    "            target_table_orig = self._table_names_upper.get(target_table_upper)\r\n"
    "            if target_table_orig:\r\n"
    "                return (target_table_orig, matched_col)\r\n"
    "        \r\n"
    "        return None"
)

if old in content:
    content = content.replace(old, new, 1)
    open(p, "wb").write(content.encode("utf-8"))
    print("DONE - shared-key heuristic extended")
else:
    print("NOT FOUND - dumping context around 'Shared-key':")
    idx = content.find("Shared-key heuristic")
    if idx >= 0:
        print(repr(content[idx:idx+400]))
    else:
        print("  'Shared-key heuristic' not found at all")
