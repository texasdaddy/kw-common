# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A breaking change to a name in a module's `__all__` is a MAJOR bump, and its entry names every
consumer repository that needs a code change.

## [1.1.0] - 2026-09-02

The leak guard becomes a module. It was a **file that seven repositories each kept a copy of**,
with one of its test suites in **five** of them — so a fix made in one reached none of the others,
and four separate engine improvements had to be re-ported by hand after being made once upstream.
A copy cannot be pinned. This release ends that the same way the alerting module did: a consumer
installs a version.

Nothing here changes what a scan CATCHES. The detection patterns, the three scan shapes
(tree / index / commit range) and the four engine-add surfaces are the canonical behaviour and are
unchanged — verified by AST against the seven canonical fixes, by the shipped self-test, and by
the 4,200 lines of engine tests that moved here with the code.

### Added

- **`kw_common.leakguard`** — the engine, importable, with a **`kw-leak-guard`** console entry
  point (`python -m kw_common.leakguard` works identically). It imports nothing from the rest of
  the package and depends on nothing outside the standard library.
- **Injected configuration: `.leakguard.json` in the repository being scanned**, or a path given
  with `--config`. This is the part that makes the extraction possible at all — see the adopter
  notes below for the format and for the four fail-closed properties it was designed around.
- **`--config <path>` / `--config=<path>`** on the command line, and `GuardConfig`,
  `DEFAULT_CONFIG`, `ConfigError`, `find_config`, `load_config`, `parse_config`, `apply_config`,
  `CONFIG_FILENAME` and `cli` in the module's `__all__`.
- **A configuration that would defeat the guard is refused.** Applying a repository's allowances
  must not stop any of the engine's own deny cases being caught, or the scan stops with exit 2
  naming the case it defeats. Without this, moving the allowances into a file the consumer owns
  would have made "turn a whole pattern off across the tree, with CI still green" a one-line edit.

### Fixed

- **An installed guard no longer exempts an arbitrary file in the repository it scans** — a
  security fix, not tidying, and one whose FIRST answer was wrong in the same way. The self-exemption resolved `__file__` relative to the scanned root
  and **fell back to the constant `scripts/check_no_internal_info.py`** when that failed. It fails
  on **every run of an installed guard**, because `site-packages` is not inside the repository
  being scanned — so the fallback would have exempted whatever the consumer kept at that path,
  which is the exact path every repository in this fleet vendored its copy at. The first consumer
  to install this package and delete its old script would have handed a permanent, invisible
  amnesty to any file that later took that name. The resolver now answers "not mine" instead of
  guessing, and a repository that genuinely vendors the guard still skips its own copy.

  ⚠️ THAT LEFT THE SAME HOLE ONE LAYER OUT, and the verification gate found it. The repository
  that OWNS the engine keeps its source as an ordinary tracked file, full of synthetic deny cases
  — so with the path exemption gone, an installed guard reported all of them. The first answer was
  a `path_exempt` entry per pattern in that repository's own config; since the engine has exactly
  as many patterns as that list had entries, it was a whole-file skip written in data, and a REAL
  leak appended to the engine's source PASSED. The guard now recognises its own source by its
  BYTES: nothing else in any repository can claim that, and no configuration can widen it.
- **The finding banner no longer claims "this repo is public."** True of the one repository the
  guard was vendored into, false for most of the ones it now scans — and a sentence a reader uses
  to decide a finding does not apply to them. It states what holds everywhere instead: a commit is
  permanent.
- **The docstring's account of which repositories are public was wrong for the second time**, now
  in the opposite direction: it named one repository as the only public one, which was true when
  written and false by the time this module shipped from a second public repository — the one that
  publishes this file to everyone who installs the library. Both errors are recorded rather than
  quietly overwritten.
- **The private sibling repositories are no longer named anywhere in the engine.** `MANIFEST.in`
  had kept the guard out of the sdist for exactly that reason; the module now ships inside the
  **wheel**, so exclusion is no longer available as the fix. Provenance comments cite
  `consumer#NN`, and a test enforces the absence.
- **A `.leakguard.json` that is not tracked by git is refused.** An ignored config governed every
  local scan while being invisible to review, which is the opposite of the property that justifies
  injecting the allowances at all.
