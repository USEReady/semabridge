"""patch: remove 'if not osi_model.relationships' guard in tmsl_to_osi.py"""
p = "src/semabridge/converter/tmsl_to_osi.py"
content = open(p, "rb").read().decode("utf-8")

old = (
    "            # Fallback: infer relationships when source metadata exposes none.\r\n"
    "            if not osi_model.relationships and osi_model.datasets:"
)
new = (
    "            # Supplement: always run RelationshipDetector to add inferred\r\n"
    "            # relationships that the Fabric model omits (e.g. CUSTOMER->INDUSTRY,\r\n"
    "            # BU->EXECUTIVE).  Results are merged with dedup so that explicit raw\r\n"
    "            # relationships are never overwritten.\r\n"
    "            if osi_model.datasets:"
)

if old in content:
    content = content.replace(old, new, 1)
    print("Step 1 OK: guard replaced")
else:
    print("Step 1 FAIL: guard not found")
    idx = content.find("Fallback: infer")
    if idx >= 0:
        print(repr(content[idx - 10 : idx + 120]))

# Add 'added = 0' counter before the loop
old2 = (
    "                        for rel in inferred_rels:\r\n"
    "                            sig = ("
)
new2 = (
    "                        added = 0\r\n"
    "                        for rel in inferred_rels:\r\n"
    "                            sig = ("
)
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("Step 2 OK: added counter inserted")
else:
    print("Step 2 FAIL")

# Increment counter at end of loop body
old3 = (
    "                            seen_signatures.add(sig)\r\n"
    "                        logger.info(\r\n"
    "                            \"Inferred %s fallback relationship(s) from table metadata\",\r\n"
    "                            len(osi_model.relationships),\r\n"
    "                        )"
)
new3 = (
    "                            seen_signatures.add(sig)\r\n"
    "                            added += 1\r\n"
    "                        if added:\r\n"
    "                            logger.info(\r\n"
    "                                \"Supplemented %d inferred relationship(s) (total: %d)\",\r\n"
    "                                added,\r\n"
    "                                len(osi_model.relationships),\r\n"
    "                            )"
)
if old3 in content:
    content = content.replace(old3, new3, 1)
    print("Step 3 OK: logger updated")
else:
    print("Step 3 FAIL")
    idx = content.find("Inferred %s fallback")
    if idx >= 0:
        print(repr(content[idx - 60 : idx + 100]))

# Fix warning message
old4 = '                logger.warning("Relationship inference fallback failed: %s", exc)'
new4 = '                logger.warning("Relationship inference supplement failed: %s", exc)'
if old4 in content:
    content = content.replace(old4, new4, 1)
    print("Step 4 OK: warning message updated")
else:
    print("Step 4 FAIL (warning msg)")

open(p, "wb").write(content.encode("utf-8"))
print("File written.")
