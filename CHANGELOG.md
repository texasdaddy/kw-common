# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A breaking change to a name in a module's `__all__` is a MAJOR bump, and its entry names every
consumer repository that needs a code change.

## [1.3.0] - 2026-09-03

A check that shipped inside the thing it was checking, and a marker that recorded the wrong fact.

### Security

- **The private-name check no longer publishes the list it enforces (#16).** It asked ONE file —
  the guard's own source — while everything else the wheel and the sdist carry went unchecked, and
  it carried the forbidden names as a plain tuple in a test file that `MANIFEST.in` puts inside
  every published tarball. Both halves were wrong, and the second made the first unfixable in
  place: widening the test's reach while the list stayed would have made the disclosure worse, and
  hashing or splitting the list in-repo is the same disclosure with extra steps.

  Measured against the artifacts 1.2.0 actually published: **17 occurrences in 4 files of the
  sdist**, 0 in the wheel — the one file any check looked at was the one file that was clean, and
  every occurrence was in a test the wheel does not carry and `recursive-include tests *.py` puts
  in the tarball. The list moved OUT of this repository, into a guard committed to no repository
  at all. The same measurement against 1.3.0's wheel and sdist: 0 and 0.

- **`.githooks/pre-push` (#10).** The check moved to the moment publication happens. CI catches a
  leak *after* the push, and a push to a public remote is permanent — the value stays readable at
  the commit that added it, in every clone, and the repair is a history rewrite. The hook scans
  the working tree (what the artifacts are built from) and the commits each ref would publish, and
  **fails CLOSED when it is not configured**, because a guard that is silently not running looks
  exactly like a pass. Install: `git config core.hooksPath .githooks` plus one config key naming
  the guard, so no operator path is written into this repository.

  ⚠️ **Declared bound, stated rather than implied:** a hook is a local convention. Nothing here and
  nothing in CI can assert that it ran on somebody's machine, because CI must not have the list
  either. `tests/test_leak_guard_hook.py` drives the real hook against a real `git push` to a real
  bare remote and asserts whether the REMOTE REF MOVED — refused for a leaking commit, a leaking
  worktree and an unconfigured guard; allowed for a clean push and a branch deletion; and passing
  `--not --remotes` for a brand-new branch, which would otherwise scan zero commits at exactly the
  moment a leak is most likely.

### Changed

- **The boot marker records the config's sha256 instead of relying on its own timestamp (#15).**
  `rsync -a`, `cp -p`, `tar -x` and volume restores all PRESERVE mtime, so a config restored at an
  older timestamp left the marker still newer: a file nobody had checked booted clean, announced
  nothing, and the service came up alerting no one. A timestamp cannot answer "did the contents
  change"; a digest can, whatever the clock says.

  Contents decide in both directions — a restore with different bytes re-validates, an identical
  restore does not, so a routine backup restore does not page anybody. The digest is over the RAW
  BYTES, so a silent re-encode (the cp1252 file this module refuses at boot) re-validates too.

  Two things go away with the old mechanism. The coarse-filesystem defect: on a one-second
  granularity filesystem the config and the marker landed on the same second, the marker could
  never be strictly newer, and every boot re-validated and re-alerted forever — `_outrank`, which
  existed only to break that tie, is deleted rather than kept. And the one-second window it left,
  where a config edited in the second after a boot was skipped.

- **`py.typed`** — PEP 561's marker, so a consumer's mypy actually reads the annotations this
  package already carried. Asserted against the BUILT WHEEL rather than the source tree: it is
  data, `packages.find` does not carry it, so it could be committed, read as done, and be absent
  from the artifact with nothing in this repository noticing.

- **Consumer counts are out of the prose.** How many services a library is installed on is the
  operator's estate, not the library's documentation, and this repository is public.

### Fixed

- **`_RefuseRedirects` described the ntfy `Title` as `[ERROR] <service>: <title>`.** It is
  `[SEV] <title>`; the service name has never been in that header. On a shared topic
  `AlertSettings.title_prefix` puts `[<env>][<service>] ` at the front of the TITLE, which is a
  property of the topic rather than of the header — the docstring conflated the two.

### Notes for adopters

- **Upgrading costs exactly one re-validation, silently.** A marker written by 1.2.0 is empty, so
  it matches no digest; the first boot after the upgrade validates, sends one confirmation, and
  rewrites the marker. No operator step, and no way to get a stale "validated" verdict across the
  upgrade — which is the direction that matters.
- **`touch` is no longer the remedy after a restore, and is no longer needed.** To force a
  re-check without editing the file, delete `CONFIG_PATH/.alerting-validated-<env>`.
- **The public surface is unchanged.** Nothing was added to or removed from any module's
  `__all__`; the digest helper is deliberately private.

## [1.2.0] - 2026-09-03

The alerting **convention** becomes a function signature. The ops-alerting standard has specified
one shared configuration file and three container variables for a while; the templates each
declared a different shape of alerting variable and **not one declared the variable the standard
actually names**, because a convention that exists only as prose gets interpreted. This release
makes it executable: a missing variable is now an error at boot rather than a divergence
discovered when an alert does not arrive.

`kw_common.alerting` stays environment-free — that is what makes it portable, and putting file and
environment reading back into it would undo the removal 1.0.1 made deliberately. The deployment
knowledge lives in a **sibling module** instead.

### Added

- **`kw_common.alerting_env`** — two layers, on purpose:
  - `load_alert_settings(config_path, deploy_env, service)` is pure. It reads no environment
    variable and has no default path, so it works outside this deployment and is testable without
    touching `os.environ`.
  - `load_alert_settings_from_env(service)` is the convention: it reads `SHARED_ROOT` and
    `DEPLOY_ENV` and **refuses, loudly, when either is unset or blank.** Blank is refused as well
    as absent, because a container platform passes an unfilled Variable as an empty string.
- **`validate_boot(...)` / `validate_boot_from_env(...)`** — the startup check. It verifies the
  mount, the structure, that the file is readable, that every key this environment requires carries
  a value that is not still the template's `CHANGE-ME`, and that every configured channel is one
  the library can actually SEND on. On success it sends **one** confirmation alert and writes an
  empty marker into `CONFIG_PATH`; later boots skip while that marker is newer than the config
  file. The marker is **per environment**, so a dev → prod promotion re-validates even though the
  shared file did not change.
  ⛔ **A failure logs and raises and does not attempt to alert** — it cannot report a broken
  alerting channel through that channel. With no `Alerter` installed it validates but does NOT
  write the marker, so a confirmation that could not be sent is not silently marked as sent.
- **The required-key manifest, owned here** — `required_keys(deploy_env)`. Otherwise every app
  answers "what does prod require?" independently and they disagree the first time a key is added.
- **`alerting.env.template`** and **`docs/alerting-setup.md`** — the setup contract, owned once.
  Per-OS commands create the structure and fetch the template with `mkdir` + `curl -fLo` or
  `New-Item` + `Invoke-WebRequest -OutFile`; **nothing is piped into a shell**, and the page says
  to review any file you download and that you proceed at your own risk. A consumer README carries
  a prerequisite block and a **link** to that page — never a copy.
- **`AlertSettings.title_prefix`** — applied to the email subject and the ntfy title, and to
  nothing else. A control character in it is refused: it becomes an HTTP header value and a mail
  `Subject`, both of which reject one, so such a prefix would fail every alert on every channel
  while the boot report still said both were ready.

### Changed

- **`SMTP_USER` is now OPTIONAL and defaults to `EMAIL_FROM` — when, and only when, that is a
  bare ASCII mailbox.** They are the same value in this deployment today and they are different
  things: `SMTP_USER` is the auth identity, `EMAIL_FROM` is the header. A From header may
  legitimately read `Alerts <box@host>` or carry a non-ASCII display name, and neither is a
  credential — deriving one from the other made `email_ready()` report READY for a channel that
  then failed 100% of sends, which is the "dead while looking configured" failure this module's
  docstrings call its worst. Where the two cannot be the same, the key is simply required, and the
  existing diagnostic already says so. A file that sets it explicitly is always honoured, which is
  what a verified alias or a relay whose user is literally `apikey` needs. Applied where the file
  is READ, so it survives the per-notification re-read that lets a password rotation take effect
  without a restart.
- **`_parse_env_file` split into a read and a parse.** One parser, two error policies: `alerting`
  reads fail-soft (a missing or mis-encoded file means the email channel is unconfigured, which is
  right for a notification already in flight) and `alerting_env.read_config` reads fail-loud (at
  boot, "the file is empty" and "the file is UTF-16" must not look alike). A second parser would
  let boot validation accept a file the send path then reads differently.
- `tests/test_readme.py` demanded EXACTLY ONE `python` block in the README, which was a bound on
  the document rather than a property of it — and the cheapest way to satisfy it was to delete a
  block. It now checks every block, per block.

### Notes for adopters

- **The title prefix is a property of the TOPIC, not of the service.** A service on the shared
  environment topic gets `[<env>][<service>]` in front of its alert titles, because on a shared
  topic nothing else says who is talking. A service with its own `NTFY_URL_<SERVICE>` topic gets
  **no** prefix. A service that already has its own topic keeps exactly what it does today.
- **The prefix reaches the two outbound channels and nothing else.** The de-duplication state key,
  the retrievable error record and the process log line all keep the RAW title, so promoting a
  service from dev to prod does not re-page every escalating condition it had already reported.
- **`NTFY_URL_<SERVICE>` has exactly one spelling**: the service name upper-cased, with each RUN
  of characters that are not an ASCII letter or digit replaced by a SINGLE `_` and the ends
  dropped — so `backup-agent` is `NTFY_URL_BACKUP_AGENT`, `feed--poller` is `NTFY_URL_FEED_POLLER`
  rather than `FEED__POLLER`, and `café-poller` is `NTFY_URL_CAF_POLLER`. A
  key spelled any other way is never looked up and the service quietly lands back on the shared
  topic with a prefix it should not have. `alerting_env.ntfy_key(service)` returns the answer, and
  the template's worked examples are checked against it by a test rather than by proofreading.
- **`state_file` and `error_log` are NOT in the shared file and are not loader arguments.** They
  are the app's own paths under its own volume; use `dataclasses.replace(settings, ...)`.
- **`NTFY_URL_<ENV>` is required even for a service that has its own topic.** The file is shared:
  a file missing `NTFY_URL_PROD` is broken for every service without an override, and the next
  service to adopt is exactly that one.
- **A service may not be named after an environment.** `dev` or `prod` derives
  `NTFY_URL_DEV`/`NTFY_URL_PROD` — keys the shared file always carries — so it would read a shared
  topic as its own dedicated one: wrong topic, no prefix, and a confirmation alert saying it is on
  its own. Refused by `ntfy_key`.
- **The marker is a TIMESTAMP comparison, and that is its limit.** A config file restored at an
  older mtime (`rsync -a`, `cp -p`, `tar -x`, a volume restore) leaves the marker still newer, so
  that change is not re-validated. `touch` the file after a restore.
- ⚠️ **`CONFIG_PATH` is not the config file.** `load_alert_settings`'s first parameter is called
  `config_path` and is the alerting.env FILE; the `CONFIG_PATH` variable is the app's own
  read-write DIRECTORY, where the marker goes. The validation function spells that one
  `marker_dir` rather than repeating the ambiguity.

### Out of scope, deliberately

Adopting this anywhere — each service's adoption is its own package — any container template edit,
and user-facing notifications, which run off per-user preferences and never share a channel, a
topic or a config file with operator alerting.

## [1.1.0] - 2026-09-02

The leak guard becomes a module. It was a **file each repository kept its own copy of**, with one
of its test suites copied alongside it — so a fix made in one reached none of the others, and four
separate engine improvements had to be re-ported by hand after being made once upstream.
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
  notes below for the format and for the five fail-closed properties it was designed around.
- **`--config <path>` / `--config=<path>`** on the command line, and `GuardConfig`,
  `DEFAULT_CONFIG`, `ConfigError`, `find_config`, `load_config`, `parse_config`, `apply_config`,
  `CONFIG_FILENAME` and `cli` in the module's `__all__`.
- **A configuration that stops the guard's own DENY CASES being caught is refused**, on any of
  the three surfaces, with exit 2 naming the case it defeats. ⚠️ Read that literally: the check
  is CORPUS-SHAPED. It refuses a literal that silences one of a finite set of samples, so the
  pool root the corpus spells is refused and another pool name is not — and an accepted literal
  is not narrow, since allowing a pool root silences that pool everywhere. It is a backstop
  against the careless case, not a proof that the format cannot be misused. What keeps an
  allow-list honest is that every entry is an exact literal, spelled out, justified, and read
  from the index.

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
  ]
}
```

`allow_literals` is the whole surface. It suppresses a hit that falls INSIDE an exact literal; it
cannot remove a pattern, widen one, or skip a file. Five properties, each chosen in the fail-closed
direction and each worth knowing before you write a config:

1. **No config file means nothing is allowed.** A missing or misnamed file can only make a scan
   *stricter*. The opposite default — shipping this fleet's own allowances — would mean an
   installed library silently permitting values in a repository that never asked, and a consumer
   would have to edit installed code to tighten it, which is the fork this package exists to
   prevent.
2. **The config is read from the INDEX, not from your working copy.** What governs a scan is
   exactly what a commit would record, so `git add` an edit before expecting it to apply — the
   guard prints a note when the two differ. A file on disk that the index does not have is an
   error rather than silence. `--config <path>` is exempt: it is written out in the invocation,
   which is the visibility that matters.
3. **A config this scanner cannot understand STOPS the scan** (exit 2). It never degrades to "no
   config": an unknown or misspelled key, a missing `why`, a `--config` path that does not exist.
   The difference between "your rules were applied" and "your rules were unreadable, so none were"
   is invisible in a clean verdict. A UTF-8 BOM is accepted — Windows editors write them, and
   reddening a correct config is how a guard gets switched off.
4. **`why` is required on every entry**, so "keep each one justified" is enforced rather than
   requested. An entry whose justification is blank or whitespace is refused.
5. **The only file whose CONTENT is skipped outright is the guard's own source, recognised as
   its own.** (A `SKIP_SUFFIXES` asset whose bytes really are binary is skipped as well; a text
   file merely NAMED `.png` is not.) Either it is
   literally that file — you vendored the guard inside the repository it scans, or installed it in
   editable mode from there — or the file's BYTES are the running guard's own bytes. Nothing else
   in your repository can claim either.

   ⚠️ Stated precisely, because the short version of it is wrong: where the running guard IS the
   repository's own file, that file's content is skipped UNCONDITIONALLY. That is inherent in a
   guard whose source carries its own deny corpus; the layer that covers that one file is a
   separate real-literal check run from outside the repository, which deliberately does not skip
   it.

**There is no path-exemption setting.** A repository could briefly excuse one named pattern on one
path regex, and both attempts to bound that regex were fail-open, one review round apart: the first
never measured it (`{"path_regex": "."}` turned a pattern off tree-wide and was accepted); the
second measured the deny corpus at a list of "ordinary" probe paths, which is a nine-name allowlist
wearing the word "property" — `\.go$` and `^internal/` sailed past it, a negative lookahead over
the nine names turned a pattern off everywhere in one line, and it REFUSED the narrowest exemption
there is (a single file) whenever that file was called `README.md` or `Dockerfile`. No acceptance
criterion asked for it, so it was withdrawn rather than repaired a third time. A config naming
`path_exempt` is refused with a message saying so, and `PATH_EXEMPT` survives as an engine constant
that only an edit to the module changes.

**If your repository keeps a leak-guard corpus of its own** (synthetic deny cases, fixtures full
of `.lan` hosts), there is no setting for it: assemble those values at RUNTIME from fragments,
the way this repository's own test suites do, so the file carries no matchable literal at rest.
This repository's `.leakguard.json` is the worked example of what a config now looks like, and
the suite reads the real file so a broken config here fails a test here.

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
removes them rather than carrying them forward into one copy per consumer.

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

[1.3.0]: https://github.com/texasdaddy/kw-common/releases/tag/v1.3.0
[1.2.0]: https://github.com/texasdaddy/kw-common/releases/tag/v1.2.0
[1.1.0]: https://github.com/texasdaddy/kw-common/releases/tag/v1.1.0
[1.0.1]: https://github.com/texasdaddy/kw-common/releases/tag/v1.0.1
[1.0.0]: https://github.com/texasdaddy/kw-common/releases/tag/v1.0.0
