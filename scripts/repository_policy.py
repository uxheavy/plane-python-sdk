#!/usr/bin/env python3
# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

"""Enforce repository shape and provider-free SDK boundaries."""

from __future__ import annotations

import argparse
import ast
import builtins
import subprocess
from collections.abc import Callable
from pathlib import Path

GENERIC_ROOTS = {"common", "helpers", "shared", "utils"}
TRACKED_OUTPUTS = {".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist", "htmlcov"}
TRANSPORT_IMPORTS = {
    "aiohttp",
    "http.client",
    "httplib2",
    "httpcore",
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
}
TRANSPORT_ALLOWLIST = {
    "plane/api/base_resource.py",
    "plane/api/work_items/attachments.py",
    "plane/client/oauth_client.py",
}
BUILTIN_EXCEPTIONS = {
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
}


def parse_name_status(raw: bytes) -> list[tuple[str, str]]:
    fields = [part.decode() for part in raw.split(b"\0") if part]
    changes: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            index += 1
            path = fields[index]
            index += 1
        else:
            path = fields[index]
            index += 1
        changes.append((status[0], path))
    return changes


def import_from_module(node: ast.ImportFrom, package: str = "") -> str:
    if not node.level:
        return node.module or ""
    parts = package.split(".") if package else []
    parts = parts[: max(0, len(parts) - node.level + 1)]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def imported_modules(source: str, package: str = "") -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = import_from_module(node, package)
            if module:
                modules.add(module)
                modules.update(f"{module}.{alias.name}" for alias in node.names)
            else:
                modules.update(alias.name for alias in node.names)
    return modules


def imports_package(modules: set[str], package: str) -> bool:
    return any(module == package or module.startswith(f"{package}.") for module in modules)


def import_bindings(
    source: str, package: str, root_exports: dict[str, str] | None = None
) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                bindings[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom):
            module = import_from_module(node, package)
            for alias in node.names:
                target = f"{module}.{alias.name}" if module else alias.name
                if module == "plane" and root_exports:
                    target = root_exports.get(alias.name, target)
                bindings[alias.asname or alias.name] = target
    return bindings


