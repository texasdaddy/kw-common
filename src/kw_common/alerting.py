"""Ops alerting — the shared `notify(severity, title, message)` contract.

The fleet's ops-alerting core, extracted from the reference implementation so that every service
runs the SAME code rather than a copy that drifts. One dispatch per service, fanning out to every
configured channel, with the severity carrying the meaning.

**Stdlib only, and importable alone.** Nothing here imports another `kw_common` module, so a
consumer that wants only alerting pays for only alerting.

CONFIGURATION IS INJECTED, NEVER ASSUMED
    This module names no environment variable, no file path, no topic and no service name. The
    consumer resolves those however it likes — env, a settings object, a TOML file — and passes
    the result as an `AlertSettings`:

        from kw_common.alerting import AlertSettings, configure, notify, OK, WARN, ERROR

        configure(AlertSettings(
            service="my-service",
            config_file="/etc/myservice/alerting.env",   # email settings; None = no email
            ntfy_url="https://ntfy.example.com/my-topic",  # "" = no ntfy
            state_file="/data/alert-state.json",         # None = de-duplication OFF
            error_log="/data/logs/my-service-errors.log",  # None = no error-log sink
        ))
        notify(ERROR, "my-service: backup failed", "...", escalating=True)

    `configure()` returns the `Alerter` it installed; a consumer that would rather not have a
    process-wide default can hold that object itself and call `alerter.notify(...)`. The module
    level `notify()` exists because the call site is usually deep inside an `except` handler where
    threading an object through is exactly the friction that makes people skip alerting.

    There is deliberately NO default for any path. A default path is a promise the library cannot
    keep: the reference implementation defaulted its error log into `/data/logs/...`, and an
    adopter without a `/data` volume got `os.makedirs` SUCCEEDING inside the container's own
    writable layer — no error, no warning, records accumulating, destroyed on every recreate. An
    unset sink is off, and says so at boot.

WHAT SEVERITY BUYS YOU
    A success that pages you like a failure trains you to ignore the channel. `severity` is
    load-bearing: it selects the log level, the ntfy priority + tag, and the subject prefix.
    `OK` is a positive confirmation at low priority — it is NOT a warning.

EDGE-TRIGGER vs ESCALATE (`escalating=`)
    A condition that keeps firing must not keep paging: an alert that arrives every cycle for a
    week is one you stop reading, which is a slower way of having no alerting at all. So every
    notification carries an `escalating` flag.

        escalating=False (default)  alert ONCE when the condition starts, then stay quiet until
                                    it clears. Steady-state conditions.
        escalating=True             keep re-alerting on a widening backoff until it clears —
                                    for conditions where SILENCE is the danger (a lapsed
                                    consent, a failed backup, an expiring certificate).

    Two things are deliberately NOT de-duplicated:

    - **An `OK` is never suppressed.** It is a confirmation, not a condition; for a service that
      reports in on a cadence, that positive notice IS the liveness signal, and silence past the
      cadence is how you find out it died. Nothing here rate-limits `OK`, so do not emit one at
      high frequency.
    - **The log line always goes out.** Logging is not paging. The container log stays the
      complete record even when the channels are quiet.

CLEARING A CONDITION (`clears=`)
    A condition is resolved by an `OK` and by nothing else. There is deliberately no silent
    "forget this condition" call: going quiet is the outcome this module is most afraid of, so
    the only way to stop paging is to say out loud that the thing recovered.

    An `OK` clears **its own title, plus every title it names in `clears=`** — and NOTHING else.
    That matters because most services have several independent conditions:

        notify(ERROR, "svc: backup failed",  "...", escalating=True)
        notify(ERROR, "svc: census stalled", "...")
        # the backup succeeds; the census is still stalled
        notify(OK, "svc: backup ok", "...", clears="svc: backup failed")

    The census stays marked firing, so it is not re-armed into paging again. An earlier version
    of this code cleared EVERY condition on any `OK`, which meant a service emitting a routine
    hourly confirmation re-armed every edge-triggered condition it had.

    `clears=` takes a title or an iterable of titles, and is honoured on `OK` only. Passing it
    with a WARN/ERROR is refused: an alert that resolved its own condition would reset the
    escalation backoff on every retry, so a permanent failure would page at the base gap forever
    and never escalate — while looking healthy, because the alerts keep arriving.

    That refusal also blocks a legitimate shape, the severity DOWNGRADE — reporting that a total
    failure is now merely degraded. Do it as two notifications, in this order:

        notify(OK,   "svc: db reachable again", "...", clears="svc: db unreachable")
        notify(WARN, "svc: db degraded",        "...")

    The rule is deliberately the blunt one. "An alert cannot resolve a condition" is something an
    adopter can hold in their head; "an alert can resolve any condition except its own" is not.

ADOPTER NOTES
    - **Set `state_file` to a persistent path of your own** for any service with an escalating
      alert. On a non-persistent path a restart forgets every firing condition, and two services
      sharing a filesystem share one file. `warn_if_unconfigured()` says so at boot.
      Set it to **`None`** only if the service already de-duplicates its own conditions upstream:
      every `notify()` call is then delivered, nothing is persisted, and escalation backoff does
      not run. Do NOT pass `None` on a service that relies on this module to de-duplicate — it
      will page on every cycle.
      One consequence to know while DEVELOPING against it: the `clears=` misuse diagnostics live
      in the de-duplicator, which the opt-out skips, so a bad `clears=` argument is silently
      ignored instead of reported. Develop with a real state file; `None` is a production setting.
    - **Emit an `OK` when a condition recovers**, naming it in `clears=`. If you use the default
      `escalating=False` for something and never emit that `OK`, the condition stays marked
      firing and its next genuine occurrence is suppressed. Even for an `escalating=True`
      condition, which re-alerts regardless of stale state, the recovery still matters: without
      it the backoff resumes at the daily cap instead of the base gap, so a fault that recurs
      weeks later is reported a day late.
    - **Key a condition by the CONDITION, not the occurrence.** A title carrying an instance id
      ("backup failed for job-1234") makes every occurrence a new condition, so nothing is ever
      de-duplicated and the state file grows until pruning starts dropping the oldest.

INVARIANTS
    - `notify()` NEVER raises. Alerting that can crash its caller is worse than no alerting.
    - A channel that fails logs and continues; it never blocks the other channel. Redundancy is
      the whole point — a dead ntfy must not silence the durable email record.
    - A channel with no config is skipped, not failed. If NO channel is configured,
      `warn_if_unconfigured()` says so loudly at boot: alerts must never go silently nowhere.
    - **De-duplication fails OPEN.** Every error in the escalation bookkeeping — unreadable
      state, corrupt JSON, a read-only directory, a clock that went backwards — SENDS the
      alert. Over-alerting is a nuisance; a de-duplicator that silently swallows a lapsed
      consent is the exact failure this exists to prevent.

THE RETRIEVABLE ERROR LOG (`error_log=`)
    Every WARN and ERROR is also appended as one JSON line — `ts, severity, title, message,
    service` — to a small, size-capped, rotating file. It exists because getting a container's
    log OUT is the part that keeps failing: a remote agent's log pull dies on anything long, and
    what it returns is mostly startup noise. This file is small enough to fetch whole, and
    `read_jsonl_tail()` is the other half — an HTTP-API service answers
    `GET /v1/admin/errors?since=&limit=` from it.

    `OK` is excluded, and that is the point: a file that also collected the periodic success
    confirmations would be the container log again.

    A SUPPRESSED alert is still written. De-duplication protects the operator's phone; hiding an
    occurrence from the file used to troubleshoot it would be the opposite of what this is for —
    the same reasoning that keeps the log line outside the guards.

    ⚠️ **The file persists your MESSAGE BODIES to disk.** That is a different exposure from a
    channel: it is long-lived, it sits on the host filesystem, and its whole purpose is to be
    fetched off the box and read by tooling. Whatever a caller puts in a message now has that
    lifetime. Do not put a credential, a token, or an OAuth callback URL with its query string
    into a title or a message — sanitise at the raise site, where the value is understood. The
    file is created 0o600 in a 0o700 directory AND an existing one is narrowed to the same on
    every write, because a mode passed at creation does nothing for a path that already exists.
    Treat it as operator-confidential, not as safe to hand around.
"""

from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

__all__ = [
    "OK",
    "WARN",
    "ERROR",
    "SeveritySpec",
    "SEVERITIES",
    "AlertSettings",
    "AlertConfig",
    "Alerter",
    "configure",
    "current_alerter",
    "notify",
    "warn_if_unconfigured",
    "smtp_port_fault",
    "parse_since",
    "read_jsonl_tail",
    "EMAIL_KEYS",
    "EMAIL_REQUIRED",
    "DEFAULT_SMTP_PORT",
    "MIN_SMTP_PORT",
    "MAX_SMTP_PORT",
    "SMTP_TIMEOUT_S",
    "NTFY_TIMEOUT_S",
    "ESCALATE_BASE_HOURS",
    "ESCALATE_MAX_HOURS",
    "MAX_TRACKED_CONDITIONS",
    "ERROR_LOG_MAX_BYTES",
    "ERROR_LOG_BACKUPS",
    "MAX_RECORD_FIELD",
]

log = logging.getLogger("kw_common.alerting")

# --- severity ------------------------------------------------------------------------------
OK = "OK"
WARN = "WARN"
ERROR = "ERROR"


@dataclass(frozen=True)
class SeveritySpec:
    """Everything a severity decides. Adding a channel = adding a field here, not an if-tree."""

    log_level: int
    prefix: str         # subject / title prefix, e.g. "[OK]"
    ntfy_priority: str  # ntfy: min|low|default|high|urgent
    ntfy_tags: str      # ntfy renders these tag NAMES as the emoji (see _post_ntfy)
    emoji: str          # the emoji those tags render to — for humans reading this table
    to_error_log: bool  # does this severity belong in the retrievable error log?


