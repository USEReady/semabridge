import re

expr = '''
SWITCH(SELECTEDVALUE('Plant_BU_Mapping'[business_unit]),
  "USP", "• Source of dashboard is SAP subledger via BW...",
  "MSH", "• Source of dashboard is SAP subledger via BW...",
  "CMM", "• Source of dashboard is SAP subledger via BW...",
  "DefaultText"
)
'''.strip()

pattern = r"(?is)^SWITCH\s*\(\s*SELECTEDVALUE\s*\(\s*(?:'(?P<table_q>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))\s*\[(?P<col>[^\]]+)\]\s*(?:,\s*.*?)?\)\s*,(.+)\)$"
m_switch = re.match(pattern, expr)
if m_switch:
    print("Match!")
    print("table_q", m_switch.group("table_q"))
    print("table", m_switch.group("table"))
    print("col", m_switch.group("col"))
    print("args", m_switch.group(3))
else:
    print("No match")

expr2 = '''SWITCH(SELECTEDVALUE(Plant_BU_Mapping[business_unit]), "USP", "Result")'''
m_switch2 = re.match(pattern, expr2)
if m_switch2:
    print("Match 2!")
    print("table_q", m_switch2.group("table_q"))
    print("table", m_switch2.group("table"))
    print("col", m_switch2.group("col"))
    print("args", m_switch2.group(3))
else:
    print("No match 2")

