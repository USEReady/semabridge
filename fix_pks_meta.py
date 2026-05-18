"""Patch: simplify pks_meta - use simple approach for dim detection, avoid false positives."""
p = "src/semabridge/converter/tmsl_to_osi.py"
content = open(p, "rb").read().decode("utf-8")

old = (
    "                    # Identify which datasets appear to be dimension tables.\r\n"
    "                    # Datasets referenced as 'to_table' in existing relationships are\r\n"
    "                    # dimension tables (their join-key column is the PK we want to\r\n"
    "                    # resolve to). Datasets that are only 'from_table' are fact-like;\r\n"
    "                    # do NOT pass their PK list to the detector so FK columns are not\r\n"
    "                    # accidentally skipped (the detector skips columns that are PKs\r\n"
    "                    # in their own table).\r\n"
    "                    dim_tables = {r.to_dataset for r in osi_model.relationships}\r\n"
    "                    pks_meta: Dict[str, List[str]] = {\r\n"
    "                        ds.unique_name: (\r\n"
    "                            [_norm(c.unique_name) for c in ds.columns if c.is_key]\r\n"
    "                            if ds.unique_name in dim_tables else []\r\n"
    "                        )\r\n"
    "                        for ds in osi_model.datasets\r\n"
    "                    }\r\n"
    "                    # Extend pks_meta: treat a normalized column name as a 'virtual PK'\r\n"
    "                    # of any dataset if that column name appears in exactly one other\r\n"
    "                    # dataset -- a reliable signal for YearPeriod -> Calendar joins.\r\n"
    "                    norm_col_to_tables: Dict[str, list] = {}\r\n"
    "                    for ds in osi_model.datasets:\r\n"
    "                        for c in ds.columns:\r\n"
    "                            nc = _norm(c.unique_name)\r\n"
    "                            norm_col_to_tables.setdefault(nc, []).append(ds.unique_name)\r\n"
    "                    for nc, ds_list in norm_col_to_tables.items():\r\n"
    "                        if len(ds_list) == 2:\r\n"
    "                            for ds_name in ds_list:\r\n"
    "                                if nc not in pks_meta.get(ds_name, []):\r\n"
    "                                    pks_meta.setdefault(ds_name, []).append(nc)"
)

new = (
    "                    # Pass only the normalized PK names to the detector.\r\n"
    "                    # Fact-like tables that have all their FK columns marked as PKs\r\n"
    "                    # (common in Fabric models) would have those columns skipped by\r\n"
    "                    # the detector. To avoid that, we pass an empty PK list for any\r\n"
    "                    # dataset whose normalized key columns include a column that\r\n"
    "                    # also exists in another dataset (i.e. it's a FK, not a true PK).\r\n"
    "                    # Build a set of normalized column names that appear in >1 dataset.\r\n"
    "                    norm_col_to_tables: Dict[str, list] = {}\r\n"
    "                    for ds in osi_model.datasets:\r\n"
    "                        for c in ds.columns:\r\n"
    "                            nc = _norm(c.unique_name)\r\n"
    "                            norm_col_to_tables.setdefault(nc, []).append(ds.unique_name)\r\n"
    "                    shared_cols = {nc for nc, ds_list in norm_col_to_tables.items() if len(ds_list) > 1}\r\n"
    "                    pks_meta: Dict[str, List[str]] = {\r\n"
    "                        ds.unique_name: [\r\n"
    "                            _norm(c.unique_name)\r\n"
    "                            for c in ds.columns\r\n"
    "                            # Only treat as PK if it's not a shared join column\r\n"
    "                            if c.is_key and _norm(c.unique_name) not in shared_cols\r\n"
    "                        ]\r\n"
    "                        for ds in osi_model.datasets\r\n"
    "                    }\r\n"
    "                    # Add shared-column names as virtual PKs on the dimension side:\r\n"
    "                    # if a column appears in exactly 2 datasets, add it to the PK list\r\n"
    "                    # of the dataset that already has fewer PKs (likely the dim table).\r\n"
    "                    for nc, ds_list in norm_col_to_tables.items():\r\n"
    "                        if len(ds_list) == 2:\r\n"
    "                            ds_a, ds_b = ds_list\r\n"
    "                            # Add as PK to the dataset with fewer existing PKs (dim side)\r\n"
    "                            pks_a = pks_meta.get(ds_a, [])\r\n"
    "                            pks_b = pks_meta.get(ds_b, [])\r\n"
    "                            if len(pks_a) <= len(pks_b) and nc not in pks_a:\r\n"
    "                                pks_meta.setdefault(ds_a, []).append(nc)\r\n"
    "                            elif nc not in pks_b:\r\n"
    "                                pks_meta.setdefault(ds_b, []).append(nc)"
)

if old in content:
    content = content.replace(old, new, 1)
    open(p, "wb").write(content.encode("utf-8"))
    print("DONE")
else:
    print("NOT FOUND")
    idx = content.find("Identify which datasets appear to be dimension tables")
    if idx >= 0:
        print(repr(content[idx:idx+200]))