# The standard's severity table, verbatim. OK is low priority ON PURPOSE: a periodic positive
# confirmation should land in the drawer and the inbox, not buzz the phone at 03:00.
SEVERITIES: dict[str, SeveritySpec] = {
    OK: SeveritySpec(logging.INFO, "[OK]", "low", "white_check_mark", "✅", to_error_log=False),
    WARN: SeveritySpec(logging.WARNING, "[WARN]", "high", "warning", "⚠️", to_error_log=True),
    # `red_circle` and not `rotating_light`: ntfy renders the latter as 🚨, and the standard's
    # severity table says 🔴. The tag NAME is the wire format — the emoji field beside it is the
    # human-readable check that the two have not drifted apart.
    ERROR: SeveritySpec(logging.ERROR, "[ERROR]", "urgent", "red_circle", "🔴", to_error_log=True),
}

# --- email settings file ---------------------------------------------------------------------
# Keys read from the consumer's `config_file`. SMTP_PASSWORD is a secret: it is read, used, and
# never logged. These names are the FILE's schema, not an environment contract — the file is a
# deployment artifact the operator writes, and every consumer of this library reads the same
# shape so one shared file can serve a whole fleet.
EMAIL_KEYS = ("EMAIL_TO", "EMAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD")
EMAIL_REQUIRED = ("EMAIL_TO", "EMAIL_FROM", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")

DEFAULT_SMTP_PORT = 587  # STARTTLS submission
# A TCP port number. 0 is reserved and is not a port a client may connect to; 65535 is the top of
# the 16-bit field. Everything outside is a typo, and it used to reach the socket as one.
MIN_SMTP_PORT = 1
MAX_SMTP_PORT = 65535

SMTP_TIMEOUT_S = 20
NTFY_TIMEOUT_S = 15

# Anything that must never appear in a URL we are about to hand to http.client.
_UNSAFE_IN_URL = re.compile(r"[\s\x00-\x1f\x7f]")

# --- escalation state ------------------------------------------------------------------------
# The re-alert cadence for an escalating condition: the gap after the Nth send is
# BASE * 2**(N-1), capped at MAX. So 1h, 2h, 4h, 8h, 16h, then daily forever.
ESCALATE_BASE_HOURS = 1.0
ESCALATE_MAX_HOURS = 24.0
# 2**n on an unbounded n is a real cost for a nonsense state file; the cap is reached long before.
_MAX_DOUBLINGS = 20

# How many conditions may be remembered at once, by default. See `Alerter._prune`.
MAX_TRACKED_CONDITIONS = 200

# --- the retrievable error log ---------------------------------------------------------------
# Rolled to a single `.1` by default. The bound is the cap plus one final record on each side:
# the roll is checked BEFORE the append, and `json.dumps` escapes an astral character to 12
# bytes, so the worst realistic record is ~144 KB and the sink tops out near 2.3 MB rather than
# exactly 2.
ERROR_LOG_MAX_BYTES = 1_000_000

# How many rolled generations to keep beside the live file. One, by default: the point of this
# sink is a file small enough to fetch WHOLE through a flaky agent, and two bounded files are
# easier to reason about than five. A consumer that wants a longer tail raises
# `AlertSettings.error_log_backups`, which reaches the roller and the reader from the SAME place
# — a reader that looks for fewer generations than the writer keeps silently loses the oldest
# records, which is a data-loss bug that no test of either side alone would catch.
#
# LOWERING it is the asymmetric direction: generations above the new number are already on disk,
# and the roller will never shift them again while the reader no longer opens them, so they are
# stranded — taking their disk with them and appearing nowhere in a result. Delete them by hand
# after lowering it.
ERROR_LOG_BACKUPS = 1

# Per-field ceiling. The roll check runs BEFORE the append, so without this one enormous message
# writes one enormous record and the size cap does not hold at all.
MAX_RECORD_FIELD = 4000

# Distinguishes "this condition was not firing" from a stored entry of None.
_MISSING = object()


def _now() -> float:
    """Wall clock, behind a name of its own so a test can drive the backoff without sleeping
    through it — and without patching `time` for the whole process."""
    return time.time()


# --- injected configuration -------------------------------------------------------------------
@dataclass(frozen=True)
class AlertSettings:
    """Everything this module would otherwise have had to assume. The consumer supplies it all.

    Nothing here has a path default, on purpose — see the module docstring. An unset sink is OFF
    and reports itself at boot, which is the honest failure; a default path is one that silently
    "works" in the exact deployment it is wrong for.

    `service`      the name that appears in every error-log record. Required, non-blank.
    `config_file`  path to a `KEY=VALUE` file holding the email settings named in `EMAIL_KEYS`.
                   `None` (the default) means the email channel is unconfigured.
                   Read fresh on every notification rather than cached, so rotating the SMTP
                   password or moving the inbox takes effect WITHOUT restarting the service.
    `ntfy_url`     the FULL ntfy topic URL (`https://ntfy.example.com/<topic>`). A bare topic is
                   REJECTED, loudly: `urllib` raises `unknown url type: '<topic>'` on every send,
                   so the channel is dead while looking configured. `""` means unconfigured.
    `state_file`   where firing-condition state is persisted. `None` disables de-duplication
                   entirely — nothing is read, nothing is written, every call is delivered.
    `error_log`    path for the retrievable JSONL error log. `None` disables that sink.

    The remaining fields are the sizing knobs. They are constructor arguments rather than module
    constants a consumer is expected to edit after install, because an edited install is a fork.
    """

    service: str
    config_file: str | None = None
    ntfy_url: str = ""
    state_file: str | None = None
    error_log: str | None = None
    error_log_max_bytes: int = ERROR_LOG_MAX_BYTES
    error_log_backups: int = ERROR_LOG_BACKUPS
    max_record_field: int = MAX_RECORD_FIELD
    max_tracked_conditions: int = MAX_TRACKED_CONDITIONS

    def __post_init__(self) -> None:
        # Raising here is deliberate and does NOT weaken the "notify() never raises" invariant:
        # settings are built once, at boot, where a refusal is a startup crash the operator sees
        # rather than records that say "unknown" forever. A blank service name is not a value
        # this library can substitute for — only the consumer knows what it is called.
        if not isinstance(self.service, str) or not self.service.strip():
            raise ValueError("AlertSettings.service must be a non-blank name for this service")
        # Normalise once so nothing downstream has to remember to strip it.
        object.__setattr__(self, "service", self.service.strip())

        # ⭐⭐ A BLANK PATH IS REFUSED, NOT TREATED AS "OFF". This is the single likeliest way to
        # misconfigure this library, and it used to fail SILENTLY in the worst direction.
        #
        # The natural port of an environment-driven consumer is
        # `state_file=os.environ.get("ALERT_STATE_FILE", "")` — and a container platform that
        # passes every unset optional Variable as an EMPTY STRING makes that the common case, not
        # the exotic one. Before this check, `""` disabled de-duplication exactly as `None` does,
        # so every escalating condition re-paged at the caller's full cycle rate with no backoff,
        # and the one boot line the operator got said "set it to a path, or to None to disable" —
        # pointing away from the cause, because blank was neither.
        #
        # `None` means OFF and is spelled `None`; anything else must be a real path. The two
        # states are now distinguishable at the only moment a human can act on the difference.
        for name in ("config_file", "state_file", "error_log"):
            value = getattr(self, name)
            if value is None:
                continue
            # `os.PathLike` accepted and coerced: `Path(...)` is the obvious thing to pass a
            # public library, and it used to work for `error_log`/`config_file` (which reach
            # `open()`) while breaking `state_file` (which reaches `.strip()`) — an asymmetry
            # that showed up as de-duplication silently off plus a traceback per notification.
            if isinstance(value, os.PathLike):
                value = os.fspath(value)
                object.__setattr__(self, name, value)
            if not isinstance(value, str):
                raise ValueError(
                    f"AlertSettings.{name} must be a path, or None to disable it — "
                    f"got {type(value).__name__}")
            if not value.strip():
                raise ValueError(
                    f"AlertSettings.{name} is blank. Pass None to disable it deliberately; a "
                    f"blank string is almost always an unset environment variable reaching this "
                    f"constructor by accident, and silently disabling it is the wrong default.")

        for name in ("error_log_max_bytes", "error_log_backups", "max_record_field",
                     "max_tracked_conditions"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"AlertSettings.{name} must be a positive integer, got {value!r}")


# --- the shared config file --------------------------------------------------------------------
def _parse_env_file(path: str) -> dict[str, str]:
    """Parse a KEY=VALUE file. Blank lines, `#` comments, an `export ` prefix and surrounding
    quotes are all tolerated, because this file is hand-edited by an operator. A missing file
    is not an error — it means the channel is unconfigured, which the caller reports."""
    values: dict[str, str] = {}
    try:
        # ⭐ `newline=""` DISABLES universal-newline translation, and it is HALF of the fix —
        # without it the other half does nothing for `\r`. Python's text reader rewrites `\r\n`
        # AND a lone `\r` to `\n` BEFORE the caller sees a single character, so `split("\n")`
        # below can never observe a bare CR: it has already become a line break. Measured — with
        # the split fixed but the reader left alone, `EMAIL_TO=a@example.com\rb@example.invalid`
        # still parsed to `a@example.com`, silently dropping the second recipient, which is the
        # entire defect. `splitlines()` honours nine boundaries; removing it addresses eight of
        # them and leaves the ninth live one layer down.
        #
        # ⛔ SHIPPING ONLY ONE HALF RE-INTRODUCES THE BUG. The reference implementation shipped
        # exactly that in its first commit and a verification agent caught it. Both halves, or
        # neither.
        #
        # Reading verbatim means a CRLF file now yields lines ending in `\r`. That is handled,
        # and by an existing line rather than a new one: `line.strip()` immediately below removes
        # it, as does the `value.strip()` further down.
        with open(path, encoding="utf-8", newline="") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return values
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is NOT theoretical and NOT an OSError: this file is hand-edited by
        # an operator, and one accented character saved as cp1252 (or a UTF-16 save) used to
        # raise straight out through notify() and kill the caller's loop.
        log.warning("alert config %s unreadable (%s) — email alerts disabled until it is fixed",
                    path, type(exc).__name__)
        return values
    # ⭐ `split("\n")`, NEVER `splitlines()` — the second half of the same fix. `str.splitlines()`
    # breaks on far more than a newline — `\v`, `\f`, `\x1c`-`\x1e`, `\x85`, U+2028 and U+2029 are
    # all line boundaries to it — and no config-file format intends any of them as one. The
    # consequence is not a REJECTED value but a SILENTLY TRUNCATED one: measured on 3.12,
    # `EMAIL_TO=a@example.com` + U+2028 + `b@example.invalid` parsed to `a@example.com`, dropping
    # a recipient with no error anywhere. Splitting on `\n` alone loses nothing a real file needs,
    # because the reader above is opened `newline=""` and the `.strip()` below removes the `\r` a
    # CRLF file leaves on each line.
    #
    # ⚠️ DO NOT "HARMONISE" THIS WITH A LOG-SANITISER. A helper whose job is to stop a value
    # forging extra LOG LINES must honour every `splitlines()` boundary — the opposite answer,
    # over the same character set, for a different question. Same characters, opposite correct
    # behaviour.
    #
    # KNOWN DUAL, accepted deliberately: a CR-only file (classic Mac endings) parses as ONE line.
    # Unavoidable — translating a lone CR *is* the bug.
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


# Separators an operator might reasonably type between recipients. `,` is what RFC 5322 says and
# the only one a mail header may actually carry; `;` is what a great many mail clients and admins
# use, and `EMAIL_TO` is hand-typed into a shared file by exactly those people.
_RECIPIENT_SEPARATORS = re.compile(r"[,;]")


def _recipients(raw: str) -> str:
    """`EMAIL_TO` as a `To` header smtplib can actually derive recipients from.

    ⭐ A `;` in `EMAIL_TO` — a single stray keystroke — used to cost alerts silently.
    `smtplib.SMTP.send_message()` derives its recipient list by running
    `email.utils.getaddresses()` over this header, and neither that parser nor the address header
    it reads is willing to guess at a value it cannot parse. What the guessing produces varies by
    Python version, and MEASURED on 3.12 it is not one failure but a family of them:

        "a@x.example;b@y.example"      -> ["a@x.example"]      the second recipient is GONE
        "a@x.example , ; b@y.example"  -> ["a@x.example", ""]  an empty recipient, server refuses
        ";"                            -> [""]                 nothing deliverable at all

    Do not pin a specific one of those: the point is that every branch loses recipients, and NONE
    of them says so. A total failure raises `SMTPRecipientsRefused`, which surfaces as one WARNING
    at send time — the failure reported by the very alert it is destroying, on a channel nobody
    checks because it worked yesterday. A PARTIAL failure is quieter still: the send succeeds, so
    there is no warning anywhere, and the recipient who was dropped simply never hears from this
    service again.

    So the value is normalised BEFORE it becomes a header: split on either separator, strip the
    whitespace around each segment, drop the empty segments a trailing or doubled separator leaves
    behind, and rejoin with the one separator the parser accepts. A single clean address comes back
    unchanged.

    ⚠️ WHAT THIS DOES NOT HANDLE — one case, MEASURED, and it is not the one you would guess.
    This is a character split, not an RFC 5322 parser, so a separator inside QUOTES is split like
    any other. For a quoted DISPLAY NAME that costs nothing, because rejoining with `", "` puts
    the string back exactly as it was:

        '"Doe, Jane" <j@x.example>'                     -> unchanged, delivers to j@x.example
        '"A, B" <a@x.example>, "C, D" <c@y.example>'    -> unchanged, delivers to both
        '"Doe; Jane" <j@x.example>'                     -> display name rewritten to '"Doe, Jane"';
                                                           delivery unaffected, so it is cosmetic
        'Team: a@x.example, b@y.example;'               -> group syntax, delivers to both either way

    The one case that genuinely loses is a separator inside a quoted LOCAL PART, where the rewrite
    names a DIFFERENT MAILBOX:

        '"a,b"@x.example'      ->  '"a, b"@x.example'      a different mailbox, silently
        '"team,ops"@x.example' ->  '"team, ops"@x.example'

    That is a deliberate trade and not a case to chase. The only fix is to ask the stdlib which
    spans are quoted — and this function exists precisely because that parser's answer for the
    input that matters varies by Python patch level. A quoted local part is also vanishingly rare
    (this setting is documented to hold a bare address list) and, unlike a `;` between recipients,
    nobody types one by accident.

    An all-separator value normalises to the empty string rather than raising. `email_ready()` asks
    this same question, so the complaint arrives BEFORE smtplib is reached rather than as a
    refusal from the server afterwards. It is not less frequent — readiness is evaluated on every
    `notify()` — it is earlier, and it names the setting instead of the symptom.
    """
    parts = (part.strip() for part in _RECIPIENT_SEPARATORS.split(raw))
    return ", ".join(part for part in parts if part)


def _fault_shape(raw: str) -> str:
    """How to describe a REJECTED setting without printing it: its length, and nothing else.

    A rejected value is not safe to echo, and a length cap does not make it safe: every real
    credential is well under any cap worth setting, so the cap never engages for exactly the value
    that matters. The trigger here is concrete — `SMTP_PORT` and `SMTP_PASSWORD` sit adjacent in
    the shared file, and an operator who pasted the app password into the port field had it echoed
    verbatim into the log the README tells them to paste into an issue.

    Everything actionable survives: WHICH setting, WHAT KIND of wrong it is, and which default is
    now in force. The length is kept because it separates the two mistakes that look identical from
    the outside — an empty-ish typo, and a whole value dropped in the wrong field.
    """
    return f"its {len(raw)}-character value"


def smtp_port_fault(raw: str) -> str:
    """Why `SMTP_PORT` is unusable, as one finished sentence — or `""` when it is fine.

    ⭐ PURE, and that is the whole point of it existing. It logs nothing, sends nothing, reads no
    file and touches no global, so it is safe to call from places `AlertConfig.smtp_port()` is
    not: a boot-time settings report, and a config dump deciding whether a value is safe to print.

    Before this existed, the rule lived only inside `smtp_port()`, which is called at SEND time
    and complains by logging — so a service that wanted to know "is this port acceptable" at BOOT
    could only find out by provoking a send-time ERROR. That is what left a rejected port logging
    on every send and PAGING on none of them.

    ⛔ It must stay pure. Raising an alert from inside a config read is a re-entrancy hazard, not
    a style preference: `notify()` loads the config, which resolves the port, so a `notify()` in
    here is `notify()` calling itself. Keep the decision here and the REPORTING at the call site.

    The contract on `raw` is deliberately scoped, not absolute: a `str` is required.
    `AlertConfig.load()` always produces one, and hand-building a config with `SMTP_PORT=None`
    raises `AttributeError` here exactly as it does in `smtp_port()` — this is a public function
    in a library other services share, so the two behave identically rather than one quietly
    tolerating what the other refuses.

    A whitespace-only value is NOT a fault: that is how a template passes an unset optional
    setting, and it falls back silently.
    """
    raw = raw.strip()
    if not raw:
        return ""
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        fault = f"is not a number ({type(exc).__name__})"
    else:
        # A plain comparison, never a conversion: `int()` accepts a 400-digit number happily and
        # converting one to a float raises `OverflowError` inside the guard meant to stop a bad
        # setting breaking anything. `10**400 <= 65535` is answered exactly.
        if MIN_SMTP_PORT <= value <= MAX_SMTP_PORT:
            return ""
        fault = f"is outside the usable range [{MIN_SMTP_PORT}, {MAX_SMTP_PORT}]"
    # The SHAPE of the rejected value, never the value. Assembled here so the log line and the
    # boot report cannot drift into describing the same fault two different ways — they share this
    # sentence verbatim.
    return (f"SMTP_PORT {fault} — {_fault_shape(raw)} is IGNORED; using the "
            f"default {str(DEFAULT_SMTP_PORT)!r}")


@dataclass(frozen=True)
class AlertConfig:
    """A resolved snapshot of both channels' settings."""

    ntfy_url: str = ""
    email: dict[str, str] | None = None
    config_file: str | None = None  # where `email` came from, for diagnostics only

    @classmethod
    def load(cls, settings: AlertSettings) -> AlertConfig:
        """Read the current config: ntfy from the settings, email from the settings' file.

        The FILE is read fresh on every call rather than cached, so rotating the password or
        moving the inbox in it takes effect WITHOUT restarting the service. The cost is one small
        file read per notification.
        """
        path = settings.config_file
        parsed: dict[str, str] = {}
        if path:
            try:
                parsed = _parse_env_file(path)
            except Exception as exc:  # the FILE is the email channel's own config source
                # Anything at all that goes wrong reading the file belongs to the EMAIL channel.
                # Letting it out of here would take ntfy — whose config is a plain string and is
                # perfectly fine — down with it, which is the exact failure this keeps relearning.
                # Loading config is not a channel, so it gets its own boundary. `exc_info` because
                # "could not be read" is what an operator's bad file looks like AND what a bug in
                # the parser looks like, and without the traceback the second kind is invisible.
                log.error("alert config %s could not be read (%s) — email alerts DISABLED; other "
                          "channels are unaffected. If the file is fine, this is a bug in the "
                          "parser; the traceback says which.", path, type(exc).__name__,
                          exc_info=True)
                parsed = {}
        return cls(ntfy_url=settings.ntfy_url or "",
                   email={k: parsed.get(k, "").strip() for k in EMAIL_KEYS},
                   config_file=path)

    # --- readiness (blank gating) ---------------------------------------------------------
    def ntfy_ready(self) -> bool:
        """True only for a FULL http(s) URL. A bare topic is the live failure this guards:
        urllib raises `unknown url type: '<topic>'` on every send, so the channel is dead while
        looking configured. Reject it here, loudly, and let the boot warning see it as unset."""
        if not self.ntfy_url:
            return False
        if _UNSAFE_IN_URL.search(self.ntfy_url):
            # Whitespace or a control character survives urlsplit but makes http.client raise
            # `InvalidURL`, whose message quotes the path — which would print the topic, a
            # capability, into the log the rest of this module is careful to keep it out of.
            log.error("the ntfy URL contains whitespace or a control character — ntfy alerts "
                      "are DISABLED until it is fixed")
            return False
        try:
            parts = urlsplit(self.ntfy_url)
        except ValueError as exc:
            # urlsplit RAISES on some malformed values — an unclosed IPv6 bracket
            # (`https://[::1/t`) is the easy one to typo. A readiness check that throws is worse
            # than one returning False: it escapes this channel and kills the other one's
            # notification too, which is exactly what the fan-out exists to prevent.
            # The CLASS NAME only, deliberately: urlsplit's message quotes the offending host,
            # and the topic URL is a capability. Do not "improve" this by logging `exc`.
            log.error("the ntfy URL is not parseable (%s) — ntfy alerts are DISABLED "
                      "until it is fixed", type(exc).__name__)
            return False
        if parts.scheme in ("http", "https") and parts.netloc:
            return True
        log.error("the ntfy URL must be the FULL topic URL (https://<host>/<topic>), not a "
                  "bare topic — ntfy alerts are DISABLED until it is fixed")
        return False

    def email_ready(self) -> bool:
        email = self.email or {}
        # EMAIL_TO is judged by what the SEND PATH will actually use, not by what was typed. A
        # value that is nothing but separators — `";"` — is non-empty, so a plain presence check
        # calls it configured while `_recipients` normalises it to no recipients at all. Reporting
        # that channel READY is the same nothing-is-delivered-and-nothing-says-so failure.
        if not any(email.get(k) for k in EMAIL_REQUIRED):
            # Nothing at all: the file is absent or empty. That is "not configured", which the
            # boot warning reports — not a misconfiguration to shout about on every send.
            #
            # ⭐ ORDERING IS LOAD-BEARING. Asked of the RAW values, deliberately, and BEFORE the
            # normalisation below. Counting the RESOLVED ones instead — which is what
            # `len(missing) == len(EMAIL_REQUIRED)` did once EMAIL_TO could normalise to empty —
            # quietly reclassified a real misconfiguration as "nothing configured": a file
            # holding only `EMAIL_TO=;` made all five look absent and returned False with NO log
            # line at all. This module's one unforgivable failure is going quiet, so the shortcut
            # has to mean what it says: nothing was configured, not nothing was USABLE.
            return False
        resolved = {**email, "EMAIL_TO": _recipients(email.get("EMAIL_TO", ""))}
        missing = [k for k in EMAIL_REQUIRED if not resolved.get(k)]
        if missing:
            # Names only. The values include the SMTP password. "or unusable" because one of these
            # names can now be present-but-empty-after-normalisation rather than absent, and
            # "missing" alone would send an operator looking for a line that is right there.
            log.error("email alerts DISABLED — %s missing or unusable in %s",
                      ", ".join(missing), self.config_file)
            return False
        return True

    def smtp_port(self) -> int:
        """The configured port, or the documented default with a LOUD complaint.

        Never raises for a config produced by `AlertConfig.load()`, which is how every caller in
        this module and in any consuming service obtains one: the parser is line-based and
        `load()` `.strip()`s every value, so `SMTP_PORT` is always a `str` here. Scoped that way
        on purpose rather than claimed absolutely — hand-building
        `AlertConfig(email={"SMTP_PORT": None})` raises `AttributeError` on `.strip()`, and this
        is a PUBLIC dataclass. The never-raises guarantee callers actually rely on belongs to the
        dispatcher, which wraps each channel; it is not re-implemented per accessor.

        Two things are rejected, and both were reachable:

        * **Not a number.** The value quoted back is the one an operator most often gets wrong
          here by pasting the app password from the adjacent line — see `_fault_shape`.
        * **Outside a TCP port's range.** `0`, `-1`, `65536` and a 400-digit number all parsed
          cleanly and went straight into `smtplib.SMTP(host, port)`, where they surface as an
          `OSError` or a `gaierror` per send with nothing naming the setting that caused it.

        A whitespace-only value is NOT a fault: that is how a template passes an unset optional
        setting, so it falls back SILENTLY.

        `email_ready()` deliberately does NOT consult this. A rejected port falls back to a working
        submission port, so the channel is alive; reporting it unusable would be a false claim
        about a channel that sends.
        """
        raw = (self.email or {}).get("SMTP_PORT", "").strip()
        if not raw:
            return DEFAULT_SMTP_PORT
        # The DECISION is `smtp_port_fault`'s, so that a boot report and a config dump reach the
        # same verdict without provoking a send-time log line to find it out. What stays here is
        # the REPORTING, which is what makes this the send-time accessor.
        fault = smtp_port_fault(raw)
        if not fault:
            return int(raw)
        # `%s` with the complaint already assembled, so nothing in the message is re-interpreted
        # as a format string. The SHAPE of the rejected value, never the value.
        log.error("%s", fault)
        return DEFAULT_SMTP_PORT

    def setting_faults(self) -> list[str]:
        """Every setting in THIS config that was rejected and fell back — for a boot-time report.

        The counterpart to `smtp_port()`'s log line, and the reason `smtp_port_fault` is pure: a
        consuming service collects its own rejected settings at boot and pages once, and this is
        how the settings that live in the shared FILE join that page instead of only ever
        complaining into a log. Reports; never sends. The caller decides what a fault means.

        A list rather than a single string because this is the extension point: `SMTP_PORT` is the
        only validated setting in the shared file today, and a second one appends here rather than
        growing a new accessor and a new call site in every consumer.
        """
        raw = (self.email or {}).get("SMTP_PORT", "").strip()
        return [fault] if raw and (fault := smtp_port_fault(raw)) else []

    def is_ready(self, channel: str) -> bool:
        """Is this ONE channel usable? Never raises — a config value bad enough to break its own
        readiness check disables that channel and leaves every other channel alone."""
        try:
            # Derived from the channel NAME, so `_CHANNELS` is the single registry. A separate
            # lookup table here would be a second one, and adding a channel to only one of them
            # made this method raise the KeyError its own docstring promises it never will.
            # The lookup is INSIDE the guard for the same reason the readiness call is.
            return bool(getattr(self, f"{channel}_ready")())
        except Exception as exc:  # one channel's bad config is not the other channel's problem
            # exc_info because this catches two very different things: an operator typo in the
            # config, and a defect in the readiness check itself. Without the traceback both
            # render as one line blaming the config, and the second kind is invisible forever.
            log.error("%s alerts DISABLED — its readiness check failed with %s. This is either "
                      "bad config or a bug in this check; the traceback says which. Other "
                      "channels are unaffected.", channel, type(exc).__name__, exc_info=True)
            return False

    def ready_channels(self) -> list[str]:
        return [name for name, _ in _CHANNELS if self.is_ready(name)]


# --- channels ------------------------------------------------------------------------------
def _send_email(cfg: AlertConfig, spec: SeveritySpec, title: str, message: str) -> None:
    email = cfg.email or {}
    msg = EmailMessage()
    msg["Subject"] = f"{spec.prefix} {title}"
    msg["From"] = email["EMAIL_FROM"]
    # Normalised, never raw: `send_message()` derives the recipient list from this header, and a
    # `;` in it silently cost recipients — how many depends on the value and the Python build, so
    # do not pin one outcome here. See `_recipients`.
    msg["To"] = _recipients(email["EMAIL_TO"])
    msg.set_content(message)
    with smtplib.SMTP(email["SMTP_HOST"], cfg.smtp_port(), timeout=SMTP_TIMEOUT_S) as smtp:
        # `starttls()` with no context does NOT verify the server: smtplib falls back to
        # `ssl._create_stdlib_context()`, which is check_hostname=False / CERT_NONE. The channel
        # would be encrypted but unauthenticated, so anyone able to intercept the egress could
        # present any certificate and collect the app password on the next login() — which is
        # the whole reason that password lives in a file rather than the environment.
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(email["SMTP_USER"], email["SMTP_PASSWORD"])
        smtp.send_message(msg)


def _post_ntfy(cfg: AlertConfig, spec: SeveritySpec, title: str, message: str) -> None:
    # Header values are encoded latin-1 by http.client, so the Title stays ASCII and the emoji
    # is carried by the Tags header as a NAME (ntfy renders `white_check_mark` as ✅). Putting
    # the emoji itself in a header raises UnicodeEncodeError before anything is sent.
    # The scheme check that makes this safe is `AlertConfig.ntfy_ready()`, which the dispatcher
    # calls before either line below is reached; a test feeds it `ftp://` and other non-http
    # values to prove it refuses them. The suppression sits on the `Request`, which is where the
    # URL is accepted.
    req = urllib.request.Request(  # noqa: S310 — scheme gated by ntfy_ready()
        cfg.ntfy_url,
        data=message.encode("utf-8"),
        headers={"Title": f"{spec.prefix} {title}",
                 "Priority": spec.ntfy_priority,
                 "Tags": spec.ntfy_tags},
    )
    # The scheme is validated in `AlertConfig.ntfy_ready()`, which the dispatcher calls before it
    # ever reaches this function — an unvalidated URL here could be a `file://` read.
    urllib.request.urlopen(req, timeout=NTFY_TIMEOUT_S).read()  # noqa: S310 — scheme is gated


_CHANNELS: tuple[tuple[str, Callable[[AlertConfig, SeveritySpec, str, str], None]], ...] = (
    ("email", _send_email),
    ("ntfy", _post_ntfy),
)


# --- error-log helpers (pure; the Alerter supplies the paths) ----------------------------------
def _restrict(path: str, mode: int) -> None:
    """Narrow an EXISTING path's permissions. Silent when it does not exist or cannot be changed.

    The `mode=` arguments to `os.makedirs` and `os.open` apply only when the thing is CREATED —
    `open(2)` ignores the mode for an existing file, and `makedirs(exist_ok=True)` never chmods.
    So on any upgrade path they achieve nothing: a build that wrote this file 0644, or a
    directory an operator made over SMB, stays wide open forever while the docs claim otherwise.
    """
    try:
        if os.path.exists(path):
            os.chmod(path, mode)
    except OSError:
        # A filesystem that does not model these bits, or a file owned by someone else. Not worth
        # failing an alert over, and never worth raising from here.
        pass


def _roll_error_log(path: str, max_bytes: int, backups: int) -> None:
    """Start a fresh file once the current one reaches the cap, keeping `backups` generations.

    `os.replace` is atomic, so a reader never sees the log missing. Generations shift from the
    oldest down, `.1` being the most recent — the same numbering `read_jsonl_tail()` walks.

    At `backups=1` this is exactly a single-backup roll: the shifting loop below is empty and the
    only move is live → `.1`.
    """
    try:
        if os.path.getsize(path) < max_bytes:
            return
    except OSError:
        # No file yet — the overwhelmingly common case, on every fresh container. Rolling a file
        # that is not there would raise, and the append that follows creates it anyway.
        # (If the file exists but cannot be stat'd, this also declines to roll. That is the wrong
        # answer in principle, but the alternative — rolling on an unreadable stat — risks
        # destroying a log we cannot measure, and the append still lands either way.)
        return

    # One generation is kept STRUCTURALLY, whatever is passed: the shift loop below is empty for
    # anything under 2, and the live → `.1` move after it is unconditional. The clamp that DOES
    # bite is in `read_jsonl_tail()`, where a zero would stop the reader looking at a generation
    # the roller still writes.
    for i in range(backups, 1, -1):
        try:  # noqa: SIM105 — the `except` body carries the reason; suppress() would hide it
            os.replace(f"{path}.{i - 1}", f"{path}.{i}")
        except OSError:
            # That generation does not exist yet — normal until the log has rolled `backups`
            # times. Skipping it leaves the older ones where they are, which is correct.
            pass
    os.replace(path, f"{path}.1")


def _field(value: object, max_len: int = MAX_RECORD_FIELD) -> str:
    """One record field, coerced and bounded. Does not raise for any ordinary value.

    Coerced because a caller passing a dict would otherwise serialise as a nested object and
    quietly change the record SHAPE everything reading this file depends on. `repr` is called
    inside its own guard: an object whose `__repr__` raises is exactly the kind of thing that
    turns up in an exception handler, and this runs on the path that must never raise.

    Bounded because nothing else bounds it. The roll checks the size BEFORE appending, so one
    unbounded message writes one unbounded record and the "size cap" is not a cap at all —
    measured: a single 5 MB message produced a 5 MB file against a 1 MB cap.
    """
    if not isinstance(value, str):
        try:
            value = repr(value)
        except Exception:  # noqa: BLE001 — a caller's broken __repr__ must not reach the loop
            return "<unrepresentable>"
    if len(value) > max_len:
        return value[:max_len] + "…[truncated]"
    return value


# --- reading the error log back ----------------------------------------------------------------
# The other half of "retrievable". Writing the records is only useful if something can fetch them
# without a remote management agent: a service with an HTTP API exposes
# `GET /v1/admin/errors?since=&limit=` (admin scope) over HTTPS and answers it from here.
#
# These live in the shared library rather than being re-derived per service because one adopter
# already re-derived this read path and an audit found its `since` filter was a STRING comparison,
# which is wrong for two spellings that both look right (see `parse_since`).


def _parse_stamp(text: str) -> datetime:
    """Parse an ISO-8601 instant, tolerating a `Z` suffix on every interpreter we support.

    `datetime.fromisoformat` only learned `Z` in 3.11, and **this library supports 3.10**. Worse,
    `Z` is exactly what this module's own records carry — the sink stamps them with
    `time.strftime(..."%SZ")` — so on 3.10 an unnormalised parser raises `ValueError` on every
    record it wrote itself. That failure is invisible: `_record_is_at_or_after` KEEPS a record it
    cannot parse, so the `since` filter would silently degrade to "return everything".

    ⚠️ DECLARED LIMIT — this normalises the `Z` suffix and NOTHING ELSE. 3.11 also taught
    `fromisoformat` the rest of ISO-8601, so a record stamped in a spelling only 3.11 accepts —
    a 9-digit fractional second (RFC 3339 "nano", what Go emits), or the basic format
    `20260808T100000Z` — still parses on 3.12 and still fails on 3.10, and a failed parse is
    KEPT, so the `since` window silently widens on the older interpreter. This module's own
    writer emits `%SZ`, which normalises cleanly; the exposure is a consumer whose records come
    from a DIFFERENT writer. Widening this is a deliberate non-goal rather than an oversight: the
    fix for such a consumer is to stamp records with this module's sink, not to reimplement
    ISO-8601 here.
    """
    text = text.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def parse_since(since_iso: str) -> datetime | None:
    """`since` as an aware UTC datetime, or None for "no lower bound". Raises on garbage.

    ⭐ WHY NOT A STRING COMPARE. The obvious implementation filters with
    `str(rec["ts"]) < since_iso`, which is only correct when both sides are ISO-8601 in the SAME
    offset — and the caller's side is a free-text query parameter. Two shapes break it silently,
    with no error and a plausible-looking result:
      - a non-UTC offset. `2026-08-08T09:00:00-05:00` is 14:00Z, but it sorts BELOW every
        `…T10:00:00+00:00` record, so an operator asking "since 9am my time" gets five extra
        hours of records and no indication anything was wrong.
      - a `Z` suffix, which is what most tooling emits — and what this module writes. `Z` (0x5A)
        sorts ABOVE the `.` of a fractional second, so a record stamped in the same second as
        `since` is dropped.
    Comparing parsed instants is the only version that is right for every legal spelling; an
    ILLEGAL one raises, so an endpoint built on this answers 422 instead of quietly filtering
    wrong.

    Raises `ValueError` on an unparseable value and `OverflowError` on one that parses but cannot
    be shifted to UTC (`9999-12-31T23:59:59-05:00` crosses `datetime.max`). Both are caller
    errors and both must reach the endpoint as a 422 — the second one 500'd in the variant this
    was folded back from until verification found it.
    """
    text = (since_iso or "").strip()
    if not text:
        return None
    stamp = _parse_stamp(text)
    return (stamp.astimezone(timezone.utc) if stamp.tzinfo
            else stamp.replace(tzinfo=timezone.utc))


def _record_is_at_or_after(rec: dict, floor: datetime) -> bool:
    """Is this record at or after `floor`? An unparseable `ts` is KEPT.

    Keeping it is deliberate: a record whose timestamp cannot be read is exactly the kind of
    anomaly someone reading an error log needs to see, and dropping it would hide a corrupt
    writer behind an empty result.
    """
    try:
        stamp = _parse_stamp(str(rec.get("ts", "")))
    except (TypeError, ValueError):
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= floor


def read_jsonl_tail(path: str, limit: int, since_iso: str = "",
                    keep: Callable[[dict], bool] | None = None,
                    backups: int = ERROR_LOG_BACKUPS) -> list[dict]:
    """The most recent records from a rotating jsonl file, oldest-first. Never raises on I/O.

    Reads the rotated generations oldest → newest and then the live file, so a `since`/`limit`
    window can span a rotation. `since_iso` filters on each record's `ts` by comparing parsed
    instants (see `parse_since`); `keep(record)` is an optional extra predicate. Returns at most
    the newest `limit` records — `limit<=0` means no cap.

    `backups` MUST match what wrote the file, or the reader silently loses the oldest generation.
    `Alerter.read_errors()` passes the settings' value and is the call to prefer.

    A bad `since_iso` RAISES — `ValueError`, or `OverflowError` for a value that parses but
    cannot be shifted to UTC (see `parse_since`). A caller who asked for a window must not be
    handed the whole file as though they had asked for nothing. That is the one deliberate
    exception to "never raises" here, and it is the caller's own bad input, not an I/O failure.
    ⚠️ An endpoint must catch BOTH and answer 422: catching only `ValueError` turns
    `9999-12-31T23:59:59-05:00` into a 500. `limit` raises nothing — a non-integer raises
    `TypeError` at the comparison, which is a programming error rather than user input, and an
    absurdly large one is clamped rather than rejected.

    ⚠️ `path` is trusted. It is joined with `.1`/`.2`… to find the rotated generations and is
    otherwise passed straight to `open()`, so a consumer who lets a query parameter reach it has
    built an arbitrary-file read. Resolve the path from the service's own config, never from the
    request.
    """
    floor = parse_since(since_iso)
    generations = max(1, backups)
    files = [f"{path}.{i}" for i in range(generations, 0, -1)] + [path]
    # BOUNDED BY `limit`, not by the file. Accumulating every match and slicing at the end costs
    # memory proportional to the FILE rather than to the answer — measured at 122 MB peak for a
    # 40 MB log with `limit=1`. An endpoint answering `?limit=10` should not be a memory
    # amplifier for whatever the log grew to. A `maxlen` deque keeps the newest `limit` and
    # discards from the left as it goes, which is what the slice did anyway.
    #
    # `limit > 0` rather than `limit and limit > 0`: the `and` short-circuits on `None`, which
    # would make a MISSING query parameter mean "no cap" — silently reading the whole file, the
    # exact amplification this bound exists to stop. Comparing directly lets `None` raise the
    # `TypeError` it always used to, loudly, at the caller.
    #
    # `min(…, maxsize)` because `deque(maxlen=)` narrows to a C ssize_t and raises `OverflowError`
    # above 2**63, where the old end-slice simply worked. A limit that large means "no real cap"
    # anyway, so clamping preserves the answer instead of turning `?limit=99999999999999999999`
    # into a 500.
    out: deque[dict] | list[dict] = (deque(maxlen=min(limit, sys.maxsize)) if limit > 0 else [])
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001,S112 — a truncated final line is expected
                        # A roll or a crash can leave a partial record. One unreadable line must
                        # not cost the caller the rest of the file.
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if floor is not None and not _record_is_at_or_after(rec, floor):
                        continue
                    if keep is not None and not keep(rec):
                        continue
                    out.append(rec)
        except FileNotFoundError:
            continue  # that generation has not been rolled yet — normal
        except Exception as exc:  # noqa: BLE001 — an unreadable mount must not break retrieval
            log.warning("could not read the error log %s (%s) — its records are missing from "
                        "this result", fp, type(exc).__name__)
            continue
    return list(out)


def _escalate_gap_hours(count: int) -> float:
    """How long after the Nth send before an escalating condition may page again."""
    doublings = min(max(count - 1, 0), _MAX_DOUBLINGS)
    return min(ESCALATE_BASE_HOURS * (2 ** doublings), ESCALATE_MAX_HOURS)


def _clears_list(clears: object) -> list[str]:
    """Normalise `clears=` to a list of condition titles. Never raises.

    A caller that passes nonsense loses the clear; it must not also lose the alert, and it must
    not be left guessing — an ignored clear means the named condition stays marked firing, whose
    only symptom is a LATER alert that never arrives. So say so at ERROR now.
    """
    if clears is None:
        return []
    if isinstance(clears, str):
        return [clears]
    try:
        titles = list(clears)  # type: ignore[call-overload]
    except Exception:  # not just TypeError: a generator can raise mid-iteration
        log.error("alerting: clears=%r is neither a title nor an iterable of titles — ignoring "
                  "it. Any condition it meant to resolve stays marked firing.", clears,
                  exc_info=True)
        return []
    good = [t for t in titles if isinstance(t, str)]
    if len(good) != len(titles):
        log.error("alerting: clears=%r contains entries that are not condition titles — those "
                  "are ignored and stay marked firing.", clears)
    return good


def _is_cert_failure(exc: BaseException) -> bool:
    """Is this failure a TLS certificate that did not verify — i.e. possible interception?

    Not just `isinstance`: `urlopen` catches every `OSError` from the handshake and re-raises it
    as `URLError(err)`, and `URLError` is NOT an `SSLError`. So an intercepted ntfy connection
    arrives wrapped, and a plain `except ssl.SSLCertVerificationError` — which reads like it
    covers both channels — is dead code on the one that goes through urllib.
    """
    seen: set[int] = set()
    while isinstance(exc, BaseException) and id(exc) not in seen:
        if isinstance(exc, ssl.SSLCertVerificationError):
            return True
        seen.add(id(exc))  # a self-referencing cause chain must not spin forever
        # `URLError.reason` is often the wrapped exception, but it can also be a plain string —
        # hence the isinstance check on the loop rather than a None check.
        try:
            # `.reason` and `__cause__` are attribute ACCESSES, and an attribute access can run
            # arbitrary code. This walk happens inside the per-channel exception handler, so
            # anything escaping it aborts the fan-out mid-loop and costs the OTHER channel its
            # notification — which is the one thing this module must never do. The classifier
            # is not important enough to be able to break delivery: give up and say "not a
            # certificate failure", which only ever downgrades a log level.
            nxt = getattr(exc, "reason", None)
            exc = nxt if isinstance(nxt, BaseException) else exc.__cause__  # type: ignore[assignment]
        except Exception:  # noqa: BLE001 — a log-level decision must not break the fan-out
            return False
    return False


# --- the alerter ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Alerter:
    """One service's alerting, bound to its injected settings.

    Hold one of these and call `notify()` on it, or install it as the process default with
    `configure()` and use the module-level `notify()`. Nothing here reads the environment.
    """

    settings: AlertSettings

    # --- diagnostics ---------------------------------------------------------------------
    def state_file_problem(self) -> str:
        """Why the escalation state file is not durable, or "" if it is fine.

        It is a WARNING and never a refusal. A missing state file must not stop an alert going
        out; the failure direction here is re-paging too often, which is a nuisance, not a
        silence.
        """
        try:
            path = self.settings.state_file
            if path is None:
                # Reported rather than passed over in silence. For the service that MEANT it this
                # is one boot line confirming the opt-out took effect; for a service with an
                # escalating alert it is the only warning that its backoff is inert. Deliberately
                # NOT phrased as a misconfiguration — it is a supported setting, not a typo.
                return ("state_file is None, which disables de-duplication — every notify() call "
                        "is delivered, nothing is written to disk, and an escalating condition "
                        "re-pages at its caller's cadence instead of backing off")
            # No "is it blank" branch: `AlertSettings` REFUSES a blank path at construction, so
            # by the time anything holds an `Alerter` the only two states are None and a real
            # path. A diagnostic for an unreachable state would be a third answer nobody can act
            # on — and the version that existed here said "or to None to disable", which was
            # actively misleading while blank ALSO disabled.
            directory = os.path.dirname(path) or "."
            if not os.path.isdir(directory):
                return f"state_file points into {directory!r}, which does not exist"
            if not os.access(directory, os.W_OK):
                return f"state_file points into {directory!r}, which is not writable"
        except Exception as exc:  # noqa: BLE001 — a diagnostic must not become the failure
            # Broad on purpose, and not narrowed to the one failure that came to mind: this runs
            # on the alerting path, called from notify(), so ANY escape takes down the alert it
            # was only annotating. Answer "unusable" instead.
            return f"state_file could not be checked ({type(exc).__name__})"
        return ""

    def error_log_problem(self) -> str:
        """Why the error log will not be retrievable, or "" if it looks fine.

        A consumer whose `error_log` points somewhere the volume is not mounted gets
        `os.makedirs` SUCCEEDING inside the container's own writable layer. No error, no warning,
        records accumulating — destroyed on every recreate and unreachable from the host. That is
        exactly the retrieval failure this sink exists to fix, arriving disguised as success.
        """
        try:
            path = self.settings.error_log
            if path is None:
                return ("error_log is None — WARN and ERROR alerts are in the process log only, "
                        "with no small retrievable file to fetch off the host")
            # No "is it blank" branch — see the note in `state_file_problem`.
            directory = os.path.dirname(path) or "."
            if os.path.isdir(directory):
                if not os.access(directory, os.W_OK):
                    return f"error_log points into {directory!r}, which is not writable"
                return ""
            parent = os.path.dirname(directory.rstrip("/\\")) or os.sep
            if not os.path.isdir(parent):
                return (f"error_log points into {directory!r}, and even its parent {parent!r} "
                        f"does not exist — in a container that usually means the volume is not "
                        f"mounted, so these records would be written into the disposable layer "
                        f"and could not be read from the host")
        except Exception as exc:  # noqa: BLE001 — a diagnostic must not become the failure
            return f"error_log could not be checked ({type(exc).__name__})"
        return ""

    def warn_if_unconfigured(self) -> list[str]:
        """Boot check: report which channels are live, and WARN loudly if none are.

        A service whose alerts go nowhere looks exactly like a service with nothing to report,
        which is how two services quietly stopped alerting. Returns the ready channels.

        Like `notify()`, this cannot raise: it runs at the top of the service's main loop, so an
        exception here is a boot crash — a restart loop over a mistyped config file.
        """
        service = self.settings.service
        # FIRST, before the config load — that load has an `except` which returns, and a boot with
        # an unreadable config file is exactly the boot that needs both warnings rather than one.
        problem = self.state_file_problem()
        if problem:
            log.warning("ALERTING STATE for %s: %s. A service with an escalating alert needs a "
                        "persistent state_file.", service, problem)
        problem = self.error_log_problem()
        if problem:
            log.warning("ALERTING ERROR LOG for %s: %s. Set error_log to a path inside a "
                        "mounted volume.", service, problem)
        try:
            ready = self.config().ready_channels()
        except Exception as exc:  # noqa: BLE001 — bad config must not stop the service booting
            log.warning("ALERTING CONFIG UNREADABLE for %s (%s) — treating every channel as "
                        "unconfigured; alerts will go NOWHERE", service, type(exc).__name__)
            return []
        if ready:
            log.info("alerting: %s ready for %s", " + ".join(ready), service)
        else:
            log.warning("ALERTING UNCONFIGURED for %s — no email and no ntfy channel is usable, "
                        "so every alert this service raises will go NOWHERE. Set ntfy_url to the "
                        "full topic URL and/or point config_file at the shared email settings.",
                        service)
        return ready

    def config(self) -> AlertConfig:
        """This service's config, read fresh. See `AlertConfig.load`."""
        return AlertConfig.load(self.settings)

    def setting_faults(self) -> list[str]:
        """Rejected settings from the shared file, for this service's boot report."""
        return self.config().setting_faults()

    # --- the retrievable error log --------------------------------------------------------
    def read_errors(self, limit: int, since_iso: str = "",
                    keep: Callable[[dict], bool] | None = None) -> list[dict]:
        """The newest error-log records, oldest-first. `[]` when no sink is configured.

        Passes the settings' `error_log_backups` so the reader and the roller cannot disagree
        about how many generations exist. See `read_jsonl_tail` for the raising contract on
        `since_iso`.
        """
        path = self.settings.error_log
        if not path:
            return []
        return read_jsonl_tail(path, limit, since_iso, keep,
                               backups=self.settings.error_log_backups)

    def _append_error_record(self, severity: object, title: object, message: object) -> None:
        """Append one JSON line describing this alert. Never raises.

        Guarded exactly like a channel, and for the same reason: a sink that cannot write must not
        take down the notification it was recording. A full disk would otherwise turn one lost log
        line into total silence.

        EVERYTHING is inside the guard, including resolving the path and building the record.
        Those two lines sat outside it and could raise — on an object whose `__repr__` raises, or
        on a clock so far out of range that `time.gmtime` refuses it — which made `notify()`
        raise, out of the caller's handler, out of its loop, and out of the container. On
        `restart: no` that is a container which never comes back and never says why. The irony
        was that those lines existed precisely to tolerate absurd callers.
        """
        try:
            # ⛔ INSIDE the guard, including resolving the path. It sat outside, which made the
            # paragraph above false: `self.settings` is an attribute access, and an attribute
            # access can run arbitrary code — a duck-typed or lazily-resolved settings object
            # whose `error_log` property raises would have travelled straight out of `notify()`.
            # Nothing here is allowed to be the one statement that breaks the invariant.
            path = self.settings.error_log
            if not path:
                return
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_now())),
                "severity": _field(severity, self.settings.max_record_field),
                "title": _field(title, self.settings.max_record_field),
                "message": _field(message, self.settings.max_record_field),
                "service": _field(self.settings.service, self.settings.max_record_field),
            }
        except Exception as exc:  # noqa: BLE001 — building a log line must never break alerting
            log.warning("could not build an error-log record (%s) — this alert is in the process "
                        "log only", type(exc).__name__)
            return

        try:
            directory = os.path.dirname(path)
            if directory:
                # 0o700: these records carry alert MESSAGE BODIES, and the directory is expected
                # to be treated as operator-confidential.
                os.makedirs(directory, mode=0o700, exist_ok=True)
                _restrict(directory, 0o700)
            _restrict(path, 0o600)
            _roll_error_log(path, self.settings.error_log_max_bytes,
                            self.settings.error_log_backups)
        except Exception as exc:  # noqa: BLE001 — housekeeping must not cost us the record
            # A roll that fails is not a reason to drop the alert being recorded; try the append
            # anyway. Worst case the file grows past its cap, which beats losing the error.
            log.warning("could not prepare the error log %s (%s)", path, type(exc).__name__)
        try:
            # os.open rather than open(), so a NEW file is created 0o600 rather than created wide
            # and narrowed afterwards. An existing one was already narrowed by `_restrict` above,
            # which is the case `mode=` alone silently misses.
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                # `json.dumps` escapes newlines, so a traceback stays ONE line. Without that the
                # file is unparseable from the first multi-line error onward — and a traceback is
                # the most likely thing to end up in here.
                fh.write(json.dumps(record) + "\n")
            # AFTER the write as well as before it. The `_restrict` above cannot narrow a file
            # that does not exist yet, so on the very FIRST alert the mode came only from
            # `os.open` — and if that is ever wrong, the first record sits exposed until the next
            # alert, which on a quiet service can be days. Deliberately redundant with the mode
            # argument: both are cheap, and the failure they guard is a credential-bearing file
            # readable by anything on the host.
            _restrict(path, 0o600)
        except Exception as exc:  # noqa: BLE001 — never raises, like every other sink here
            log.warning("could not append to the error log %s (%s) — this alert is in the process "
                        "log only", path, type(exc).__name__)

    # --- edge-trigger / escalate bookkeeping ----------------------------------------------
    def _dedup_enabled(self) -> bool:
        """Whether the title-keyed state-file de-duplication runs at all.

        A service that ALREADY de-duplicates its own conditions upstream passes `state_file=None`
        to deliver every call it makes, rather than run a redundant second suppression layer —
        which, for a condition that never emits an `OK` recovery, would otherwise mute its next
        genuine occurrence forever.

        Gated at the two functions that touch the filesystem rather than at each of their callers,
        because a caller added later would not know to ask.
        """
        path = self.settings.state_file
        return bool(path and path.strip())

    def _read_state(self) -> dict[str, dict]:
        """Currently-firing conditions, keyed by title. A missing or unusable file means "nothing
        is firing", which errs towards sending — the safe direction for a de-duplicator."""
        if not self._dedup_enabled():
            # "Nothing is firing" is exactly the right answer with the opt-out set: every
            # condition then looks new, so every call is delivered. Gated HERE as well as at the
            # write, so a path that only reads cannot open a file either.
            return {}
        path = str(self.settings.state_file)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except Exception as exc:  # noqa: BLE001 — corrupt JSON, wrong encoding, unreadable mount
            log.warning("alert state %s unreadable (%s) — treating every condition as new, so "
                        "this alert goes out", path, type(exc).__name__)
            return {}
        return data if isinstance(data, dict) else {}

    def _prune(self, state: dict[str, dict]) -> dict[str, dict]:
        """Keep the remembered conditions bounded, dropping the oldest first.

        Nothing else prunes this file. It used to be bounded by accident — any `OK` wiped it —
        and per-condition clearing removed that accident. A consumer whose titles carry a
        per-occurrence id ("backup failed for job-1234") would grow the file forever while
        re-reading and re-parsing it on every single notification.

        Dropping a condition makes it alert again if it recurs, which is the direction this
        module always fails in.
        """
        cap = self.settings.max_tracked_conditions
        if len(state) <= cap:
            return state

        def first_seen(item: tuple[str, dict]) -> float:
            try:
                return float(item[1].get("first", 0.0))
            except (AttributeError, TypeError, ValueError):
                return 0.0  # junk sorts oldest, so it is what gets dropped

        keep = sorted(state.items(), key=first_seen)[-cap:]
        log.warning("alert state is tracking more than %d conditions — dropping the %d oldest, "
                    "which will alert again if they recur. A title that carries a unique id per "
                    "occurrence does this: key the CONDITION, not the instance.",
                    cap, len(state) - len(keep))
        return dict(keep)

    def _write_state(self, state: dict[str, dict]) -> None:
        """Persist, atomically, and never let a failure reach the caller.

        Write-then-rename so a crash mid-write cannot leave a half-written file that would then
        read as corrupt. If the whole thing fails the service simply forgets — it re-pages a
        condition it had already reported, which is the right way round to be wrong.
        """
        if not self._dedup_enabled():
            # Every other gate decides whether to SEND; this one decides whether anything is
            # PERSISTED, and it covers `_forget_unreported()`, which writes on the
            # failed-delivery path.
            return
        path = str(self.settings.state_file)
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._prune(state), fh)
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001 — a read-only mount must not break alerting
            log.warning("could not save alert state to %s (%s) — recurring conditions will "
                        "re-alert", path, type(exc).__name__)

    def _peek_condition(self, title: str) -> object:
        """This condition's entry before anything touches it, so a notification that reaches
        nobody can be un-recorded. `_MISSING` means it was not firing."""
        try:
            return self._read_state().get(title, _MISSING)
        except Exception:  # noqa: BLE001 — a snapshot for a rollback must not become the failure
            return _MISSING

    def _forget_unreported(self, title: str, previous: object) -> None:
        """Undo the "already firing" note for a notification that no channel delivered.

        The note is written BEFORE the send on purpose — a crash between the two then re-alerts
        rather than going quiet. But leaving it behind when NOTHING reached a human turns a
        delivery outage into a permanent silence: the condition reads as already-reported, so an
        edge-triggered one is suppressed from then on and only an `OK` naming it will ever bring
        it back.
        """
        try:
            state = self._read_state()
            if previous is _MISSING:
                if title not in state:
                    return
                state.pop(title, None)
            else:
                if state.get(title) == previous:
                    return
                state[title] = previous  # type: ignore[assignment]
            self._write_state(state)
            log.info("alerting: %r reached no channel, so it stays un-reported — the next "
                     "occurrence will alert instead of being de-duplicated against a delivery "
                     "that failed.", title)
        except Exception as exc:  # noqa: BLE001 — the rollback must not break the caller either
            log.warning("could not un-record %r after a failed delivery (%s) — its next "
                        "occurrence may be suppressed", title, type(exc).__name__)

    def _should_send(self, severity: str, title: str, escalating: bool,
                     clears: object = None) -> bool:
        """Is this notification new information? Records the decision. Callers treat a raise as
        YES.

        The condition's identity is its TITLE. The message body carries per-occurrence detail (a
        timestamp, an exception string), so keying on it would make every occurrence look new and
        nothing would ever be de-duplicated.
        """
        state = self._read_state()
        resolved = _clears_list(clears)

        if resolved and severity != OK:
            # Refused rather than honoured. `notify(ERROR, "x", …, clears="x")` inside a retry
            # loop would delete its own escalation bookkeeping on every attempt, so a condition
            # that never recovers would page at the base gap forever and never escalate — and the
            # caller would have no way to tell, because the alerts still arrive.
            log.error("alerting: clears=%r was passed with severity %s and is IGNORED — a "
                      "condition is resolved by an OK, not by another alert. Those conditions "
                      "stay firing. To downgrade a condition rather than end it, emit the OK "
                      "that clears it and then the WARN describing what remains.", clears,
                      severity)

        if severity == OK:
            # Never suppressed — an OK is a confirmation, not a condition, and for a service that
            # reports in on a cadence it IS the heartbeat.
            #
            # It clears PER CONDITION: its own title, plus whatever it names in `clears=`. Not
            # everything. A service with several independent conditions would otherwise have any
            # one routine confirmation re-arm all of them — and a periodic self-test, which is an
            # OK, would wipe the entire firing state every time it ran.
            cleared = [t for t in dict.fromkeys([title, *resolved]) if t in state]
            if cleared:
                for name in cleared:
                    state.pop(name, None)
                log.info("alerting: recovery — re-arming %s", ", ".join(cleared))
                self._write_state(state)
            return True

        if escalating:
            # Only for escalating conditions: this is the one case where losing the state file
            # changes behaviour that matters — the backoff cannot widen if nothing remembers the
            # count, so an unresolved condition pages at full rate instead of tapering.
            problem = self.state_file_problem()
            if problem:
                log.warning("alerting: %s. This condition is escalating, so without durable "
                            "state it will re-page at the base cadence rather than backing off.",
                            problem)

        entry = state.get(title)
        if not isinstance(entry, dict):
            # Not firing (or the entry is junk): this is the edge, so it always goes out.
            state[title] = {"first": _now(), "last": _now(), "count": 1}
            self._write_state(state)
            return True

        if not escalating:
            log.info("alerting: %r is already firing and has not cleared — not re-sending "
                     "(edge-trigger). It will alert again after the next OK.", title)
            return False

        try:
            count = int(entry.get("count", 1))
            last = float(entry.get("last", 0.0))
        except (TypeError, ValueError):
            count, last = 1, 0.0  # junk in the file must not decide to stay silent

        gap_h = _escalate_gap_hours(count)
        waited_h = (_now() - last) / 3600
        if 0 <= waited_h < gap_h:
            # `0 <=` on purpose: a clock that jumped BACKWARDS makes waited_h negative, and
            # treating that as "not long enough" would suppress an escalating alert for as long
            # as the skew lasts. Send it instead.
            log.info("alerting: %r still firing; next escalation in %.1fh", title,
                     gap_h - waited_h)
            return False

        entry["last"] = _now()
        entry["count"] = count + 1
        state[title] = entry
        self._write_state(state)
        log.info("alerting: %r still firing after %.1fh — escalating (send #%d)",
                 title, waited_h, count + 1)
        return True

    # --- dispatch -------------------------------------------------------------------------
    def _dispatch(self, spec: SeveritySpec, title: str, message: str) -> dict[str, str]:
        cfg = self.config()
        results: dict[str, str] = {}
        for name, send in _CHANNELS:
            # Readiness is evaluated INSIDE this channel's own guard. Hoisting it out — which
            # reads tidier — means a config value that breaks one channel's check takes the other
            # channel's notification with it, and reports it as "failed" when it was never
            # attempted.
            try:
                if not cfg.is_ready(name):
                    results[name] = "skipped"
                    continue
                send(cfg, spec, title, message)
                results[name] = "sent"
            except Exception as exc:  # noqa: BLE001 — one dead channel must not silence the other
                results[name] = "failed"
                if _is_cert_failure(exc):
                    # Louder than a transient outage on purpose: a certificate that does not
                    # verify is the signature of something sitting between this service and the
                    # server.
                    log.error("%s alert failed TLS VERIFICATION — the server's certificate did "
                              "not validate, which is what an intercepted connection looks "
                              "like: %s", name, exc)
                else:
                    log.warning("%s alert failed: %s: %s", name, type(exc).__name__, exc)
        return results

    def notify(self, severity: str, title: str, message: str, escalating: bool = False,
               clears: object = None) -> dict[str, str]:
        """Dispatch one operator notification to every configured channel.

        `severity` is one of OK / WARN / ERROR and drives the log level, the ntfy priority + tag,
        and the subject prefix.

        `escalating` picks the repeat behaviour for a condition that keeps firing. `False` (the
        default) alerts once and stays quiet until an OK clears it. `True` keeps re-alerting on a
        widening backoff — for conditions where going quiet is itself the danger. See the module
        docstring; an OK is never suppressed either way.

        `clears` names the condition title(s) this notification resolves — a string or an iterable
        of strings. Honoured on `OK` only, where it re-arms exactly those conditions plus this
        notification's own title, and nothing else.

        Returns {channel: "sent"|"failed"|"skipped"|"suppressed"} — the return exists for tests
        and for a caller that wants to log the outcome; ignoring it is the normal case.

        NEVER raises: an unknown severity, an unreachable SMTP server and a 500 from ntfy all end
        up as a log line. The caller's loop must not die because a notification did.
        """
        try:
            spec = SEVERITIES.get(severity)
        except Exception:  # noqa: BLE001 — not only TypeError: `dict.get` propagates whatever the
            # key's own `__hash__` raises, and "unhashable" is only the tidiest way for a caller
            # to get this wrong. A severity argument must never be able to take the caller down.
            spec = None
        recognised = spec is not None
        if spec is None:
            # Fail LOUD, not silent: an unrecognised severity is a bug, and the safe assumption is
            # that whatever the caller was reporting mattered.
            log.error("unknown alert severity %r — treating as %s", severity, ERROR)
            spec = SEVERITIES[ERROR]

        # The log line goes out FIRST and outside every guard below: whatever happens to the
        # channels, and whether or not this is a repeat, the process log has the message.
        # Logging is not paging.
        log.log(spec.log_level, "%s %s — %s", spec.prefix, title, message)

        # And into the retrievable error log, for the same reason and at the same point: BEFORE
        # the de-duplication decision, so a suppressed repeat is still recorded. De-duplication
        # exists to protect the operator's phone, not to hide occurrences from the file being used
        # to troubleshoot them. An unrecognised severity lands here too — it is treated as ERROR
        # everywhere else, and this must not be the one place a caller bug makes something vanish.
        if spec.to_error_log:
            # The EFFECTIVE severity, not the raw one. An unrecognised value is treated as ERROR
            # everywhere else, and a reader filtering this file on WARN/ERROR — which the fixed
            # record shape invites — would otherwise drop it entirely. The raw value is not lost:
            # the "unknown alert severity %r" line above carries it.
            self._append_error_record(severity if recognised else ERROR, title, message)

        # Captured BEFORE the de-duplicator records this condition as firing, so a notification
        # that turns out to reach nobody can be put back the way it was. See `_forget_unreported`.
        #
        # The OK/unrecognised exclusion here is DELIBERATELY redundant with the identical guard on
        # the rollback below — either one alone is sufficient. Both are kept: this one avoids a
        # pointless file read on the most frequent notification there is, and the one below states
        # the rule at the point the decision is made. Removing either is safe; removing both
        # restores a firing condition that has just recovered, on the one delivery that failed.
        previous = _MISSING if (not recognised or severity == OK) else self._peek_condition(title)

        if not recognised:
            # A caller bug must never make this module QUIETER. An unrecognised severity is not
            # de-duplicated (it always goes out) and it cannot clear the firing state either — one
            # typo would otherwise mute every condition at once.
            send = True
        else:
            try:
                # INSIDE this guard. `_should_send` touches the filesystem, and anything that
                # raises there would have travelled straight out of `notify()` — which is called
                # from `except` handlers, so it takes the caller's loop and, on `restart: no`, the
                # container with it. "Never raises" here has to be structural rather than a
                # promise.
                #
                # `state_file=None` means this service de-duplicates its own conditions upstream,
                # so every call it makes is already an edge — deliver it. Nothing is stored, so an
                # OK clears nothing and no title is ever suppressed.
                #
                # Redundant with the gates inside `_read_state()`/`_write_state()`, which alone
                # already produce this outcome: an always-empty state makes every condition look
                # new. Kept anyway because those gates make the opt-out SAFE while this makes it
                # INTENDED — without it, "delivers everything" is an emergent consequence of two
                # unrelated early returns that a later edit could silently take away.
                send = (True if not self._dedup_enabled()
                        else self._should_send(severity, title, escalating, clears))
            except Exception as exc:  # de-duplication must fail OPEN, always
                # Its own boundary, not the one below, because the two failures need opposite
                # defaults: a broken channel means "report failed", a broken de-duplicator means
                # "SEND IT". An accidental silence here is indistinguishable from nothing being
                # wrong, which is the single outcome this module exists to prevent.
                log.error("alert de-duplication failed (%s) — sending anyway. This is a bug in "
                          "the escalation bookkeeping; the traceback says where.",
                          type(exc).__name__, exc_info=True)
                send = True
        if not send:
            return {name: "suppressed" for name, _ in _CHANNELS}

        try:
            results = self._dispatch(spec, title, message)
        except Exception as exc:  # noqa: BLE001 — "never raises" is structural, not a promise
            # Reading the config can fail in ways no individual channel is responsible for (an
            # unreadable mount, a file in the wrong encoding). Every one of those used to travel
            # out of here into a caller that is often already inside an `except` block.
            log.error("alerting failed before any channel could be tried: %s: %s",
                      type(exc).__name__, exc)
            results = {name: "failed" for name, _ in _CHANNELS}

        if recognised and severity != OK and not any(r == "sent" for r in results.values()):
            # Nothing reached a human, so this condition was not actually reported. Leaving it
            # marked firing would de-duplicate the NEXT occurrence against a delivery that never
            # happened.
            self._forget_unreported(title, previous)
        return results