- **A UTF-8 BOM on an otherwise valid config no longer reddens the build.** Windows PowerShell 5.1
  and Notepad both write one; the config is read as `utf-8-sig`.
- **The `--staged` banner no longer claims "this repo is public" either.** The tree and range
  banners were corrected and this one was missed — the instance, not the class, inside an edit
  whose entire subject was a false claim.
- Two incidental repairs the move surfaced, neither of which changes a verdict: `zip(..., strict=)`
  where the lengths were already asserted equal, and a rebound name in `selftest()` that made the
  type checker right about a real type error.

  ⚠️ An earlier draft of this entry claimed a third — "a false-positive PATH report that
  interpolated the CONTENT loop's findings instead of its own". **That bug never existed**: the
  pre-move file assigns the name inside the same loop, one line above its use, so the report was
  always correct. A verification agent checked the claim against the original and it was false.
  Recorded rather than deleted, because a changelog that invents a fix is the same defect class as
  a comment that invents one.

### Notes for adopters

**This release does not change your CI's answer. It changes where the guard comes from.** A
repository adopting it deletes its vendored `scripts/check_no_internal_info.py`, pins
`kw-common@v1.1.0`, and calls `kw-leak-guard` instead of `python scripts/check_no_internal_info.py`.

⛔ **Do not delete your vendored copy without first moving its `ALLOW_LITERALS` tail into
`.leakguard.json`.** That tail is the per-repository configuration the whole extraction exists to
rescue, and the engine now ships with it **empty** — the library assumes nothing about its
consumer. Deleting the script without porting the tail turns your allowances off, which reddens CI
on lines that were previously, and correctly, permitted.

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

Five properties, each chosen in the fail-closed direction and each worth knowing before you write
a config:

1. **No config file means nothing is allowed.** A missing or misnamed file can only make a scan
   *stricter*. The opposite default — shipping this fleet's own allowances — would mean an
   installed library silently permitting values in a repository that never asked, and a consumer
   would have to edit installed code to tighten it, which is the fork this package exists to
   prevent.
2. **The config must be TRACKED.** A gitignored or never-added `.leakguard.json` is refused. The
   whole justification for injecting the allowances is that they become reviewable, and a file
   nobody can see governs every local scan — the pre-commit and pre-push paths especially — while
   `git status` stays empty. `--config <path>` is exempt: it is written out in the invocation.
3. **A config this scanner cannot understand STOPS the scan** (exit 2). It never degrades to "no
   config": an unknown or misspelled key, a missing `why`, a regex that will not compile, a
   `--config` path that does not exist. The difference between "your rules were applied" and "your
   rules were unreadable, so none were" is invisible in a clean verdict. A UTF-8 BOM is accepted —
   Windows editors write them, and reddening a correct config is how a guard gets switched off.
4. **`why` is required on every entry**, so "keep each one justified" is enforced rather than
   requested. `path_exempt` excuses ONE named pattern on ONE path regex; a `pattern` naming no real
   pattern is an error rather than an entry that quietly does nothing, and a regex wide enough to
   match an ordinary file (`.`, `.+`, `.*`, `(tests/fixtures/)?`) is refused as an off-switch. A
   scoped one — `^tests/fixtures/` — is accepted and honoured.
5. **The guard skips at most one file: its own source, recognised as its own.** Either it is
   literally that file (you vendored the guard inside the repository it scans), or the file's
   BYTES are the running guard's own bytes. Nothing else can claim that and no config can widen
   it: change one character of the guard's source and it is scanned like any other file.

**If your repository keeps a leak-guard corpus of its own** (synthetic deny cases, fixtures full of
`.lan` hosts), declare it with `path_exempt` per pattern, scoped to the directory that holds it.
This repository's own `.leakguard.json` is the worked example, and the suite reads the real file so
a broken config here fails a test here.

**What is NOT in this release:** adoption anywhere. Each consuming repository takes it as its own
change, and `unraid-templates` — where this engine came from — should re-adopt rather than keep its
byte-identical copy.

