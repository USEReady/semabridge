import ast

filepath = r"c:\Users\Premasai\Documents\workspace\semabridge\src\semabridge\connectors\databricks_publisher.py"
with open(filepath, "r", encoding="utf-8") as f:
    tree = ast.parse(f.read())

for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        if node.name == "DatabricksPublisher":
            for n in node.body:
                if isinstance(n, ast.FunctionDef):
                    print(f"def {n.name}(...) - line {n.lineno}")
