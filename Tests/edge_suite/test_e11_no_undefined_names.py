"""Every production module uses no undefined names in executable code (catches runtime NameError bugs,
e.g. an undefined variable in a report builder or a missing import). Names used only inside type
annotations (incl. string forward references) are ignored, since they are never evaluated."""
import ast
import pathlib

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
from pyflakes import messages as M  # noqa: E402
from pyflakes.reporter import Reporter  # noqa: E402

ROOT = pathlib.Path(__file__).resolve()
while not (ROOT / "src" / "semabridge").exists() and ROOT.parent != ROOT:
    ROOT = ROOT.parent
FILES = sorted((ROOT / "src" / "semabridge").rglob("*.py"))


def _annotation_names(tree):
    found = set()

    def grab(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                found.add((n.lineno, n.id))
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                try:
                    for m in ast.walk(ast.parse(n.value, mode="eval")):
                        if isinstance(m, ast.Name):
                            found.add((n.lineno, m.id))
                except SyntaxError:
                    pass

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            grab(node.annotation)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            grab(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            grab(node.returns)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Mapped":
            grab(node.slice)
    return found


class _Collect(Reporter):
    def __init__(self):
        self.found = []

    def flake(self, msg):
        if isinstance(msg, (M.UndefinedName, M.UndefinedLocal)):
            self.found.append((msg.lineno, msg.message_args[0], msg.message % msg.message_args))

    def unexpectedError(self, filename, msg):
        pass

    def syntaxError(self, filename, msg, lineno, offset, text):
        pass


@pytest.mark.parametrize("path", FILES, ids=[str(p.relative_to(ROOT / "src")) for p in FILES])
def test_module_has_no_undefined_names(path):
    src = path.read_text(encoding="utf-8-sig", errors="ignore")
    tree = ast.parse(src)  # a real syntax error fails here
    rep = _Collect()
    pyflakes_api.check(src, str(path), rep)
    ann = _annotation_names(tree)
    real = [f"line {ln}: {text}" for ln, name, text in rep.found if (ln, name) not in ann]
    assert not real, "\n".join(real)