**A note for whoever ports the tests.** Five `.githooks`-shaped tests did not travel: they assert
how a repository WIRES the guard up (the Windows `command -v python3` stub, `--not --remotes` on a
brand-new branch, the executable bit git silently needs, and two that run a real `git commit` and
check that HEAD did not move). Every one is a real lesson and none is a property of the engine.
Keep them beside your hooks; each removal site here says what it asserted.

## [1.0.1] - 2026-09-01

The three inherited security defects found during the v1.0.0 extraction and filed rather than
silently changed (#2, #3, #4), fixed before the first consumer adopts. All three are byte-identical
to the reference implementation, so they are live in it today; adopting this release is what
removes them rather than carrying them forward into six copies.

### Security

- **A channel failure no longer renders a configured secret into the log** (#2). The per-channel
  failure line — which repeats on **every** alert, in the log an operator is most likely to paste
  into a bug report — now passes the exception text through a redactor holding this config's
  `SMTP_PASSWORD` and its ntfy topic URL. The certificate-verification branch and `notify()`'s own
  "failed before any channel could be tried" line are redacted with it; all three rendered the
  exception verbatim.
  Both demonstrated leaks are additionally closed **at their source**, because a substring
  redactor cannot reach a secret quoted in transformed form:
  - `ntfy_ready()` now refuses a URL carrying **userinfo** (`https://user:pass@host/topic`). Such
    a URL never worked — `urllib` hands the whole netloc to `http.client`, which raises
    `InvalidURL` **quoting it** — so this converts a channel that failed on every send while
    printing its password into one refusal at boot. The host is judged **as the send path
    resolves it**: `urlsplit` returns the raw netloc while `urllib.request.Request` `unquote`s
    it, so a URL written `https://user:pw%40host/topic` has no `@` for a naive check to see and
    still arrives at `http.client` as `user:pw@host`. Asking the send path's own parser closes
    that whole class rather than enumerating escapes; `%20` and `%0d` went the same way.
    The host is then handed to **`http.client` itself** to accept or refuse, rather than to a
    predicate imitating it. `http.client` has never looked for an `@`: `_get_hostport` splits on
    the LAST COLON and raises `InvalidURL` quoting what follows it — so the colon is the trigger
    and the `@` was incidental, and `https://tok:hunter2%2540host/t`, `https://tok:hunter2/t`
    (no `@` anywhere) and an ordinary port typo `https://host:abc/t` each reported ready and then
    printed their own configuration on every alert. Constructing the connection object runs the
    real `_get_hostport`/`_validate_host` and opens no socket.
  - **A host outside latin-1 is refused.** `urllib` pre-adds the `Host` header, so
    `putrequest`'s IDNA branch never runs and `putheader` encodes latin-1 — measured, correcting
    an earlier claim in this file that a non-ASCII hostname "genuinely works". Such a host was
    ready and permanently dead, with a `UnicodeEncodeError` quoting the character. A latin-1
    hostname (`nötify.example`) is still accepted, because it does go out.
  - `ntfy_ready()` now refuses a **non-ASCII topic path or query**. `http.client` ASCII-encodes
    the request line, and its `UnicodeEncodeError` prints the offending character plus an index
    into the topic — the ntfy sibling of the SMTP refusal below, and equally beyond redaction's
    reach. Percent-encode the topic. A non-ASCII **host** is still accepted: `http.client`
    IDNA-encodes it, so it genuinely works.
  - `_send_email()` now refuses a **non-ASCII `SMTP_USER` or `SMTP_PASSWORD`** before `smtplib`
    sees it. `SMTP.auth` encodes both as ASCII, and its `UnicodeEncodeError` prints the offending
    character plus an offset into the value. The refusal names the setting and its length, exactly
    as `SMTP_PORT`'s does, and no connection is opened.
- **`ntfy_ready()` refuses a cleartext `http://` topic URL by default** (#3). A topic URL is a
  **write capability**, not an address: over cleartext it, the alert `Title` and the body are all
  readable by anything on the path, and the capability stays compromised afterwards.
- **The ntfy channel no longer follows redirects** (#4). `urlopen` re-issued a redirected request
  with the headers intact, so a `302` from the topic host delivered the alert `Title` — the most
  identifying part of the message — to a host the operator never configured, over any scheme the
  redirect named. A `3xx` is now a visible channel failure.

### Added

- `AlertSettings(allow_cleartext_ntfy=...)` — the supported opt-in for an `http://` topic on a
  trusted network. Defaults to `False`. Mirrored on `AlertConfig`, which is where `ntfy_ready()`
  reads it.
- Verification for `_restrict()`, the 0600/0700 narrowing (#6, item 1) — a security control that
  had a six-line justification and no test at all: gutting it to `pass` survived a 127-mutation
  sweep, and so did removing the deliberately redundant post-write call. The two tests that need
  real mode bits are POSIX-only and run on the CI matrix rather than on a Windows worktree, where
  `chmod` cannot express them; the two that assert *behaviour* (that `chmod` is not attempted on
  a missing path, and that a refused `chmod` stays silent) run everywhere. Measured on
  `ubuntu-latest`: 5 of 7 mutations caught, and the 2 survivors are the deliberate redundancies
  (`os.makedirs`' and `os.open`'s `mode=` arguments, each reaching the same end state as the
  `_restrict` call beside it).
- A test for the suite's own no-network property (`tests/test_no_network.py`). It was a guard with
  no test, and four mutations of it — including lifting the block entirely — left the whole suite
  green. The block now blocks every `smtplib.SMTP` subclass by walking the module rather than by a
  hand-written list (`SMTP_SSL` and then `LMTP` were each found un-blocked one round apart, which
  is the same fix-the-instance-not-the-class miss twice), and its loopback opt-out parses the host
  as an address instead of matching text — a `startswith("127.")` form admitted
  `127.evil.example`, a DNS name resolved off the machine.

### Fixed

- `_post_ntfy` **closes the response** instead of leaving the socket to the garbage collector — a
  slow descriptor leak on the one path that runs when things are already going wrong.
- The suite's network block covered `urlopen` only, so the ntfy channel's own opener walked
  straight past it into a real DNS lookup. It now blocks `OpenerDirector.open`, which covers every
  opener.
- The ntfy opener is built **on first send, not at import**, matching `urlopen`'s own timing.
  `build_opener()` constructs a `ProxyHandler` that snapshots `getproxies()` when it is built, so
  building it at import would have silently dropped proxying for any consumer that resolves its
  proxy environment in `main()`. The only thing this opener changes is redirect handling.

### Notes for adopters

- ⚠️ **A cleartext `http://` ntfy URL now disables the ntfy channel.** This is the one behaviour
  change that can turn a working channel off. It is loud — an `ERROR` line at readiness naming the
  scheme and the opt-in, and the boot report counts the channel as unconfigured — never silent.
  Two supported fixes, in order of preference: move the topic to `https://`, or pass
  `AlertSettings(allow_cleartext_ntfy=True)` to accept the exposure deliberately. Refusing outright
  was considered and rejected: a self-hosted ntfy on a trusted network is a real deployment, and a
  library whose only answer is "no" leaves a fork as the alternative.
- ⚠️ `allow_cleartext_ntfy` **must be a real `bool`.** A non-bool raises `ValueError` at
  construction rather than being coerced, because the string `"false"` is TRUE to Python — a
  deployment that spelled its opt-out correctly in a container Variable would otherwise have opted
  **in** by reading its own opt-out. Port it as
  `allow_cleartext_ntfy=os.environ.get("ALLOW_CLEARTEXT_NTFY", "") == "1"`, not as
  `bool(os.environ.get(...))`.
- ⚠️ **An ntfy URL carrying userinfo now disables the channel** — including a percent-encoded one
  (`user:pw%40host`) — instead of failing on every send. If a deployment appears to lose ntfy at
  this version, this is the likeliest cause, and that channel was never delivering. ntfy
  authentication belongs in a header (a token via a proxy), not in the URL.
- ⚠️ **A non-ASCII ntfy topic now disables the channel** instead of failing on every send. It
  never delivered either. Percent-encode the topic (`/geheim-t%C3%B6pic`); a non-ASCII hostname is
  unaffected.
- ⚠️ **A non-ASCII `SMTP_USER` or `SMTP_PASSWORD` now fails the email channel with a named
  refusal** rather than an opaque `UnicodeEncodeError` per send. Neither ever authenticated: SMTP
  AUTH cannot carry them. The value is not echoed; its length is.
- **What the redaction does NOT do**, stated because a guard that implies coverage it lacks is
  worse than none: it replaces a secret quoted **verbatim**. A message rendering one in transformed
  shape — an escaped `repr` of one character, a hash, a length — passes through. That is why the
  two demonstrated cases are closed at their source rather than left to it, and why the host half
  of an ntfy URL is deliberately **not** redacted: it carries the diagnostic value ("connection
  refused", a DNS failure) and is not itself the capability.
- No exported name changed, so no consumer needs a code change to take this release.

## [1.0.0] - 2026-08-30

First release. The alerting core, extracted from the fleet's reference implementation so that
every service runs the same code instead of a copy that drifts.

### Added

- **`kw_common.alerting`** — the `notify(severity, title, message, escalating=False, clears=None)`
  contract, fanning out to an email channel and an ntfy channel, with the severity driving the log
  level, the ntfy priority and tag, and the subject prefix.
  - `AlertSettings` / `configure()` / `Alerter` — **all configuration is injected**. The module
    reads no environment variable and ships no default path.
  - `AlertConfig` with `email_ready()`, `ntfy_ready()`, `smtp_port()` and `setting_faults()`;
    `smtp_port_fault()` as a pure classifier that logs nothing and sends nothing, so a boot-time
    settings report can reach the same verdict as the send path without provoking one.
  - Edge-trigger and escalating de-duplication over a state file, with `clears=` resolving a
    condition per title. De-duplication **fails open**: every error in the bookkeeping sends the
    alert.
  - A size-capped, rotating JSONL error log for WARN and ERROR, plus `read_jsonl_tail()` and
    `parse_since()` to read it back — the read path that a consuming service exposes as
    `GET /v1/admin/errors?since=&limit=`.
- The generic-code contract enforced as tests: isolated import in a subprocess with only the
  standard library on the path, an AST check that the module never reads the environment, and an
  AST check that no module-level constant holds an absolute path.
- CI on Python **3.10 and 3.12** (lint, type-check, tests), the leak guard (self-test, tree scan
  and commit-range scan), and a packaging job that installs the built wheel into a clean
  environment and imports it.
- A release workflow that refuses to publish when the tag and `kw_common.__version__` disagree.

### Notes for adopters

- **Both halves of the config-file parser fix are present**, and both are required.
  `_parse_env_file` opens the file with `newline=""` *and* splits on `"\n"`. Shipping only the
  split leaves a bare `\r` truncating a value silently, because universal-newline translation
  rewrites it before the split can observe it. Two tests pin the pair, in both directions.
- **Known dual, accepted deliberately:** a CR-only file (classic Mac line endings) parses as one
  line. Unavoidable — translating a lone CR *is* the bug being fixed.
- **A control character between two recipients now fails the email channel loudly** (contained by
  the per-channel guard, with the value not echoed) instead of silently delivering to a subset.
  That is the intended direction.
- Configuration that used to be read from the environment is now passed in. There is no default
  path for the email settings file, the state file or the error log: an unset sink is OFF and says
  so at boot, rather than silently succeeding into a container's disposable layer.
- ⚠️ **A BLANK path is REFUSED at construction; `None` is how you turn a sink off.** This is the
  one migration detail likely to bite, because the obvious port of an environment-driven service
  is `state_file=os.environ.get("ALERT_STATE_FILE", "")` — and a container platform that passes
  every unset optional Variable as an empty string makes that the common case rather than the
  exotic one. A blank behaving like `None` would disable de-duplication silently, so
  `AlertSettings(...)` raises `ValueError` naming the field and what its blank would have cost.
  Port it as `os.environ.get("ALERT_STATE_FILE") or None`.
- `os.PathLike` is accepted for all three path settings, so `pathlib.Path` works everywhere a
  `str` does.
- `read_jsonl_tail(backups=...)` no longer takes `None` as a sentinel meaning "use the module
  default" — `Alerter.read_errors()` passes the settings' value, so the reader and the roller
  cannot disagree. Calling the function directly with `backups=None` raises; see issue #5.

[1.0.1]: https://github.com/texasdaddy/kw-common/releases/tag/v1.0.1
[1.0.0]: https://github.com/texasdaddy/kw-common/releases/tag/v1.0.0