# --- the process default ------------------------------------------------------------------------
# A module-level default so the call site — usually deep inside an `except` handler — does not
# have to thread an object through. It is set explicitly by `configure()`; there is no implicit
# construction from the environment, because guessing a service's paths is the thing this library
# refuses to do.
_default: Alerter | None = None


def configure(settings: AlertSettings) -> Alerter:
    """Install `settings` as the process-wide default and return the `Alerter` it built.

    Call this once at startup. Calling it again replaces the default — which is what a test wants
    and what a service reloading its own config wants; nothing caches the old one.
    """
    global _default
    _default = Alerter(settings)
    return _default


def current_alerter() -> Alerter | None:
    """The installed process default, or `None` if `configure()` has not been called."""
    return _default


# ⭐ Every message in the two functions below is formatted LAZILY by `logging` (`%s`/`%r` with
# arguments) and never by an f-string. An f-string evaluates the caller's `__str__`/`__repr__` at
# the call site, BEFORE logging is reached, so a hostile argument propagates out; the `%`-style
# form defers it into `logging`, which prints its own error to stderr and returns. That difference
# is the whole bug these two functions used to have.


def notify(severity: str, title: str, message: str, escalating: bool = False,
           clears: object = None) -> dict[str, str]:
    """`Alerter.notify` on the process default installed by `configure()`.

    If nothing was configured this still cannot raise, and it still writes the log line — logging
    is not paging, and a caller inside an `except` handler must not be punished for a startup
    mistake. Every channel comes back `"skipped"`, and the reason is stated at ERROR.

    ⭐ The unconfigured branch is held to the SAME never-raises standard as `Alerter.notify`, and
    it is tested against the same hostile arguments. It previously was not, which was the worst
    possible place to be lax: the population this branch exists to protect is precisely the
    process that got its startup wrong, and it died in the caller's `except` handler instead of
    logging. Everything below is either lazily formatted or inside a guard.
    """
    alerter = _default
    if alerter is None:
        _unconfigured_notify(severity, title, message)
        return {name: "skipped" for name, _ in _CHANNELS}
    return alerter.notify(severity, title, message, escalating=escalating, clears=clears)


