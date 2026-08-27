## Copyright and license checks

New Python files in this fork use the `Ngo Quoc Huy` copyright header.
Existing Plane and third-party notices must be preserved. Package metadata
must continue to declare the MIT license.

Check all rules:

```bash
python scripts/copyright_headers.py --check --changed-from origin/main
```

Apply headers to new Python files:

```bash
python scripts/copyright_headers.py --write --added-from origin/main
```

CI runs the same check through `.github/workflows/copyright-check.yml`.
