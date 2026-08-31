# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A breaking change to a name in a module's `__all__` is a MAJOR bump, and its entry names every
consumer repository that needs a code change.

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

[1.0.0]: https://github.com/texasdaddy/kw-common/releases/tag/v1.0.0