def _unconfigured_notify(severity: object, title: object, message: object) -> None:
    """Log what an unconfigured process would have alerted about. Never raises."""
    try:
        spec = SEVERITIES.get(severity)  # type: ignore[call-overload]
    except Exception:  # noqa: BLE001 — `dict.get` propagates the key's own `__hash__`
        # Same reasoning as `Alerter.notify`: an unhashable severity is a caller bug, and a
        # caller bug must never be able to take down the process it is reporting from.
        spec = None
    if spec is None:
        log.error("unknown alert severity %r — treating as %s", severity, ERROR)
        spec = SEVERITIES[ERROR]
    log.error("kw_common.alerting is NOT configured — %s %r reached no channel. Call "
              "configure(AlertSettings(...)) at startup; until then every alert this process "
              "raises reaches the process log and NOTHING else.", severity, title)
    log.log(spec.log_level, "%s %s — %s", spec.prefix, title, message)


def warn_if_unconfigured(service: str | None = None) -> list[str]:
    """`Alerter.warn_if_unconfigured` on the process default installed by `configure()`.

    `service` is accepted and ignored when a default is installed — the name comes from the
    settings, so the two cannot disagree. It is here because the boot call site usually has the
    name to hand, and because a process that never called `configure()` still deserves a warning
    that says WHICH service went unconfigured.

    Never raises, including for a `service` whose `__str__` does.
    """
    alerter = _default
    if alerter is None:
        # `%r` of the raw object, lazily — not `f"...{service}"`, which evaluates it here.
        log.error("kw_common.alerting is NOT configured — no channel is usable for %r. Call "
                  "configure(AlertSettings(...)) at startup; until then every alert this process "
                  "raises reaches the process log and NOTHING else.", service)
        return []
    return alerter.warn_if_unconfigured()
