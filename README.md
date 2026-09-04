# kw-common

Shared, **stdlib-only** building blocks for Python services.

This library exists to end code duplication that was previously handled by copying files between
repositories. That copying was not hypothetical: one guard script and one of its test suites had
been copied into repository after repository, each copy drifting independently, and one service's
copy of the alerting module had forked outright. A copy that drifts is not one implementation
deployed several times — it is several implementations, of which at most one is current.

**Identity here is enforced by construction.** A consumer pins a version and installs it; there is
no port, no alignment audit, and no "which copy is the good one" question to answer later.

## Install

Consumers install from git at an **exact tag** — never a branch:

```
pip install git+https://github.com/texasdaddy/kw-common@v1.3.0
```

In a `requirements.in` / `requirements.txt`:

```
kw-common @ git+https://github.com/texasdaddy/kw-common@v1.3.0
```

⛔ **Never pin a branch.** `@main` makes every rebuild of every consumer a silent, unreviewed
upgrade — the failure this library was created to remove, arriving from the other direction.
Bumping a consumer is a deliberate pull request in that consumer's repository.

Releases follow semver. A breaking change to a name in a module's `__all__` is a MAJOR bump, and
its release notes name every exported symbol that changed, so a consumer can tell in one read
whether it is affected. ⛔ They do NOT name the consumers: this repository is public, and an
inventory of who installs the library is the operator's estate rather than the library's
documentation.

## What is in it

| Module | What it is |
| --- | --- |
| `kw_common.alerting` | The `notify(severity, title, message)` contract: fan-out to email + ntfy, edge-trigger vs. escalating de-duplication, and a small retrievable JSONL error log. |
| `kw_common.alerting_env` | The deployment layer `alerting` refuses to be: one shared config file plus three variables in, `AlertSettings` out — and the boot check that refuses to start when they are wrong. |
| `kw_common.leakguard` | The internal-information leak guard: shape-based scans of the tracked tree, the index, and the commits a push publishes — plus the `kw-leak-guard` command. |

`import kw_common` pulls in nothing, and no module drags in one you did not ask for — `alerting`
and `leakguard` share not a line. (`alerting_env` is the one deliberate dependency: it RETURNS an
`AlertSettings`, so it imports `alerting` and nothing else.) Take what you need:

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

## Ops alerting configuration — `kw_common.alerting_env`

`alerting` takes settings and assumes nothing about where they came from. **`alerting_env` is
where the deployment knowledge lives**, and keeping the two apart is what lets a consumer outside
this deployment use the first without inheriting the second.

The convention it makes executable: **one shared file, three variables.**

| variable | mode | what it is |
| --- | --- | --- |
| `SHARED_ROOT` | read-only | carries `configs/alerting.env` and nothing else |
| `CONFIG_PATH` | read-write | the app's **own** directory; it writes its boot marker here |
| `DEPLOY_ENV` | — | `prod` or `dev`, case-insensitive — lowercased inside the library |

```python
from dataclasses import replace

from kw_common.alerting import configure, warn_if_unconfigured
from kw_common.alerting_env import load_alert_settings_from_env, validate_boot_from_env

settings = replace(
    load_alert_settings_from_env("my-service"),   # reads SHARED_ROOT + DEPLOY_ENV
    state_file="/data/my-service/alert-state.json",
    error_log="/data/my-service/logs/errors.log",
)
alerter = configure(settings)
warn_if_unconfigured("my-service")
validate_boot_from_env(settings, alerter=alerter)   # raises AlertEnvError -> refuse to boot
```

What it decides, and why each is not left to the caller:

* **The topic.** `NTFY_URL_<SERVICE>` if the shared file carries one, else this environment's
  `NTFY_URL_DEV` / `NTFY_URL_PROD`.
* **The title prefix, which is a property of the TOPIC and not of the service.** On a shared topic
  nothing else says who is talking, so titles carry `[<env>][<service>]`. On a service's own topic
  the topic is already the identifier, so there is no prefix. It reaches the email subject and the
  ntfy title and **nothing else** — the de-duplication key and the error-log record keep the raw
  title, so promoting a service from dev to prod does not re-page every condition it had already
  reported.
* **What "required" means, per environment**, once, next to the loader — otherwise every app
  answers "what does prod require?" independently and they disagree the first time a key is added.
  `SMTP_USER` is optional and defaults to `EMAIL_FROM` — but only when that is a bare ASCII
  mailbox. They are the same value in this deployment today and are different things (the auth
  identity and the header), and a From header carrying a display name is not a credential: copying
  it into `SMTP_USER` would make a channel that fails every send report itself READY.

There is **no default for any of the three variables, and no default path anywhere in the module.**
A missing variable raises at boot. A default would point at the wrong deployment silently, and the
symptom of that is an incident that produced no page.

`validate_boot_from_env` checks the mount, the structure, the file, every key this environment
requires — including that none is still the template's `CHANGE-ME` — and that every configured
channel is one the library can actually SEND on. That last one matters more than it sounds:
`NTFY_URL_PROD=my-topic` (a bare topic instead of the full URL) is non-blank, and a check that
asked only "is there a value" would pass it, announce that the configuration checks out, and leave
the channel dead. It asks `alerting`'s own readiness functions rather than re-deriving the answer.

