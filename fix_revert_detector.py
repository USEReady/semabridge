"""Revert relationship_detector.py changes and restore clean state."""
p = "src/semabridge/connectors/relationship_detector.py"
content = open(p, "rb").read().decode("utf-8")

# Revert _match_column_to_table to original clean form
# (undo: space normalization, shared-column heuristic additions)

old = (
    "        # Check for common FK suffixes.\r\n"
    "        # Normalize spaces->underscores first so \"BU Key\" matches \"BU_KEY\" suffix.\r\n"
    "        col_norm = column_name.replace(\" \", \"_\")\r\n"
    "        col_upper = col_norm.upper()\r\n"
    "        \r\n"
    "        for suffix in self.FK_SUFFIXES:\r\n"
    "            suffix_upper = suffix.upper()\r\n"
    "            if col_upper.endswith(suffix_upper):\r\n"
    "                # Extract the base name (e.g., CUSTOMER from CUSTOMER_ID)\r\n"
    "                base = col_upper[:-len(suffix_upper)].rstrip(\"_ \")\r\n"
    "                if not base:\r\n"
    "                    continue\r\n"
    "                \r\n"
    "                # Try to find matching table\r\n"
    "                target_table = self._find_matching_table(base)\r\n"
    "                if target_table and target_table.upper() != from_table.upper():\r\n"
    "                    # Find the PK column in target table\r\n"
    "                    target_pk = self._get_likely_pk(target_table)\r\n"
    "                    if target_pk:\r\n"
    "                        return (target_table, target_pk)\r\n"
)

new = (
    "        col_upper = column_name.upper()\r\n"
    "        \r\n"
    "        # Check for common FK suffixes\r\n"
    "        for suffix in self.FK_SUFFIXES:\r\n"
    "            suffix_upper = suffix.upper()\r\n"
    "            if col_upper.endswith(suffix_upper):\r\n"
    "                # Extract the base name (e.g., CUSTOMER from CUSTOMER_ID)\r\n"
    "                base = col_upper[:-len(suffix_upper)].rstrip(\"_\")\r\n"
    "                if not base:\r\n"
    "                    continue\r\n"
    "                \r\n"
    "                # Try to find matching table\r\n"
    "                target_table = self._find_matching_table(base)\r\n"
    "                if target_table and target_table.upper() != from_table.upper():\r\n"
    "                    # Find the PK column in target table\r\n"
    "                    target_pk = self._get_likely_pk(target_table)\r\n"
    "                    if target_pk:\r\n"
    "                        return (target_table, target_pk)\r\n"
)

if old in content:
    content = content.replace(old, new, 1)
    print("Step 1 OK: reverted space-normalization in FK suffix block")
else:
    print("Step 1 FAIL: old text not found")

# Revert the extended shared-key heuristic
old2_start = "        # Shared-key heuristic: if this column name matches a PK -- or any column --"
old2_end = "        return None"

idx_start = content.find(old2_start)
idx_end = content.find(old2_end, idx_start)
if idx_start >= 0 and idx_end >= 0:
    old2 = content[idx_start:idx_end + len(old2_end)]
    new2 = "        return None"
    content = content.replace(old2, new2, 1)
    print("Step 2 OK: removed extended shared-key heuristic")
else:
    print("Step 2 FAIL: extended heuristic not found")
    if idx_start >= 0:
        print(f"  Start found at {idx_start}, end at {idx_end}")

open(p, "wb").write(content.encode("utf-8"))
print("File written.")
