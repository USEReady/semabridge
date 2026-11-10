import sys, re
sys.path.insert(0, ".")

from semabridge.converter.dax_ast_parser import (
    DaxAstParser, DaxSqlRenderer, DaxToken, DaxTokenType, DaxLexer,
    LiteralNode, ColumnRefNode, MeasureRefNode, BinaryOpNode, UnaryOpNode,
    FunctionCallNode, IdentifierNode, VarNode
)

# Monkey-patch DaxLexer, DaxParser, DaxSqlRenderer to test our enhancements
orig_skip = DaxLexer._skip_whitespace
def enhanced_skip(self):
    while self.pos < len(self.text):
        if self.text[self.pos] in " \t\r\n":
            self.pos += 1
            continue
        if self.text[self.pos:self.pos+2] in ("--", "//"):
            self.pos += 2
            while self.pos < len(self.text) and self.text[self.pos] not in "\r\n":
                self.pos += 1
            continue
        if self.text[self.pos:self.pos+2] == "/*":
            self.pos += 2
            while self.pos < len(self.text) and self.text[self.pos:self.pos+2] != "*/":
                self.pos += 1
            if self.pos < len(self.text):
                self.pos += 2
            continue
        break
DaxLexer._skip_whitespace = enhanced_skip

orig_read_id = DaxLexer._read_identifier
def enhanced_read_id(self):
    start = self.pos
    while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in "_"):
        self.pos += 1
    word = self.text[start : self.pos]
    word_lower = word.lower()
    if word_lower == "var":
        return DaxToken(DaxTokenType.VAR, word, start)
    if word_lower == "return":
        return DaxToken(DaxTokenType.RETURN, word, start)
    if word_lower in ("true", "false"):
        peek_pos = self.pos
        while peek_pos < len(self.text) and self.text[peek_pos] in " \t\r\n":
            peek_pos += 1
        if peek_pos < len(self.text) and self.text[peek_pos] == "(":
            return DaxToken(DaxTokenType.FUNC_NAME, word, start)
        return DaxToken(DaxTokenType.NUMBER, word, start)
    return orig_read_id(self)
DaxLexer._read_identifier = enhanced_read_id

orig_parse_primary = DaxAstParser._parse_primary
def enhanced_parse_primary(self):
    tok = self._peek()
    if tok.type == DaxTokenType.NUMBER and tok.value.lower() in ("true", "false"):
        self._advance()
        val = (tok.value.lower() == "true")
        return LiteralNode(value=val, raw=tok.value)
    return orig_parse_primary(self)
DaxAstParser._parse_primary = enhanced_parse_primary

# Renderer enhancements
orig_render_node = DaxSqlRenderer._render_node
def enhanced_render_node(self, node):
    if not hasattr(self, "_var_scope"):
        self._var_scope = {}
    if isinstance(node, VarNode):
        old_scope = dict(self._var_scope)
        try:
            for vname, vexpr in node.variables:
                val_sql = self._render_node(vexpr)
                self._var_scope[vname.casefold()] = f"({val_sql})"
            return self._render_node(node.body)
        finally:
            self._var_scope = old_scope
    if isinstance(node, IdentifierNode):
        n_cf = node.name.casefold()
        if n_cf in self._var_scope:
            return self._var_scope[n_cf]
    if isinstance(node, MeasureRefNode):
        n_cf = node.name.casefold()
        if n_cf in self._var_scope:
            return self._var_scope[n_cf]
    return orig_render_node(self, node)
DaxSqlRenderer._render_node = enhanced_render_node

orig_render_func = DaxSqlRenderer._render_function
def enhanced_render_func(self, node):
    func = node.func.upper()
    if func in ("TRUE", "FALSE"):
        return func
    if func == "KEEPFILTERS":
        if node.args:
            return self._render_node(node.args[0])
        return ""
    if func == "SELECTEDVALUE":
        if not node.args:
            raise self.DaxRenderError("SELECTEDVALUE requires at least 1 arg")
        col_sql = self._render_node(node.args[0])
        if len(node.args) > 1:
            def_sql = self._render_node(node.args[1])
            return f"COALESCE(ANY_VALUE({col_sql}), {def_sql})"
        return f"ANY_VALUE({col_sql})"
    if func == "VALUE":
        if not node.args:
            raise self.DaxRenderError("VALUE requires 1 arg")
        return f"TRY_TO_NUMBER({self._render_node(node.args[0])})"
    if func == "MROUND":
        if len(node.args) != 2:
            raise self.DaxRenderError("MROUND requires 2 args")
        v = self._render_node(node.args[0])
        m = self._render_node(node.args[1])
        return f"(ROUND(({v}) / ({m})) * ({m}))"
    if func in ("SUMX", "COUNTAX", "AVERAGEX", "MINX", "MAXX"):
        table_arg = node.args[0]
        val_arg = node.args[1]
        # Check if table_arg is FILTER(...)
        if isinstance(table_arg, FunctionCallNode) and table_arg.func.upper() == "FILTER":
            pred_sql = self._render_node(table_arg.args[1])
            val_sql = self._render_node(val_arg)
            if func == "COUNTAX":
                return f"COUNT(CASE WHEN {pred_sql} THEN {val_sql} END)"
            elif func == "SUMX":
                return f"SUM(CASE WHEN {pred_sql} THEN {val_sql}::FLOAT END)"
            elif func == "AVERAGEX":
                return f"AVG(CASE WHEN {pred_sql} THEN {val_sql}::FLOAT END)"
            elif func == "MINX":
                return f"MIN(CASE WHEN {pred_sql} THEN {val_sql} END)"
            elif func == "MAXX":
                return f"MAX(CASE WHEN {pred_sql} THEN {val_sql} END)"
        else:
            val_sql = self._render_node(val_arg)
            if func == "COUNTAX":
                if isinstance(val_arg, LiteralNode) and val_arg.value is True:
                    return "COUNT(*)"
                return f"COUNT({val_sql})"
            elif func == "SUMX":
                return f"SUM({val_sql}::FLOAT)"
            elif func == "AVERAGEX":
                return f"AVG({val_sql}::FLOAT)"
            elif func == "MINX":
                return f"MIN({val_sql})"
            elif func == "MAXX":
                return f"MAX({val_sql})"
    return orig_render_func(self, node)
