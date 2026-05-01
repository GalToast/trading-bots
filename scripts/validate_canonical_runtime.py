from __future__ import annotations

import ast
import py_compile
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKER_FILE = ROOT_DIR / "mt5_bot_v10.py"
SUPERVISOR_FILE = ROOT_DIR / "mt5_bot.py"
EXTERNAL_IMPORT_ROOTS = {"MetaTrader5", "pytz"}
PATCH_HELPERS = (
    ROOT_DIR / "scripts" / "deprecated" / "add_gemini.py",
    ROOT_DIR / "scripts" / "deprecated" / "patch_gemini_everywhere.py",
    ROOT_DIR / "scripts" / "deprecated" / "patch_gemini_range.py",
)


def resolve_local_module_files(module_name: str) -> list[Path]:
    module_path = ROOT_DIR.joinpath(*module_name.split("."))
    candidates: list[Path] = []

    file_candidate = module_path.with_suffix(".py")
    if file_candidate.exists():
        candidates.append(file_candidate)

    package_candidate = module_path / "__init__.py"
    if package_candidate.exists():
        candidates.append(package_candidate)

    return candidates


def worker_dependency_targets() -> tuple[list[Path], list[str]]:
    source = WORKER_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(WORKER_FILE))
    dependency_files: dict[Path, None] = {}
    missing_modules: dict[str, None] = {}

    def register_module(module_name: str) -> None:
        root = module_name.split(".", 1)[0]
        if root in sys.stdlib_module_names or root in EXTERNAL_IMPORT_ROOTS:
            return

        candidates = resolve_local_module_files(module_name)
        if candidates:
            for path in candidates:
                dependency_files[path] = None
            return

        missing_modules[module_name] = None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                register_module(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            register_module(node.module)
            package_candidates = resolve_local_module_files(node.module)
            if any(path.name == "__init__.py" for path in package_candidates):
                for alias in node.names:
                    register_module(f"{node.module}.{alias.name}")

    return sorted(dependency_files), sorted(missing_modules)


def compile_target(path: Path) -> str | None:
    try:
        py_compile.compile(str(path), doraise=True)
        return None
    except py_compile.PyCompileError as exc:
        return str(exc)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    targets: list[Path] = [SUPERVISOR_FILE, WORKER_FILE]
    dependency_files, missing_modules = worker_dependency_targets()
    targets.extend(dependency_files)
    targets.extend(PATCH_HELPERS)

    failures: list[tuple[str, str]] = []
    seen: set[Path] = set()

    if missing_modules:
        failures.append(
            (
                "worker imports",
                "missing local import(s): " + ", ".join(missing_modules),
            )
        )

    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        error = compile_target(target)
        if error:
            failures.append((str(target.relative_to(ROOT_DIR)), error))

    if failures:
        print("VALIDATION FAILED")
        for label, error in failures:
            print(f"- {label}: {error}")
        return 1

    print("VALIDATION OK")
    for target in sorted(seen):
        print(f"- {target.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