On success it sends **one** confirmation alert and writes a marker into `CONFIG_PATH` recording the
config file's **sha256**; later boots skip while that digest still matches. The marker is **per
environment**: with one marker for all of them, promoting a service from dev to prod — which does
not touch the shared file — skipped validation entirely and the service came up with no topic at
all. **On failure it logs and raises and does not attempt to alert** — it cannot report a broken
alerting channel through that channel — and with no `Alerter` installed it validates but withholds
the marker, so the confirmation is not lost to a boot that could not send it.

⭐ **Contents decide, not timestamps.** The marker used to be empty and the comparison used to be
"is the marker newer than the config" — and `rsync -a`, `cp -p`, `tar -x` and volume restores all
preserve mtime, so a config restored at an older timestamp was never re-checked: a broken file
booted clean and the service came up alerting nobody. A digest answers that whatever the clock
says, in both directions — a restore with different bytes re-validates, an identical one does not.
Upgrading from a version that wrote an empty marker costs exactly one re-validation, silently.

📄 **Setup — the folder structure, the template and per-OS commands: [`docs/alerting-setup.md`](docs/alerting-setup.md).**
A consumer README carries a short prerequisite block and a **link** to that page, never a copy; a
prose copy per consumer is the same duplication problem relocated into documentation.

## The generic-code contract

Every module here satisfies all of the following. A module that cannot is not ready to live in a
shared library, and saying so is a better outcome than bending the rule.