DaxSqlRenderer._render_function = enhanced_render_func

# Also enhance _render_calculate to handle SUMX/COUNTAX base
orig_render_calc = DaxSqlRenderer._render_calculate
def enhanced_render_calc(self, args):
    if not args:
        raise self.DaxRenderError("CALCULATE requires at least 1 arg")
    base = args[0]
    if isinstance(base, FunctionCallNode) and base.func.upper() in ("SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTAX"):
        # Synthesize table FILTER
        # e.g. CALCULATE(SUMX(table, val), filter1, ...) -> SUM(CASE WHEN filter1 THEN val END)
        filter_clauses = []
        for farg in args[1:]:
            if isinstance(farg, FunctionCallNode) and farg.func.upper() == "FILTER":
                filter_clauses.append(self._render_node(farg.args[1]))
            elif isinstance(farg, BinaryOpNode):
                filter_clauses.append(self._render_node(farg))
        if filter_clauses:
            where = " AND ".join(filter_clauses)
            val_sql = self._render_node(base.args[1])
            f_upper = base.func.upper()
            if f_upper == "SUMX":
                return f"SUM(CASE WHEN {where} THEN {val_sql}::FLOAT END)"
            elif f_upper == "COUNTAX":
                return f"COUNT(CASE WHEN {where} THEN {val_sql} END)"
            elif f_upper == "AVERAGEX":
                return f"AVG(CASE WHEN {where} THEN {val_sql}::FLOAT END)"
            elif f_upper == "MINX":
                return f"MIN(CASE WHEN {where} THEN {val_sql} END)"
            elif f_upper == "MAXX":
                return f"MAX(CASE WHEN {where} THEN {val_sql} END)"
    return orig_render_calc(self, args)
DaxSqlRenderer._render_calculate = enhanced_render_calc

# Now test all 22 AI measures!
import os
import pytest
import openpyxl

excel_path = os.environ.get(
    "SEMABRIDGE_MEASURES_REPORT_EXCEL",
    r"C:\Users\manoj.s\Downloads\PowerBI_Measures_Report_Model_wise.xlsx",
)
if not os.path.isfile(excel_path):
    pytest.skip(f"Measures report not found at {excel_path}", allow_module_level=True)

wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
ws = wb["Measures Report"]
ai_measures = []
current_model = None
for r in ws.iter_rows(values_only=True):
    c0 = str(r[0]).strip() if r and r[0] is not None else ""
    c1 = str(r[1]).strip() if r and len(r) > 1 and r[1] is not None else ""
    m = re.search(r"^(.*?)\s*[\W_]+\s*(\d+)\s*measures\b", c0, re.I)
    if m:
        current_model = m.group(1).strip()
        continue
    if current_model == "Artificial Intelligence" and c0:
        table, meas = "", c0
        m_tm = re.match(r"^'?(.+?)'?\[(.+)\]$", c0)
        if m_tm:
            table, meas = m_tm.group(1).replace("''", "'"), m_tm.group(2)
        ai_measures.append((table, meas, c1))

resolved_map = {}
for p in range(3):
    for table, meas, dax in ai_measures:
        k = meas.upper()
        if k in resolved_map:
            continue
        try:
            parser = DaxAstParser()
            ast = parser.parse(dax)
            renderer = DaxSqlRenderer(
                table_alias=table.upper() if table else "T",
                measure_sql_map=resolved_map,
                table_aliases={
                    "Cases": "CASES",
                    "Opportunities": "OPPORTUNITIES",
                    "Owners": "OWNERS",
                    "Opportunity Forecast Adjustment": "OPPORTUNITY_FORECAST_ADJUSTMENT",
                }
            )
            sql = renderer._render_node(ast)
            if sql:
                resolved_map[k] = sql
                resolved_map[f"[{k}]"] = sql
                resolved_map[f"[{meas}]"] = sql
                resolved_map[meas] = sql
        except Exception as e:
            if p == 0:
                print(f"FAILED [{meas}]: {type(e).__name__}: {e}")

print(f"\n=======================================================")
print(f"RESULTS: {len(resolved_map)} entries, {len([m for _, m, _ in ai_measures if m.upper() in resolved_map])}/{len(ai_measures)} measures resolved!")
print(f"=======================================================")
for idx, (table, meas, dax) in enumerate(ai_measures, 1):
    sql = resolved_map.get(meas.upper())
    status = "SUCCESS" if sql else "FAILED"
    print(f"[{idx:2d}] {table}[{meas}] -> {status}")
    if sql:
        print(f"     SQL: {sql[:140]}")
