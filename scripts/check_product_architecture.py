"""Guard the static boundary between the product core and legacy Public layers.

The legacy web/services modules still have setup and compatibility consumers.
This check does not remove them or claim to resolve dynamic imports.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ("studycenter_public.web", "studycenter_public.domain", "studycenter_public.storage")
OLD_SERVICES = "studycenter_public.services"
# The distribution adapter deliberately reuses these infrastructure providers.
PRODUCT_BRIDGES = ("studycenter_public.services.documents", "studycenter_public.services.speech")
IMPORT_SNIPPET = re.compile(r"\b(?:from|import)\s+studycenter_public(?:\.|\s|$)")


def within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def imported_modules(path: Path, module: str, *, setup: bool = False) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    tree = ast.parse(path.read_bytes(), filename=str(path))
    trees = [tree]
    if setup:
        # The setup readiness probes execute literal Python snippets via `-c`.
        # Examine those snippets too; assembled/dynamic code still needs review.
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and IMPORT_SNIPPET.search(node.value):
                try:
                    trees.append(ast.parse(node.value, filename=f"{path}:embedded"))
                except SyntaxError as exc:
                    raise ValueError(f"unparseable embedded product import: {path}") from exc
    result = set()
    for subtree in trees:
        for node in ast.walk(subtree):
            if isinstance(node, ast.Import):
                result.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    try:
                        name = importlib.util.resolve_name("." * node.level + name, package)
                    except ImportError as exc:
                        raise ValueError(f"invalid relative import: {path}") from exc
                result.add(name)
                # `from .. import web` must not bypass the boundary check.
                result.update(name + "." + alias.name for alias in node.names if alias.name != "*")
    return result


def check_architecture(package: Path) -> list[str]:
    core = package / "core"
    setup = package / "platform/setup.py"
    if (not core.is_dir() or not (package / "product.py").is_file()
        or not (package / "__main__.py").is_file() or not setup.is_file()):
        raise ValueError("product package, setup or entry points missing")
    paths = [(path, "studycenter_public.core." + path.stem, "core") for path in core.glob("*.py")]
    paths.extend((package / (name + ".py"), "studycenter_public." + name, name)
                 for name in ("product", "__main__"))
    paths.append((setup, "studycenter_public.platform.setup", "setup"))
    violations = []
    for path, module, role in sorted(paths, key=lambda item: str(item[0])):
        if path.stem == "__init__":
            module = "studycenter_public.core"
        for name in sorted(imported_modules(path, module, setup=(role == "setup"))):
            if any(within(name, prefix) for prefix in LEGACY):
                violations.append(f"{path.relative_to(package).as_posix()}: imports legacy product layer {name}")
            elif within(name, OLD_SERVICES):
                if role != "product" or not any(within(name, bridge) for bridge in PRODUCT_BRIDGES):
                    violations.append(f"{path.relative_to(package).as_posix()}: imports legacy service {name}")
    return violations


def main() -> int:
    try:
        violations = check_architecture(ROOT / "src/studycenter_public")
        for violation in violations:
            print(violation, file=sys.stderr)
        if violations:
            return 1
        print("product architecture: OK (static imports and literal setup snippets; dynamic imports are not proven)")
        return 0
    except (OSError, SyntaxError, UnicodeError, ValueError) as exc:
        print(f"product architecture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
