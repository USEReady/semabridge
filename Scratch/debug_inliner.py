import re

class SemanticDDLSanitizer:
    @staticmethod
    def _split_outer_synonyms_clause(expr: str):
        text = str(expr or "").strip()
        idx = 0
        length = len(text)
        depth = 0
        in_single = False
        syn_starts = []

        while idx < length:
            ch = text[idx]
            if in_single:
                if ch == "'" and idx + 1 < length and text[idx + 1] == "'":
                    idx += 2
                    continue
                if ch == "'":
                    in_single = False
            elif ch == "'":
                in_single = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and ch in ("W", "w"):
                tail = text[idx:]
                if re.match(r"(?i)WITH\s+SYNONYMS\s*=\s*\(", tail):
                    syn_starts.append(idx)
            idx += 1

        if not syn_starts:
            return text, ""

        cut = syn_starts[-1]
        body = text[:cut].rstrip()
        clause = text[cut:].rstrip()
        return body, " " + clause

    @staticmethod
    def _strip_inner_synonyms(expr: str) -> str:
        pattern = re.compile(
            r"\s+WITH\s+SYNONYMS\s*=\s*\((?:[^()']|'(?:''|[^'])*')*\)",
            re.IGNORECASE,
        )
        prev = None
        result = expr
        while result != prev:
            prev = result
            result = pattern.sub("", result)
        return result

    @classmethod
    def _inline_metric_references(cls, metric_lines: list[str]) -> list[str]:
        for loop_idx in range(5):
            print(f"--- LOOP {loop_idx+1} ---")
            parsed = []
            expr_by_name = {}
            defined = set()
            window_metrics = set()
            
            for line in metric_lines:
                match = re.match(
                    r'^(\s*\w+\."([^"]+)"\s+AS\s+)(.+?)(,?)\s*$',
                    line.rstrip(),
                    flags=re.IGNORECASE,
                )
                if not match:
                    parsed.append((line, "", "", "", ""))
                    continue
                prefix, name, raw_expr, comma = match.groups()
                expr, syn_clause = cls._split_outer_synonyms_clause(raw_expr)
                defined.add(name)
                expr_by_name[name] = expr
                if re.search(r"\bOVER\b", expr, flags=re.IGNORECASE):
                    window_metrics.add(name)
                parsed.append((line, prefix, name, expr, syn_clause + (comma or "")))

            changed = False
            next_lines = []
            for line, prefix, name, expr, suffix in parsed:
                if not name:
                    next_lines.append(line)
                    continue
                expanded_expr = expr
                if re.search(r"\bOVER\b", expr, flags=re.IGNORECASE):
                    candidate_refs = window_metrics
                else:
                    candidate_refs = defined
                    
                refs = [
                    ref
                    for ref in set(re.findall(r'(?<!\.)"([A-Z_][A-Z0-9_]*)"', expr))
                    if ref in candidate_refs
                    and ref != name
                    and expr_by_name.get(ref)
                ]
                
                if refs:
                    print(f"Metric '{name}' has refs: {refs}")
                
                for ref_name in sorted(refs, key=len, reverse=True):
                    ref_expr = expr_by_name.get(ref_name)
                    if not ref_expr:
                        continue
                    clean_ref_expr = cls._strip_inner_synonyms(ref_expr)
                    print(f"  Inlining '{ref_name}' into '{name}':")
                    print(f"    ref_expr: {ref_expr}")
                    print(f"    clean_ref_expr: {clean_ref_expr}")
                    expanded_expr = re.sub(
                        rf'(?<!\.)"{re.escape(ref_name)}"',
                        f"({clean_ref_expr})",
                        expanded_expr,
                    )
                if expanded_expr != expr:
                    changed = True
                    print(f"    -> Expanded expression: {expanded_expr}")
                next_lines.append(f"{prefix}{expanded_expr}{suffix}")

            metric_lines = next_lines
            if not changed:
                break
        return metric_lines

def debug():
    # Read metrics lines from the working file
    path = r"c:\Users\MANOJ\semabridge-working\output\debug\ddl_COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC.sql"
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Extract METRICS block lines
    lines = content.splitlines()
    metric_lines = []
    in_metrics = False
    for line in lines:
        if line.strip().upper() == "METRICS (":
            in_metrics = True
            continue
        if in_metrics and line.strip().startswith(")"):
            in_metrics = False
            break
        if in_metrics:
            metric_lines.append(line)
            
    # Run inline_metric_references
    SemanticDDLSanitizer._inline_metric_references(metric_lines)

if __name__ == "__main__":
    debug()