1. **It imports cleanly in isolation.** Copy the module alone into an empty directory and import
   it: no consumer module appears in `sys.modules`, and nothing outside the standard library does
   either. **The "no other `kw_common` module" half binds every module EXCEPT `alerting_env`**,
   which imports `alerting` because it exists to return an `AlertSettings` — a stated, single,
   one-directional dependency, not an exception that grows. `alerting` must never import it back.
   *(Enforced for `alerting` by `tests/test_isolation.py`, which does exactly that in a
   subprocess, and for `leakguard` by a narrower check in `test_leak_guard_extraction.py`.
   Generalising that file over every module is issue #12 — it binds one `MODULE_PATH` today.)*
2. **Configuration is INJECTED, never assumed.** No hardcoded filename, environment-variable name,
   path, topic or service name. The caller passes what it uses. Anything a consumer must customise
   is a parameter or a registered hook — **never a constant the consumer is expected to edit after
   install**, because an edited install is a fork, which is the thing this repository exists to
   stop. *(Enforced against the AST: `alerting` may not read `os.environ`, and no module-level
   constant may hold an absolute path.)*
   **`alerting_env` is the deliberate exception, and it is scoped rather than waived**: reading
   the environment IS its job, so the AST check asked of it is a different one — it may read only
   the three documented variables, through one helper, and it still may not hold an absolute path.
   Injection is not "nobody reads the environment"; it is "exactly one module does, and the module
   that does the work does not."
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
src/kw_common/          the library (py.typed — the annotations are usable by a consumer's mypy)
tests/                  the suite, run on 3.10 and 3.12
docs/                   alerting-setup.md — the ONE setup page consumers point at
alerting.env.template   the annotated shared alerting config, for an operator to fill in
.leakguard.json         THIS repository's own leak-guard allowances (see below)
.githooks/pre-push      refuses a push that would publish internal information (see below)
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

`kw_common.leakguard` is the fleet's internal-information guard. It used to be a file each
repository kept its own copy of — with one of its test suites copied alongside it — so a fix made
in one reached none of the others, and four separate engine improvements had to be re-ported by
hand. It is a module here for the same reason everything else is: a consumer pins a version.

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
  ]
}
```

* **`allow_literals`** is the whole surface. It suppresses a hit that falls *inside* an exact
  literal — it never deletes text, never skips a line, and cannot remove or widen a pattern, so a
  permitted token cannot grant amnesty to a real leak sharing the line with it.
* **`why` is required on every entry.** "Keep each one justified" is enforced rather than
  requested.

Five properties are deliberate and worth knowing before you write one:

1. **No config means nothing is allowed.** A missing or misnamed file can only ever make the scan
   *stricter*.
2. **The guard reads the config from the INDEX, not from your working copy.** So the rules that
   govern a scan are exactly the rules a commit would record — `git add` an edit before expecting
   it to apply, and the guard says so if the two differ. A file on disk that the index does not
   have is an error rather than silence. This is what makes the allowances reviewable, which is the
   whole justification for injecting them; the earlier version asked whether the path was *tracked*
   and then read the *file*, and `git update-index --skip-worktree` drove those apart with
   `git status` staying empty. `--config <path>` is exempt: it is written out in the invocation,
   which is the visibility that matters, and may legitimately live outside the repository.
3. **A config this scanner cannot understand STOPS the scan** (exit 2) — it never degrades to "no
   config". An unknown key, a missing `why`, a `--config` path that does not exist: all errors. A
   silently ignored rule is worse than no rule, because a clean verdict looks identical either way.
   (A UTF-8 BOM is fine — Windows editors write them.)
4. **A config that stops the guard's own DENY CASES being caught is refused** — on any of the
   three surfaces: file content, file paths, commit messages. Read that literally, because it is
   narrower than it sounds and an earlier version of this bullet overstated it. The check is
   corpus-shaped: it refuses a literal that silences one of the engine's finite set of samples,
   so the pool root the corpus happens to spell is refused and *another pool name is not*.
   And an accepted literal is not narrow — allowing a pool root silences that pool everywhere,
   because the match for that pattern is the pool root itself.
   So treat this as a backstop against the careless case, not as a proof. What actually keeps an
   allow-list honest is that every entry is an exact literal, spelled out, with a required
   justification, in a file read from the index.
5. **The only file whose CONTENT the guard skips outright is its own source, recognised as its
   own.** (A `SKIP_SUFFIXES` asset whose bytes really are binary is skipped too, but that is a
   read the guard would learn nothing from — rename a text file to `.png` and it is still
   scanned.) Either the guard's own source is
   literally that file — the guard is vendored inside the repository being scanned, or installed
   in editable mode from it — or the file's *bytes* are the running guard's own bytes. Nothing
   else in your repository can claim either. (Earlier versions fell back to a hardcoded
   `scripts/check_no_internal_info.py`, which silently exempted whatever a repository happened to
   keep at that path — the path every repository in this fleet vendored its copy at.)

   ⚠️ Stated precisely, because the obvious summary of it is wrong: when the running guard IS the
   repository's own file, that file's content is skipped **unconditionally**, and appending a real
   leak to it would not be caught by this guard. That is inherent in a guard whose source carries
   its own deny corpus, it is the one exemption the engine has always had, and the layer that
   covers that file is a separate real-literal check run from outside the repository — which
   deliberately does *not* skip it.

### There is no path-exemption setting, and that is the result of two failed designs

A repository briefly could excuse one named pattern on one path regex. Both attempts to bound
that regex were fail-open, one review round apart:

* the first never measured it at all, so `{"path_regex": "."}` turned a pattern off across a whole
  tree and the config was accepted;
* the second measured the deny corpus at a list of "ordinary" probe paths — which is a nine-name
  allowlist wearing the word "property". `\.go$`, `^internal/` and `^terraform/` sailed past it and
  were total against a real repository, a negative lookahead over the nine names turned a pattern
  off everywhere in one line, and it *refused* the narrowest exemption there is (a single file)
  whenever that file was called `README.md` or `Dockerfile`.

No acceptance criterion asked for it — what was asked for is an allow-list — so it was withdrawn
rather than repaired a third time. A config naming `path_exempt` is refused with a message saying
so. `PATH_EXEMPT` survives as an engine constant for the project-side guard that shares this
engine; changing it takes an edit to the module, which is a reviewed change rather than a line of
data.

This repository's own `.leakguard.json` is the worked example, and the test suite reads the real
file rather than a fixture so that a broken config here fails a test here.

### Before you push — the pre-push hook

The shape-based guard above cannot answer one question: a **name** has no shape to match. The list
of names that must not appear in this repository cannot live in this repository either, because it
would then be published by the very artifacts it exists to keep clean — that was measured, not
theorised. So the list lives in a guard that is committed to no repository at all, and
`.githooks/pre-push` is what runs it, at the moment publication actually happens.

Install it once per clone:

```
git config core.hooksPath .githooks && git config kw.privateGuard "<absolute path to the project-side guard>"
```

It scans the tracked working tree (what the wheel and the sdist are built from) and the commits
each ref would publish, and it **refuses the push** on any finding — and refuses outright when
`kw.privateGuard` is unset or does not name a file, because a guard that is silently not running
looks exactly like a pass.

Two pushes are not scanned against the working tree, because they **publish nothing**: one that
only DELETES refs, and one git reports as `Everything up-to-date` (it runs the hook with an empty
ref list). Both go through even when the worktree still holds the value you are cleaning up —
which is the point, since refusing them blocks the cleanup itself. Anything either push *does*
publish is still scanned commit by commit.

⚠️ The unconfigured-guard refusal comes FIRST, before the ref list is read, so it applies to those
two as well: on a fresh clone with `core.hooksPath` set and no `kw.privateGuard`, even a deletion
is refused — and the message names the config key rather than your tree.

⚠️ **Declared bounds.** A hook is a local convention. Nothing in this repository, and nothing in
CI, can assert that it ran on somebody's machine — CI must not have the list either. And
`core.hooksPath .githooks` is a RELATIVE path, resolved inside the working tree, so a checkout
that predates the hook has none and git says nothing: see issue #17, and the hook's own header.
`tests/test_leak_guard_hook.py` drives the real hook against a real `git push` and asserts whether
the remote ref moved; that is what can be checked here, and it says so rather than implying more.

### This repository is PUBLIC

Internal-information hygiene is load-bearing, and it binds this module more than any other: the
engine ships **inside the wheel**, so anything written in it is published to everyone who installs
the library.

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
