# Copyright (c) 2026-present Ngo Quoc Huy
# SPDX-License-Identifier: MIT

import argparse
import re
import subprocess
from pathlib import Path

COPYRIGHT = "Copyright (c) 2026-present Ngo Quoc Huy"
SPDX = "SPDX-License-Identifier: MIT"
HEADER = f"# {COPYRIGHT}\n# {SPDX}\n\n"
LICENSE_PATTERN = re.compile(
    r'^license\s*=\s*(?:\{\s*text\s*=\s*"MIT"\s*\}|"MIT")\s*$',
    re.MULTILINE,
)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def paths(repo: Path, base: str, status: str) -> set[str]:
    output = git(
        repo,
        "diff",
        "--name-only",
        f"--diff-filter={status}",
        "-z",
        base,
        "--",
        "*.py",
    )
    result = set(output.decode().split("\0"))
    if status == "A":
        result.update(
            git(repo, "ls-files", "--others", "--exclude-standard", "-z", "*.py")
            .decode()
            .split("\0")
        )
    return {path for path in result if path and "/migrations/" not in f"/{path}"}


def transform(source: str) -> str:
    if COPYRIGHT in source[:1024] and SPDX in source[:1024]:
        return source

    lines = source.splitlines(keepends=True)
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    if insert_at < len(lines) and re.match(r"#.*coding[:=]", lines[insert_at]):
        insert_at += 1
    lines.insert(insert_at, HEADER)
    return "".join(lines)


def notices(source: str) -> set[str]:
    return {
        line.strip()
        for line in source[:1024].splitlines()
        if "Copyright" in line or "SPDX-License-Identifier:" in line
    }


def check(repo: Path, base: str) -> list[str]:
    invalid: list[str] = []
    added = paths(repo, base, "A")
    modified = paths(repo, base, "M")

    for path in sorted(added):
        source = (repo / path).read_text()
        if COPYRIGHT not in source[:1024] or SPDX not in source[:1024]:
            invalid.append(f"{path}: missing fork copyright header")

    for path in sorted(modified):
        before = git(repo, "show", f"{base}:{path}").decode()
        after = (repo / path).read_text()
        if not notices(before).issubset(notices(after)):
            invalid.append(f"{path}: removed an inherited legal notice")

    if not LICENSE_PATTERN.search((repo / "pyproject.toml").read_text()):
        invalid.append("pyproject.toml: license must be MIT")

    return invalid


def run() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--changed-from")
    parser.add_argument("--added-from")
    args = parser.parse_args()
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    base = args.changed_from if args.check else args.added_from
    if not base:
        parser.error("--check requires --changed-from; --write requires --added-from")

    if args.write:
        changed = 0
        files = paths(repo, base, "A")
        for path in sorted(files):
            target = repo / path
            source = target.read_text()
            updated = transform(source)
            if updated != source:
                target.write_text(updated)
                changed += 1
        print(f"Copyright headers: {len(files)} checked, {changed} changed.")
        return 0

    invalid = check(repo, base)
    if invalid:
        print("\n".join(invalid))
        return 1
    print("Copyright and license checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
