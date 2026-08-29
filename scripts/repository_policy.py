#!/usr/bin/env python3
# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

"""Enforce repository shape and provider-free SDK boundaries."""

from __future__ import annotations

import argparse
import ast
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


def resource_base_aliases(source: str, package: str) -> set[str]:
    aliases = {"BaseResource"}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = import_from_module(node, package)
        if module == "plane.api.base_resource":
            aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == "BaseResource"
            )
        elif module == "plane.api" or module.startswith("plane.api."):
            aliases.update(alias.asname or alias.name for alias in node.names)
    return aliases


def resource_classes(classes: list[ast.ClassDef], aliases: set[str]) -> set[str]:
    resources: set[str] = set()
    while True:
        found = {
            node.name
            for node in classes
            if any(
                (isinstance(base, ast.Name) and base.id in aliases | resources)
                or (isinstance(base, ast.Attribute) and base.attr == "BaseResource")
                for base in node.bases
            )
        }
        if found == resources:
            return resources
        resources = found


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


def evaluate_tree(root: Path) -> list[tuple[str, str, str]]:
    errors: list[tuple[str, str, str]] = []
    implementation = implementation_modules(root)
    for path in sorted((root / "plane").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        package = ".".join(path.relative_to(root).parent.parts)
        imports = imported_modules(path.read_text(encoding="utf-8"), package)

        leaked_transport = {name for name in TRANSPORT_IMPORTS if imports_package(imports, name)}
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
            resources = resource_classes(classes, resource_base_aliases(source, package))
            if resources and not path.startswith("plane/api/"):
                errors.append(
                    (
                        "SDK006",
                        path,
                        f"BaseResource subclass must live under plane/api: {', '.join(resources)}",
                    )
                )
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
