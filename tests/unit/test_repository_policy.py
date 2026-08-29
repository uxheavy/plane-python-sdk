# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "repository_policy.py"
_SPEC = importlib.util.spec_from_file_location("repository_policy", _PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("Failed to load repository_policy.py")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class RepositoryPolicyTests(unittest.TestCase):
    def test_rejects_transport_import_outside_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "plane" / "models" / "leak.py"
            path.parent.mkdir(parents=True)
            path.write_text("import requests\n", encoding="utf-8")
            self.assertEqual(_MODULE.evaluate_tree(root)[0][0], "SDK001")

    def test_accepts_transport_import_at_registered_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "plane" / "api" / "base_resource.py"
            path.parent.mkdir(parents=True)
            path.write_text("import requests\n", encoding="utf-8")
            self.assertEqual(_MODULE.evaluate_tree(root), [])

    def test_rejects_new_generic_roots_and_tracked_outputs(self):
        errors = _MODULE.evaluate_changes(
            [("A", "helpers/new.py"), ("A", "dist/package.whl")],
            lambda _path: False,
        )
        self.assertEqual([error[0] for error in errors], ["SDK003", "SDK004"])

    def test_grandfathers_existing_generic_roots(self):
        self.assertEqual(
            _MODULE.evaluate_changes([("A", "helpers/new.py")], lambda path: path == "helpers"),
            [],
        )

    def test_parses_renames(self):
        self.assertEqual(
            _MODULE.parse_name_status(b"R100\0old.py\0plane/api/new.py\0"),
            [("R", "plane/api/new.py")],
        )
