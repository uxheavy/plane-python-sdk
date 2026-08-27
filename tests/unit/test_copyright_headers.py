# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

import unittest

from scripts.copyright_headers import COPYRIGHT, SPDX, transform


class CopyrightHeadersTest(unittest.TestCase):
    def test_transform_is_idempotent_and_preserves_shebang(self) -> None:
        source = "#!/usr/bin/env python3\nprint('hello')\n"
        transformed = transform(source)

        self.assertTrue(transformed.startswith("#!/usr/bin/env python3\n"))
        self.assertIn(COPYRIGHT, transformed[:1024])
        self.assertIn(SPDX, transformed[:1024])
        self.assertEqual(transform(transformed), transformed)
