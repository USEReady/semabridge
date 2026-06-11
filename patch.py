import os
import sys

# Patch ddl_builder to dump DDL
target_file = r'C:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\ddl_builder.py'
with open(target_file, 'r', encoding='utf-8') as f:
    content = f.read()

replacement = """        if metrics_lines: definitions.append("METRICS (\\n" + "\\n".join(metrics_lines) + "\\n)")

        final_ddl = lines[0] + "\\n" + "\\n".join(definitions) + ";"
        with open(r'C:\\Users\\MANOJ\\dev-test\\semabridge\\ddl_debug.sql', 'w', encoding='utf-8') as debug_f:
            debug_f.write(final_ddl)
        return fix_global_sums(final_ddl, self.translator)"""

content = content.replace('        if metrics_lines: definitions.append("METRICS (\\n" + "\\n".join(metrics_lines) + "\\n)")\n\n        final_ddl = lines[0] + "\\n" + "\\n".join(definitions) + ";"\n        return fix_global_sums(final_ddl, self.translator)', replacement)

with open(target_file, 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched.")
