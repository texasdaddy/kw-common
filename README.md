# kw-common

Shared, **stdlib-only** building blocks for a small fleet of Python services.

This library exists to end code duplication that was previously handled by copying files between
repositories. That copying was measured, and it was not hypothetical: one guard script lived in
**seven** repositories and one of its tests in **five**, each drifting independently, and one
service's copy of the alerting module had forked outright. A copy that drifts is not one
implementation with six deployments — it is six implementations, of which at most one is current.

**Identity here is enforced by construction.** A consumer pins a version and installs it; there is
no port, no alignment audit, and no "which copy is the good one" question to answer later.

## Install

Consumers install from git at an **exact tag** — never a branch:

```
pip install git+https://github.com/texasdaddy/kw-common@v1.0.0
```

In a `requirements.in` / `requirements.txt`:

```
kw-common @ git+https://github.com/texasdaddy/kw-common@v1.0.0
```

⛔ **Never pin a branch.** `@main` makes every rebuild of every consumer a silent, unreviewed
upgrade — the failure this library was created to remove, arriving from the other direction.
Bumping a consumer is a deliberate pull request in that consumer's repository.

Releases follow semver. A breaking change to a name in a module's `__all__` is a MAJOR bump, and
the release notes name every consumer that needs a code change.

## What is in it

| Module | What it is |
| --- | --- |
| `kw_common.alerting` | The `notify(severity, title, message)` contract: fan-out to email + ntfy, edge-trigger vs. escalating de-duplication, and a small retrievable JSONL error log. |

Modules are **independently importable**. `import kw_common` pulls in nothing; take what you need:

```python
from kw_common.alerting import (
    AlertSettings, configure, notify, warn_if_unconfigured, OK, WARN, ERROR,
)

configure(AlertSettings(
    service="my-service",
    config_file="/etc/my-service/alerting.env",       # email settings; None = no email channel
    ntfy_url="https://ntfy.example.com/my-topic",     # "" = no ntfy channel
    state_file="/data/my-service/alert-state.json",   # None = de-duplication OFF
    error_log="/data/my-service/logs/errors.log",     # None = no error-log sink
))

warn_if_unconfigured("my-service")   # at boot: says loudly if alerts would go nowhere

notify(ERROR, "my-service: backup failed", "rsync exited 23", escalating=True)
notify(OK, "my-service: backup ok", "12.4 GB", clears="my-service: backup failed")
```

Everything the module needs is passed in. It reads no environment variable and has no default
path — see the contract below, and the module's own docstring for the full behaviour (severities,
`escalating=`, `clears=`, and the error log's exposure warning).

## The generic-code contract

Every module here satisfies all of the following. A module that cannot is not ready to live in a
shared library, and saying so is a better outcome than bending the rule.

1. **It imports cleanly in isolation.** Copy the module alone into an empty directory and import
   it: no consumer module appears in `sys.modules`, and neither does any other `kw_common` module.
   *(Enforced by `tests/test_isolation.py`, which does exactly that in a subprocess.)*
2. **Configuration is INJECTED, never assumed.** No hardcoded filename, environment-variable name,
   path, topic or service name. The caller passes what it uses. Anything a consumer must customise
   is a parameter or a registered hook — **never a constant the consumer is expected to edit after
   install**, because an edited install is a fork, which is the thing this repository exists to
   stop. *(Enforced against the AST: the module may not read `os.environ`, and no module-level
   constant may hold an absolute path.)*
3. **Stdlib-first.** Every third-party dependency is a supply-chain surface across the whole
   fleet at once. `dependencies` in `pyproject.toml` is empty, and a new entry needs a stated
   justification rather than a convenience argument.
4. **Python 3.10+**, tested on **3.10 and 3.12** — the split that has reddened fleet CI twice. A
   library that claims 3.10 and is only ever run on 3.12 is claiming something nobody measured.
5. **Independently importable modules.** `kw_common.alerting` must not drag in anything else, and
   `__init__.py` deliberately re-exports nothing.
6. **Tests travel with the module.** A behaviour that is not tested here becomes N untested copies
   the moment it ships.
7. **The public API is explicit** — `__all__` per module. Anything not exported may change without
   a major bump; anything exported is a contract under semver.

## Repository layout

```
src/kw_common/          the library
tests/                  the suite, run on 3.10 and 3.12
scripts/                the leak guard (see below)
.github/workflows/      ci.yml (PR + main) and release.yml (v* tags)
```

## Development

```
python -m pip install -e .
python -m pip install "ruff==0.16.1" "mypy==2.3.0" "pytest==9.1.1" "pytest-timeout==2.4.0"

ruff check .
mypy
python -u -m pytest
```

Branching is single-branch: `feature/*` / `fix/*` → pull request → `main`. There is no `dev`
line, no container and no registry — a release is an annotated `v*` tag, and `release.yml` builds
the wheel and sdist and attaches them to a GitHub Release.

The tag and `kw_common.__version__` must agree; the release workflow refuses the publish if they
do not. `src/kw_common/__init__.py` is the single source of the version — `pyproject.toml` reads
it dynamically.

## This repository is PUBLIC

Internal-information hygiene is therefore load-bearing, and the guard runs from day one:

```
python scripts/check_no_internal_info.py --selftest   # prove the patterns still bite
python scripts/check_no_internal_info.py              # scan the tracked tree
python scripts/check_no_internal_info.py --staged     # scan what a commit would record
```

CI runs the self-test, the tree scan, **and** a commit-range scan — two different questions. The
tree scan asks "is it here now" and reads tracked files only; the range scan reads what each
commit *added*, so it also catches a value that was committed and then deleted, which stays
permanently readable at the commit that added it.

`scripts/check_no_internal_info.py` is a **verbatim copy** of the canonical guard from
`texasdaddy/unraid-templates` (`main`, commit `eecad1a`). It is not edited here — a local fix
would make it a fork, and a forked guard is exactly the drift this library exists to end. Improve
it upstream and re-copy the whole file.

Placeholders in code, tests, comments and documentation come from the guard's own `_MUST_PASS`
corpus — the list of shapes it is pinned to *allow*: `example.com` and `*.example` for hosts, the
RFC 5737 documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) for addresses,
and `/mnt/POOL/appdata/<app>` or the container path the code actually uses for filesystem paths.
"Realistic" is not a reason to write a real value.

⚠️ `*.invalid` is **not** in `_MUST_PASS`, and it is not a blanket-safe suffix. `example.invalid`
passes, but the freemail pattern matches on the mail *provider* and ignores the TLD — so an
address at one of the big consumer mail providers is a **denied** shape even with `.invalid`
stuck on the end, and it reddens CI on a line containing no real data.

That paragraph cannot show you the failing example, and the reason is worth knowing: the guard
scans this file too, so writing the denied literal here would redden CI on the very sentence
warning you about it. Ask the guard instead of guessing — if a placeholder is not already in
`_MUST_PASS`, check it before committing:

```
python scripts/check_no_internal_info.py --selftest
```

```
python -c "import sys; sys.path.insert(0,'scripts'); import check_no_internal_info as g; \
print(g.scan_text('YOUR PLACEHOLDER HERE', g.compile_patterns()) or 'allowed')"
```
