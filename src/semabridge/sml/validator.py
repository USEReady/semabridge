from typing import Dict, List


def validate_official_payload(payload: Dict) -> List[str]:
    """Validate official semantic payload shape (OSI-style).

    Returns a list of error messages (empty if valid).
    """
    errors: List[str] = []

    # Top-level required fields
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"]

    datasets = payload.get("datasets")
    if datasets is None:
        errors.append("missing 'datasets' field")
        return errors
    if not isinstance(datasets, list):
        errors.append("'datasets' must be a list")
        return errors

    for ds in datasets:
        if not isinstance(ds, dict):
            errors.append("each dataset must be an object")
            continue
        if not ds.get("unique_name"):
            errors.append(f"dataset missing unique_name: {ds}")
            continue
        cols = ds.get("columns", [])
        if not isinstance(cols, list):
            errors.append(f"dataset {ds.get('unique_name')} columns must be a list")
            continue
        for c in cols:
            if not isinstance(c, dict):
                errors.append(f"column entry in {ds.get('unique_name')} must be an object: {c}")
                continue
            if not c.get("unique_name") and not c.get("name"):
                errors.append(f"column missing unique_name in dataset {ds.get('unique_name')}: {c}")
            if not c.get("data_type") and not c.get("type"):
                errors.append(f"column missing data_type in dataset {ds.get('unique_name')}: {c}")

    return errors


if __name__ == "__main__":
    import argparse, json, sys

    p = argparse.ArgumentParser()
    p.add_argument("file", help="Path to payload JSON file")
    args = p.parse_args()
    data = json.loads(open(args.file).read())
    errs = validate_official_payload(data)
    if errs:
        print("Validation errors:")
        for e in errs:
            print(" -", e)
        sys.exit(2)
    print("OK")


if __name__ == "__main__":
    import argparse, json, sys

    p = argparse.ArgumentParser()
    p.add_argument("file", help="Path to canonical JSON file")
    args = p.parse_args()
    data = json.loads(open(args.file).read())
    errs = validate_canonical(data)
    if errs:
        print("Validation errors:")
        for e in errs:
            print(" -", e)
        sys.exit(2)
    print("OK")
