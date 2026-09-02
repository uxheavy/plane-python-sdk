# Repository Map

## Scope

This file governs the Plane Python SDK repository.

## Canonical Commands

- `python -m pytest tests/provider_free` — provider-free unit tests.
- `python -m ruff check <changed.py...>` — changed-file lint and import checks.
- `python -m ruff format --check <changed.py...>` — changed-file formatting check.
- `python -m mypy scripts/repository_policy.py` — policy checker type check.
- `python scripts/repository_policy.py --base <ref>` — architecture and repository policy.
- `python -m build` — package build.

## Canonical Owners

| Concern | Owner |
| --- | --- |
| Client composition and authentication | `plane/client/` |
| API operations | `plane/api/` |
| HTTP, retry, URL, and response behavior | `plane/api/base_resource.py` |
| Request and response DTOs | `plane/models/` |
| SDK exceptions | `plane/errors/` |
| Provider-free contracts | `tests/provider_free/` |
| Opt-in live workflows | `tests/scripts/` |
| Full-repository lint/type debt | CI baselines; ratchet changed files without claiming the baseline is clean |

## Boundaries

- API resources reuse `BaseResource`; transport dependencies stay in the registered transport allowlist.
- Models and errors do not import client or API implementation modules.
- Unit proof is provider-free. Do not represent live scripts as unit-test evidence.

## Evidence and Authority

- Run the narrowest affected unit test before the full provider-free suite.
- Report skipped, blocked, unavailable, and live checks explicitly.
- CI is merge evidence; live API execution requires separate credentials and approval.
