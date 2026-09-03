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
pip install git+https://github.com/texasdaddy/kw-common@v1.1.0
```

In a `requirements.in` / `requirements.txt`:

```
kw-common @ git+https://github.com/texasdaddy/kw-common@v1.1.0
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
| `kw_common.leakguard` | The internal-information leak guard: shape-based scans of the tracked tree, the index, and the commits a push publishes — plus the `kw-leak-guard` command. |

Modules are **independently importable**. `import kw_common` pulls in nothing; take what you need:

```python
from kw_common.alerting import (
    AlertSettings, configure, notify, warn_if_unconfigured, OK, WARN, ERROR,
)

configure(AlertSettings(
    service="my-service",
    config_file="/etc/my-service/alerting.env",       # email settings; None = no email channel
    ntfy_url="https://ntfy.example.com/my-topic",     # https only; "" = no ntfy channel
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

⚠️ **An ntfy topic URL must be `https://`.** It is a *write capability*, not an address: anyone who
observes it can page you from then on, and over cleartext the URL, the alert `Title` and the body
are all readable by anything on the path. A `http://` URL turns the channel off, loudly, at boot.
For a self-hosted endpoint on a trusted network, `AlertSettings(allow_cleartext_ntfy=True)` is the
supported way to accept that exposure deliberately. Userinfo (`https://user:pass@host/topic`) is
refused outright — `urllib` cannot send it, and its failure printed the password.

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
.leakguard.json         THIS repository's own leak-guard allowances (see below)
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

## The leak guard

`kw_common.leakguard` is the fleet's internal-information guard. It used to be a file that seven
repositories each kept a copy of — with one of its test suites in five of them — so a fix made in
one reached none of the others, and four separate engine improvements had to be re-ported by hand.
It is a module here for the same reason everything else is: a consumer pins a version.

Installing the package puts `kw-leak-guard` on the path:

```
kw-leak-guard --selftest   # prove the shipped patterns still bite (reads no repository)
kw-leak-guard              # scan the tracked tree
kw-leak-guard --staged     # scan what a commit would record
kw-leak-guard --range origin/main..HEAD    # scan what a push would publish
```

`python -m kw_common.leakguard` does the same thing and is equally supported.

CI runs the self-test, the tree scan **and** a commit-range scan — different questions. The tree
scan asks "is it here now" and reads tracked files only; the range scan reads what each commit
*added*, so it also catches a value that was committed and then deleted, which stays permanently
readable at the commit that added it.

### Configuring it — from YOUR repository, never by editing the install

A repository declares its own allowances in **`.leakguard.json`** at its root (or a path given
with `--config`). Nothing in the installed package is edited, so `pip install --upgrade` cannot
revert your rules and your rules cannot hold back an upgrade.

```json
{
  "_note": "a key prefixed with `_` is a comment - the only spelling this scanner ignores",
  "allow_literals": [
    {"literal": "github.com/<owner>", "why": "functional: this repository's own clone URL"}
  ],
  "path_exempt": [
    {"pattern": "private lan domain", "path_regex": "^tests/fixtures/",
     "why": "synthetic hosts, needed to prove the pattern still bites"}
  ]
}
```

* **`allow_literals`** suppresses a hit that falls *inside* an exact literal. It never deletes
  text and never skips a line, so a permitted token cannot grant amnesty to a real leak sharing
  the line with it.
* **`path_exempt`** excuses **one named pattern** on **one path regex**. It is never a whole-file
  skip: `pattern` must name a pattern the engine actually has, or the config is refused.
* **`why` is required on every entry.** "Keep each one justified" is enforced rather than
  requested.

Five properties are deliberate and worth knowing before you write one:

1. **No config means nothing is allowed.** A missing or misnamed file can only ever make the scan
   *stricter*.
2. **The config must be TRACKED.** A `.leakguard.json` that is gitignored, or simply never added,
   is refused — because the whole justification for injecting the allowances is that they become
   reviewable, and a file nobody can see in the repository governs every local scan while
   `git status` stays empty. `--config <path>` is exempt from that rule: it is written out in the
   invocation, which is the visibility that matters.
3. **A config this scanner cannot understand STOPS the scan** (exit 2) — it never degrades to "no
   config". An unknown key, a missing `why`, a regex that will not compile, a `--config` path that
   does not exist: all errors. A silently ignored rule is worse than no rule, because a clean
   verdict looks identical either way. (A UTF-8 BOM is fine — Windows editors write them.)
4. **A config that would gut the guard is refused.** Applying your allowances must not stop any of
   the engine's own deny cases being caught, on any of the three surfaces (content, paths,
   messages) or on any ordinary path. So an `allow_literals` entry as wide as a pool root — the
   `/mnt/<pool>` prefix itself, spelled out — is refused, and so is any `path_exempt` regex wide
   enough to match an ordinary file: `.`, `.+`, `.*`, `(tests/fixtures/)?`. A scoped one such as
   `^tests/fixtures/` is accepted and honoured. (This paragraph cannot show you the refused
   literal, for the reason the placeholder section below explains: the guard scans this file.) What
   the check does *not* catch is written down beside it in the code: the corpus holds one sample
   per pattern, so single real values can still be allowed one explicit, justified line at a time.
5. **The guard skips at most one file: its own source, recognised as its own.** Either it is
   literally this file (the guard is vendored inside the repository being scanned), or the file's
   *bytes* are the running guard's own bytes. Nothing else can claim that, and no config can widen
   it — change one character of the guard's source and it is scanned like any other file.
   (Earlier versions fell back to a hardcoded `scripts/check_no_internal_info.py`, which silently
   exempted whatever a repository happened to keep at that path — the path every repository in
   this fleet vendored its copy at.)

This repository's own `.leakguard.json` is the worked example, and the test suite reads the real
file rather than a fixture so that a broken config here fails a test here. Note what it does
**not** contain: any `path_exempt` entry for the engine's own source. The first version of it
excused all eight patterns on that one path, which is a whole-file skip written in data — a real
leak appended to the engine's source passed. Property 5 is what replaced it.

### This repository is PUBLIC

Internal-information hygiene is load-bearing, and it binds this module more than any other: the
engine ships **inside the wheel**, so anything written in it is published to everyone who installs
the library. It names no private repository, and a test enforces that.

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
kw-leak-guard --selftest
```

```
python -c "from kw_common import leakguard as g; \
print(g.scan_text('YOUR PLACEHOLDER HERE', g.compile_patterns()) or 'allowed')"
```