def qualified_name(node: ast.expr, bindings: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = qualified_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def matching_classes(
    classes: list[ast.ClassDef],
    bindings: dict[str, str],
    matches: Callable[[str], bool],
) -> set[str]:
    matched: set[str] = set()
    while True:
        found = {
            node.name
            for node in classes
            if any(
                matches(qualified_name(base, bindings)) or qualified_name(base, bindings) in matched
                for base in node.bases
            )
        }
        if found == matched:
            return matched
        matched = found


def module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def implementation_modules(root: Path) -> set[str]:
    imports_by_module: dict[str, set[str]] = {}
    for path in sorted((root / "plane").rglob("*.py")):
        module = module_name(root, path)
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        imports_by_module[module] = imported_modules(path.read_text(encoding="utf-8"), package)

    implementation = {"plane.api", "plane.client"} | {
        module for module in imports_by_module if module.startswith(("plane.api.", "plane.client."))
    }
    while True:
        found = {
            module
            for module, imports in imports_by_module.items()
            if module != "plane"
            and any(imports_package(imports, owner) for owner in implementation)
        }
        expanded = implementation | found
        if expanded == implementation:
            return implementation
        implementation = expanded


def transport_targets(root: Path) -> set[str]:
    targets: set[str] = set()
    for relative in TRANSPORT_ALLOWLIST:
        path = root / relative
        if not path.is_file():
            continue
        module = module_name(root, path)
        package = module.rpartition(".")[0]
        for local, target in import_bindings(path.read_text(encoding="utf-8"), package).items():
            if any(imports_package({target}, transport) for transport in TRANSPORT_IMPORTS):
                targets.add(f"{module}.{local}")
    return targets


def evaluate_tree(root: Path) -> list[tuple[str, str, str]]:
    errors: list[tuple[str, str, str]] = []
    implementation = implementation_modules(root)
    boundary_targets = transport_targets(root)
    for path in sorted((root / "plane").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        package = ".".join(path.relative_to(root).parent.parts)
        source = path.read_text(encoding="utf-8")
        imports = imported_modules(source, package)
        bindings = import_bindings(source, package)

        leaked_transport = {name for name in TRANSPORT_IMPORTS if imports_package(imports, name)}
        if relative not in TRANSPORT_ALLOWLIST:
            referenced = {
                qualified_name(node, bindings)
                for node in ast.walk(ast.parse(source))
                if isinstance(node, (ast.Name, ast.Attribute))
            }
            leaked_transport.update(
                target
                for target in boundary_targets
                if any(name == target or name.startswith(f"{target}.") for name in referenced)
            )
        if leaked_transport and relative not in TRANSPORT_ALLOWLIST:
            names = ", ".join(sorted(leaked_transport))
            errors.append(
                ("SDK001", relative, f"transport import {names} is outside the allowlist")
            )

        if relative.startswith(("plane/models/", "plane/errors/")):
            forbidden = {owner for owner in implementation if imports_package(imports, owner)}
            if "plane" in imports:
                forbidden.add("plane")
            if forbidden:
                names = ", ".join(sorted(forbidden))
                errors.append(
                    ("SDK002", relative, f"domain contract imports implementation layer {names}")
                )
    return errors


def evaluate_changes(
    changes: list[tuple[str, str]],
    base_has_path: Callable[[str], bool],
    read_file: Callable[[str], str | None] = lambda _path: None,
) -> list[tuple[str, str, str]]:
    errors: list[tuple[str, str, str]] = []
    root_exports = import_bindings(read_file("plane/__init__.py") or "", "plane")
    for status, path in changes:
        if status not in {"A", "M", "R"}:
            continue
        added = status in {"A", "R"}
        parts = Path(path).parts
        if added:
            root_name = Path(parts[0]).stem if len(parts) == 1 else parts[0]
            if parts and root_name in GENERIC_ROOTS and not base_has_path(parts[0]):
                errors.append(
                    ("SDK003", path, f"new generic root {root_name} needs a concrete owner")
                )
            output = next((part for part in parts if part in TRACKED_OUTPUTS), None)
            if output:
                errors.append(
                    ("SDK004", path, f"tracked build output directory {output} is forbidden")
                )
        if path.startswith("plane/") and path.endswith(".py"):
            source = read_file(path)
            if source is None:
                continue
            classes = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)]
            package = ".".join(Path(path).parent.parts)
            bindings = import_bindings(source, package, root_exports)
            resources = matching_classes(
                classes,
                bindings,
                lambda name: (
                    name == "BaseResource"
                    or name == "plane.api.base_resource.BaseResource"
                    or name.startswith("plane.api.")
                ),
            )
            if resources and not path.startswith("plane/api/"):
                errors.append(
                    (
                        "SDK006",
                        path,
                        f"BaseResource subclass must live under plane/api: {', '.join(resources)}",
                    )
                )
            dto_classes = matching_classes(
                classes,
                bindings,
                lambda name: name in {"BaseModel", "pydantic.BaseModel"},
            )
            if (
                dto_classes
                and not path.startswith("plane/models/")
                and path != "plane/client/oauth_client.py"
            ):
                names = ", ".join(sorted(dto_classes))
                errors.append(
                    (
                        "SDK007",
                        path,
                        f"Pydantic DTO must live under plane/models: {names}",
                    )
                )
            exception_classes = matching_classes(
                classes,
                bindings,
                lambda name: name in BUILTIN_EXCEPTIONS or name.startswith("plane.errors."),
            )
            if exception_classes and not path.startswith("plane/errors/"):
                names = ", ".join(sorted(exception_classes))
                errors.append(
                    ("SDK008", path, f"SDK exception must live under plane/errors: {names}")
                )
            client_classes = matching_classes(
                classes,
                bindings,
                lambda name: name == "PlaneClient" or name.startswith("plane.client."),
            )
            if client_classes and not path.startswith("plane/client/"):
                names = ", ".join(sorted(client_classes))
                errors.append(("SDK009", path, f"SDK client must live under plane/client: {names}"))
            if not path.startswith("plane/api/") or Path(path).name in {
                "__init__.py",
                "base_resource.py",
            }:
                continue
            invalid = [
                node.name
                for node in classes
                if not node.name.startswith("_") and node.name not in resources
            ]
            if invalid:
                errors.append(
                    (
                        "SDK005",
                        path,
                        f"API resource must inherit BaseResource: {', '.join(invalid)}",
                    )
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    raw = subprocess.check_output(
        ("git", "diff", "--name-status", "-z", "--find-renames", f"{args.base}...HEAD")
    )
    changes = parse_name_status(raw)

    def base_has_path(path: str) -> bool:
        return (
            subprocess.run(
                ("git", "cat-file", "-e", f"{args.base}:{path}"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )

    root = Path.cwd()

    def read_file(path: str) -> str | None:
        target = root / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    errors = [*evaluate_tree(root), *evaluate_changes(changes, base_has_path, read_file)]
    for rule, path, message in errors:
        print(f"{path}: {rule} {message}")
    if errors:
        return 1
    print(f"repository policy passed ({len(changes)} changed paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
