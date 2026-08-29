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


def imported_modules(source: str, package: str = "") -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: max(0, len(parts) - node.level + 1)]
                if node.module:
                    parts.extend(node.module.split("."))
                module = ".".join(parts)
            else:
                module = node.module or ""
            if module:
                modules.add(module)
                modules.update(f"{module}.{alias.name}" for alias in node.names)
            else:
                modules.update(alias.name for alias in node.names)
    return modules


def imports_package(modules: set[str], package: str) -> bool:
    return any(module == package or module.startswith(f"{package}.") for module in modules)


def implementation_reexports(root: Path) -> set[str]:
    init = root / "plane" / "__init__.py"
    if not init.is_file():
        return set()
    source = init.read_text(encoding="utf-8")
    exports: set[str] = set()
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ImportFrom):
            continue
        modules = imported_modules(ast.unparse(node), "plane")
        if not any(imports_package(modules, owner) for owner in ("plane.api", "plane.client")):
            continue
        exports.update(f"plane.{alias.asname or alias.name}" for alias in node.names)
    return exports


def evaluate_tree(root: Path) -> list[tuple[str, str, str]]:
    errors: list[tuple[str, str, str]] = []
    root_implementation_exports = implementation_reexports(root)
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
            forbidden = {
                name
                for name in (
                    "api",
                    "client",
                    "plane.api",
                    "plane.client",
                    *root_implementation_exports,
                )
                if imports_package(imports, name)
            }
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
            if parts and parts[0] in GENERIC_ROOTS and not base_has_path(parts[0]):
                errors.append(
                    ("SDK003", path, f"new generic root {parts[0]} needs a concrete owner")
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
            resources = [
                node.name
                for node in classes
                if any(
                    (isinstance(base, ast.Name) and base.id == "BaseResource")
                    or (isinstance(base, ast.Attribute) and base.attr == "BaseResource")
                    for base in node.bases
                )
            ]
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
                if not node.name.startswith("_")
                and not any(
                    (isinstance(base, ast.Name) and base.id == "BaseResource")
                    or (isinstance(base, ast.Attribute) and base.attr == "BaseResource")
                    for base in node.bases
                )
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
