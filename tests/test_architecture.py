"""Fitness functions: the package dependency rules and size limits, checked on the source tree."""
import ast
import unittest
from pathlib import Path
from typing  import Dict, Iterator, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent

SKIPPED_DIRS  = {".venv", ".claude", ".codegraph", "tests", "__pycache__", ".git"}
SKIPPED_FILES = {"example.py", "recover_chat.py"}
MAX_MODULE_LINES = 500
BANNED_MODULE_NAMES = {"utils", "helpers", "common"}

# Package (or root script) -> the internal packages it may import. Everything else is forbidden.
ALLOWED_IMPORTS: Dict[str, Set[str]] = {
    "domain"   : set(),
    "config"   : {"domain"},
    "network"  : {"domain"},
    "storage"  : {"domain"},
    "referee"  : {"domain", "config", "network"},
    "serverapp": {"domain", "config", "network", "storage"},
    "server"   : {"domain", "config", "network", "storage", "serverapp"},
    "client"   : {"domain", "config", "network", "referee"},
    "tools"    : {"domain", "config", "storage"},
}

SourceFile = Tuple[Path, str]


def _source_files() -> List[Path]:
    files = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if SKIPPED_DIRS & set(relative.parts) or relative.name in SKIPPED_FILES:
            continue
        files.append(path)
    return files


def _owner(path: Path) -> str:
    """Returns the package of a file, or the script name for a file in the repo root."""
    parts = path.relative_to(ROOT).parts
    return parts[0] if len(parts) > 1 else path.stem


def _imported_modules(path: Path) -> Iterator[Tuple[str, str]]:
    """Yields (top-level module, import text) for every absolute import in the file."""
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], f"import {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0], f"from {node.module} import ..."


def _internal_graph() -> Dict[str, Set[str]]:
    internal = set(ALLOWED_IMPORTS)
    graph: Dict[str, Set[str]] = {name: set() for name in internal}
    for path in _source_files():
        owner = _owner(path)
        for module, _ in _imported_modules(path):
            if module in internal and module != owner:
                graph.setdefault(owner, set()).add(module)
    return graph


class ArchitectureTest(unittest.TestCase):
    def test_every_source_file_belongs_to_a_known_package(self):
        unknown = [str(p.relative_to(ROOT)) for p in _source_files() if _owner(p) not in ALLOWED_IMPORTS]
        self.assertEqual([], unknown, f"files outside the known packages {sorted(ALLOWED_IMPORTS)}")

    def test_imports_follow_the_dependency_rules(self):
        violations = []
        for path in _source_files():
            owner = _owner(path)
            allowed = ALLOWED_IMPORTS.get(owner, set())
            for module, text in _imported_modules(path):
                if module in ALLOWED_IMPORTS and module != owner and module not in allowed:
                    violations.append(f"{path.relative_to(ROOT)}: `{text}` ({owner} must not import {module})")
        self.assertEqual([], violations, "forbidden imports:\n" + "\n".join(violations))

    def test_packages_have_no_import_cycles(self):
        graph = _internal_graph()
        visiting: List[str] = []
        done: Set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                cycle = visiting[visiting.index(name):] + [name]
                self.fail("import cycle between packages: " + " -> ".join(cycle))
            if name in done:
                return
            visiting.append(name)
            for target in sorted(graph.get(name, ())):
                visit(target)
            visiting.pop()
            done.add(name)

        for name in sorted(graph):
            visit(name)

    def test_no_module_is_too_long(self):
        offenders = []
        for path in _source_files():
            line_count = len(path.read_text().splitlines())
            if line_count > MAX_MODULE_LINES:
                offenders.append(f"{path.relative_to(ROOT)}: {line_count} lines")
        self.assertEqual([], offenders, f"modules over {MAX_MODULE_LINES} lines:\n" + "\n".join(offenders))

    def test_no_catch_all_modules(self):
        offenders = [str(p.relative_to(ROOT)) for p in _source_files() if p.stem in BANNED_MODULE_NAMES]
        self.assertEqual([], offenders, "utils/helpers/common modules are not allowed: " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
