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
    - **Use an `https://` ntfy topic URL.** A topic URL is a WRITE CAPABILITY — whoever observes
      it can page the operator from then on — so `http://` is refused by default and the channel
      counts as unconfigured. `AlertSettings(allow_cleartext_ntfy=True)` is the supported opt-in
      for a self-hosted endpoint on a trusted network. Userinfo (`https://user:pass@host/topic`)
      is refused outright: `urllib` cannot send it, and its failure printed the password.

WHAT THIS MODULE WILL NOT PUT IN A LOG
    Its own configuration. The failure paths here are reached BY an exception raised inside
    third-party code that was holding this module's settings, and several stdlib exceptions quote
    the value that upset them — so a misconfigured channel used to write a credential into the
    process log on EVERY alert, repeating forever, in the log an operator is most likely to paste
    into a bug report. Three things hold the line, in order of how much they are trusted:

    1. **Refusals at the source.** A URL with userinfo and a non-ASCII SMTP credential are both
       rejected before the code that would quote them is reached. Neither ever worked.
    2. **Redaction of what remains** (`_redact`), covering `SMTP_PASSWORD` and the topic URL.
       Read its docstring for what it does NOT catch, which is the part that matters.
    3. **Shape, never value**, wherever this module describes a setting the ALERTING PATH
       rejected — see `_fault_shape`. Scoped deliberately: `AlertSettings.__post_init__` does
       echo the value of a rejected integer sizing knob, which is a construction-time refusal an
       operator sees once at boot, not a line that repeats per alert. Say the rule where it
       binds rather than stating it absolutely and being wrong one function away.

    None of this reaches what a CALLER puts in a title or a message. Sanitise at the raise site,
    where the value is understood.

    ⚠️ AND "ITS OWN CONFIGURATION" IS SCOPED TO THE TWO VALUES `_secret_values` NAMES — the
    SMTP password and the topic URL. `EMAIL_TO`, `EMAIL_FROM` and `SMTP_USER` are NOT redacted,
    and smtplib quotes them: `SMTPSenderRefused` carries the sender, `SMTPRecipientsRefused` the
    recipients, and a relay that echoes the login name puts `SMTP_USER` into
    `SMTPAuthenticationError`. Those are addresses rather than credentials, and the line is a
    diagnostic an operator needs — stated here so the claim above is not read as wider than it is.

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
    file is created 0o600 in a 0o700 directory, and narrowing an existing one to the same is
    ATTEMPTED on every write, because a mode passed at creation does nothing for a path that
    already exists. ⚠️ Attempted, not guaranteed: on a bind mount whose uid does not match this
    process the `chmod` is refused, and then the file keeps whatever mode it had — a WARNING says
    so, naming the path and the errno, which is the whole of #21. Treat it as
    operator-confidential, not as safe to hand around, and read the boot log before believing the
    mode is what this paragraph asks for.
"""

from __future__ import annotations

import contextlib
import errno
import http.client
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


def _usable_path(value: object) -> str | None:
    """This path setting as a real `str`, or `None` if it is not set to anything usable.

    ⭐⭐ ONE EXPRESSION, EVERY CALL SITE, AND THAT IS THE ENTIRE POINT OF IT EXISTING.

    It returns the NORMALISED value rather than a yes/no, which does two jobs in one call: it
    answers "is this configured", and it hands the caller a genuine `str`. So a settings object
    carrying a `Path` is corrected at the boundary instead of being detected at one gate and then
    breaking at the next — which is precisely how both defects below happened.

    Six places in this module ask "is there a path here", and they must not be able to disagree.
    They did, twice, and both times the disagreement was silent and pointed the wrong way:

      * `state_file_problem()` normalised with `str(path)` while `_dedup_enabled()` did a raw
        `path.strip()`, so a `Path` made one report "fine" and the other raise `AttributeError`,
        which `notify()`'s fail-open guard swallowed — de-duplication OFF, boot report silent.
      * that was repaired for `state_file` and left for `error_log`, where the diagnostic says
        the sink is off while `_append_error_record` still attempts the write — on POSIX,
        creating a file literally named `"   "` in the process's working directory.

    Fixing the second one by hand would have left a third. This is the same reasoning the error
    log's generation count already uses: when two sides of one question can drift, give them one
    expression to read rather than a convention to remember.

    `str(...)` because a settings object that bypassed `AlertSettings` may hold a `Path`, a
    `bytes`, or a `str` subclass whose `strip()` lies — and this must answer, not raise, for all
    of them.
    """
    if value is None:
        return None
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 — a settings object's __str__ is not ours to trust
        # An object whose `str()` raises is not a usable path, and deciding that must not become
        # the failure. Every caller treats None as "this sink is off", which is the safe
        # direction: the diagnostics report it and nothing tries to open it.
        return None
    return text if text.strip() else None


def _opted_into_cleartext(settings: object) -> bool:
    """Has this settings object EXPLICITLY opted in to a cleartext (`http://`) ntfy topic?

    ⭐ `is True`, NOT `bool(...)`, and the difference is the whole point. This answers a SECURITY
    question, and an `Alerter` takes any settings-shaped object without validating it — the same
    population `state_file_problem()`'s blank backstop exists for. `AlertSettings` refuses a
    non-bool at construction (`"false"` is TRUE to Python, so honouring it would opt a deployment
    IN by reading its opt-out), but an object that never went through that constructor gets its
    value read here. Under `bool(...)` the string `"false"`, `"0"` and `"no"` would each opt in.
    Only the literal `True` does.

    A missing attribute means "not opted in", and so does an attribute access that RAISES: this
    runs on the alerting path, and a settings object with a misbehaving property must not be able
    to turn a refusal into an exception — or into an opt-in.
    """
    try:
        return getattr(settings, "allow_cleartext_ntfy", False) is True
    except Exception:  # noqa: BLE001 — an attribute access is the caller's code
        return False


# --- keeping configured secrets out of the log ---------------------------------------------------
_REDACTED = "<redacted>"


def _safe_text(exc: BaseException) -> str:
    """`str(exc)` for an exception this module did not raise, without trusting its `__str__`.

    The failure path is reached BY an exception, so it is the one place where calling more of a
    third party's code is least excusable. `%s`-lazy formatting used to defer this into stdlib
    logging, which SWALLOWS the second failure and drops the record entirely — the alert's only
    surviving trace vanishing because the exception object was hostile.
    """
    try:
        return str(exc)
    except Exception:  # noqa: BLE001 — a hostile __str__ must not become the failure
        return "<an exception whose str() raised>"


def _secret_values(cfg: AlertConfig) -> list[str]:
    """Every value in THIS config that must not appear in a log line.

    Two things, and the reasoning differs for each:

    * **`SMTP_PASSWORD`** — a secret in the ordinary sense.
    * **The ntfy topic URL, its path and any userinfo** — a topic URL is a WRITE CAPABILITY, not
      an address: whoever holds it can page the operator. The module already refuses to echo it
      from `ntfy_ready()`'s own failure branches; this is the same rule applied to text that
      arrives from somewhere else.

    The HOST is deliberately NOT in the set: it is not itself the capability, and it is the half
    of the URL an operator needs in order to act. The example this used to give was wrong and is
    worth correcting rather than deleting — MEASURED, neither `ConnectionRefusedError` nor
    `socket.gaierror` names the host in its message, so "connection refused and DNS failures name
    the host" was false. What DOES name it is `SSLCertVerificationError`'s hostname-mismatch form
    ("certificate is not valid for 'x'") — which lands in the TLS branch, the one place a host is
    the whole diagnosis. That is the reason to keep it; the old one was not.
    """
    secrets: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str) and value.strip():
            secrets.append(value)

    email = cfg.email or {}
    # ⭐ `.get`, UNGUARDED — and NOT `isinstance(email, dict)`, which is what this was. The send
    # path reads this mapping with `.get()` and nothing more, so any Mapping sends perfectly well
    # while an `isinstance(dict)` gate made the REDACTOR skip its password entirely: two sides of
    # one question able to disagree, the shape `_usable_path` exists to prevent one function over.
    # Unguarded because an object whose `.get` raises leaves "does this text hold a secret"
    # UNANSWERABLE, and `_redact`'s own handler turns that into suppressing the text — which is
    # the safe direction. Catching it here would silently redact nothing instead.
    add(email.get("SMTP_PASSWORD"))

    url = cfg.ntfy_url
    if isinstance(url, str) and url.strip():
        add(url)
        try:
            parts = urlsplit(url)
        except ValueError:
            # Unparseable — the full-URL entry above still stands, and `ntfy_ready()` has already
            # disabled this channel. Do not let a URL this module refused to use break redaction
            # of the password, which is in the same set.
            parts = None
        if parts is not None:
            # `netloc.rpartition("@")` and not `parts.username`/`parts.password`: those decode
            # percent-escapes, so what they return is not necessarily the text an exception
            # message would be quoting. The raw slice is what appears verbatim.
            userinfo = parts.netloc.rpartition("@")[0]
            add(userinfo)
            add(userinfo.partition(":")[2])
            # The topic itself, in BOTH the shapes an exception quotes it in: as the selector
            # (`/topic`, which is what `http.client` carries) and bare (`topic`, which is what a
            # message naming the topic uses). Covering only the first left the second live.
            # `"/"` alone is not a secret and would redact every slash in the message, which is
            # the over-redaction that makes an operator stop reading the line.
            topic = parts.path.strip("/")
            if topic:
                add(parts.path)
                add(topic)
    return secrets


def _redact(text: str, cfg: AlertConfig) -> str:
    """`text` with every configured secret replaced. A BACKSTOP — read what it does NOT do.

    ⚠️ IT CATCHES A SECRET QUOTED VERBATIM, AND ONLY THAT. A message that renders a secret in any
    TRANSFORMED shape — one character as an escaped `repr` plus its offset, a percent-DEcoded
    spelling of what was configured percent-encoded, a hash, base64, a length — passes straight
    through, because a substring replacement cannot see it. That is not a hypothetical gap: every
    leak found in this module so far has been of exactly that shape, which is why each one is
    closed at its SOURCE in `ntfy_ready()` / `_send_email()` rather than left to this function.
    Do not weaken any of those on the strength of this one.

    Two more limits, both measured, both inherent to substring replacement:

    * **A repeating-pattern secret can leave a FRAGMENT.** `str.replace` does not rescan what it
      has produced, so a password `abab` in the text `ababab` yields `<redacted>ab`. Half a secret
      is not a whole one, and re-scanning does not fix it (the remainder is no longer the secret).
    * **Over-redaction crosses channels.** The secrets come from ONE config, so an ntfy topic
      named `alerts` redacts that word out of the EMAIL channel's diagnostic too. Over-redaction
      is the safe direction and is deliberately not guarded against — a one-character password
      leaves an unreadable line, and unreadable is a bad log line where printed is an incident —
      but the cost lands on a line that had nothing to do with the secret.
    """
    try:
        secrets = _secret_values(cfg)
    except Exception:  # noqa: BLE001 — if the secrets cannot be enumerated, nothing is safe
        # The text CANNOT be shown, because the question "does it contain a secret" is now
        # unanswerable. Suppressing it costs a diagnostic; printing it may cost a credential.
        return "<suppressed: this config's secrets could not be enumerated for redaction>"
    # Longest first, so a secret that CONTAINS another (a URL and its own path) is replaced whole
    # rather than being broken into a redacted fragment plus a leftover tail of itself.
    for secret in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    return text


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
                   `http://` needs `allow_cleartext_ntfy=True`; userinfo is never accepted.
    `state_file`   where firing-condition state is persisted. `None` disables de-duplication
                   entirely — nothing is read, nothing is written, every call is delivered.
    `error_log`    path for the retrievable JSONL error log. `None` disables that sink.

    `title_prefix` is put in front of the title on the way OUT, on both channels and nowhere
                   else. `""` (the default) means none. `kw_common.alerting_env` sets it to
                   `[<env>][<service>] ` for a service on a SHARED ntfy topic and leaves it empty
                   for one with its own, because that is a property of the topic rather than of
                   the service.

    `allow_cleartext_ntfy` opts a deployment IN to an `http://` topic URL. Defaults to `False`,
                   which turns a cleartext topic into an unconfigured channel. See `ntfy_ready()`
                   for why a topic URL is treated as a credential rather than an address.

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
    # ⚠️ APPENDED, not filed next to `ntfy_url` where it belongs by subject. A dataclass field's
    # POSITION is part of the constructor signature, so inserting one mid-list silently rebinds
    # every positional argument after it in code this library does not get to see. Readability
    # inside this file is not worth that; the docstring above groups it where a reader looks.
    allow_cleartext_ntfy: bool = False
    # ⭐ APPENDED for the same reason as the field above — a dataclass field's POSITION is the
    # constructor signature — and DEFAULTED TO EMPTY so it is inert for every existing caller.
    #
    # What it is for: on a SHARED ntfy topic, nothing in a push notification says which service
    # is talking, so `kw_common.alerting_env` puts `[<env>][<service>] ` in front of the title. On
    # a service's OWN topic the topic is already the identifier and the prefix is noise, so it is
    # empty. The decision belongs to whoever knows the deployment; this module only carries it.
    #
    # ⚠️ DISPLAY ONLY. It reaches the email subject and the ntfy `Title` and NOTHING else — the
    # de-duplication state file, the error-log record and the process log line all keep the RAW
    # title. A condition's identity must not change when its deployment does, or promoting a
    # service from dev to prod re-fires every escalating condition it had already reported.
    title_prefix: str = ""

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
        # What each blank actually costs, so the refusal names the real consequence instead of
        # telling every field the state_file story.
        blank_costs = {
            "config_file": "the email channel is unconfigured and every email alert is skipped",
            "state_file": "de-duplication is OFF, so an escalating condition re-pages at its "
                          "caller's cadence instead of backing off",
            "error_log": "the retrievable sink is OFF, so WARN and ERROR alerts are in the "
                         "process log only",
        }
        for name, cost in blank_costs.items():
            value = getattr(self, name)
            if value is None:
                continue
            # `os.PathLike` accepted and coerced: `Path(...)` is the obvious thing to pass a
            # public library, and it used to work for `error_log`/`config_file` (which reach
            # `open()`) while breaking `state_file` (which reaches `.strip()`) — an asymmetry
            # that showed up as de-duplication silently off plus a traceback per notification.
            #
            # `__fspath__` is caller code and can misbehave: one that RAISES used to propagate
            # its own exception, and one returning `None` produced a bare `TypeError`. Both
            # escaped a caller catching `ValueError` around this constructor, which is the only
            # thing the refusals below invite them to catch.
            if isinstance(value, os.PathLike):
                try:
                    value = os.fspath(value)
                except Exception as exc:  # __fspath__ is the caller's code and can misbehave
                    raise ValueError(
                        f"AlertSettings.{name} is an os.PathLike whose __fspath__ failed "
                        f"({type(exc).__name__})") from exc
                object.__setattr__(self, name, value)
            if not isinstance(value, str):
                raise ValueError(
                    f"AlertSettings.{name} must be a path, or None to disable it — "
                    f"got {type(value).__name__}")
            if not value.strip():
                raise ValueError(
                    f"AlertSettings.{name} is blank, which would mean: {cost}. A blank string is "
                    f"almost always an unset environment variable reaching this constructor by "
                    f"accident, so it is refused rather than quietly honoured. Pass None to "
                    f"disable it deliberately.")

        for name in ("error_log_max_bytes", "error_log_backups", "max_record_field",
                     "max_tracked_conditions"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"AlertSettings.{name} must be a positive integer, got {value!r}")

        # A REAL bool, not a truthy value. This one field decides whether a topic capability is
        # allowed to cross the wire in the clear, and the way it gets set wrong is the same way
        # every blank path above gets set wrong: straight out of a container Variable. The string
        # `"false"` is TRUE to Python, so a deployment that spelled its opt-out correctly in the
        # template would have silently opted IN. Refuse the string and make the operator convert.
        # A `str` and nothing else. `None` is the obvious thing to reach for when a caller means
        # "no prefix", and it would reach the two send paths as the literal text `None` in front
        # of every alert title — visible, wrong, and only in production.
        if not isinstance(self.title_prefix, str):
            raise ValueError(
                f"AlertSettings.title_prefix must be a string ('' for none), got "
                f"{type(self.title_prefix).__name__}")
        # ⭐ AND NOT A CONTROL CHARACTER, because this string becomes an HTTP header value (ntfy's
        # `Title`) and a mail header (the `Subject`). Both reject a CR or LF outright, so a prefix
        # carrying one does not corrupt a header — it kills EVERY channel, on every notification,
        # while the boot report still says both are ready. Refusing `None` and a non-string while
        # accepting a value that guarantees total alerting failure is the wrong place to stop.
        if any(ch in self.title_prefix for ch in "\r\n") or any(
                ord(ch) < 32 or ord(ch) == 127 for ch in self.title_prefix):
            raise ValueError(
                "AlertSettings.title_prefix contains a control character. It is used as an HTTP "
                "header value and a mail Subject, both of which refuse one — so this would not "
                "corrupt a header, it would fail every alert on every channel.")

        if not isinstance(self.allow_cleartext_ntfy, bool):
            raise ValueError(
                "AlertSettings.allow_cleartext_ntfy must be True or False, got "
                f"{type(self.allow_cleartext_ntfy).__name__} — note that a non-empty string such "
                f"as 'false' is TRUE to Python, so it is refused rather than honoured backwards")


def _is_bare_mailbox(value: str) -> bool:
    """Whether `value` is a plain `local@domain` an SMTP relay could authenticate as.

    Deliberately narrow, because its only job is to decide whether `EMAIL_FROM` may stand in for
    an absent `SMTP_USER`. It answers NO for every RFC 5322 form that is valid in a header and
    useless as a credential: a display name (`Alerts <box@host>`), a non-ASCII display name, a
    group, a comment, or more than one address. Being wrong in the permissive direction is what
    makes a dead channel report itself ready, so anything it does not recognise is a NO.
    """
    value = value.strip()
    if not value or value.count("@") != 1:
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    # ⛔ A CONTROL CHARACTER IS A NO, and this line is what makes the docstring's "anything it does
    # not recognise is a NO" true rather than aspirational. `.strip()` removes only WHITESPACE, so
    # an embedded NUL or SOH survived every check below and was copied into `SMTP_USER` — leaving
    # `email_ready()` reporting True for a channel that cannot authenticate, which is precisely
    # the direction this predicate exists to close.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    # `<`/`>` are the display-name form; whitespace, `,` and `;` mean more than a mailbox;
    # `(`/`)` are a comment. Any of them and this is a header value, not a credential.
    return not any(ch in value for ch in '<>,;()"\\ \t')


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
    return _parse_env_text(raw)


def _parse_env_text(raw: str) -> dict[str, str]:
    """The parse itself, over text that has already been read.

    ⭐⭐ SPLIT OUT SO THERE IS EXACTLY ONE PARSER, WITH TWO ERROR POLICIES ABOVE IT.
    `_parse_env_file` reads fail-SOFT: a missing or mis-encoded file means the email channel is
    unconfigured, which is the right answer for a notification already in flight.
    `kw_common.alerting_env.read_config` reads fail-LOUD, because at BOOT "the file is empty" and
    "the file is UTF-16" must not look alike. Two policies, one parse — a second parser would be a
    second answer to the same question, and boot validation would then be able to accept a file
    the send path reads differently.

    This function is imported by `alerting_env` despite the leading underscore, deliberately: it
    is a seam inside one package, not a public surface, and exporting it would make the parse
    format a semver contract it is not.
    """
    values: dict[str, str] = {}
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
    # Appended for the same reason `AlertSettings.allow_cleartext_ntfy` is: a field's position is
    # the constructor signature. `AlertConfig(url, email, path)` still means what it meant.
    allow_cleartext_ntfy: bool = False

    def __repr__(self) -> str:
        """The dataclass repr with `SMTP_PASSWORD` shown as `<set>`/`<unset>`, never its value.

        ⭐ A GENERATED REPR PRINTS EVERY FIELD, AND ONE OF THEM HOLDS THE SHARED FILE'S PASSWORD.
        `Alerter.config()` returns this object to any adopter, and `repr(alerter.config())` in a
        debug line or a boot-time settings dump was the password in the process log — the same
        class of escape as the marker's digest (#20), through a different door. The fleet's
        boot-config-dump standard prints credentials as set/unset; this does the same, and every
        other field stays readable because they are what an operator needs to see.
        """
        email = None if self.email is None else {
            key: (("<set>" if value else "<unset>") if key == "SMTP_PASSWORD" else value)
            for key, value in self.email.items()}
        return (f"AlertConfig(ntfy_url={self.ntfy_url!r}, email={email!r}, "
                f"config_file={self.config_file!r}, "
                f"allow_cleartext_ntfy={self.allow_cleartext_ntfy!r})")

    @classmethod
    def load(cls, settings: AlertSettings) -> AlertConfig:
        """Read the current config: ntfy from the settings, email from the settings' file.

        The FILE is read fresh on every call rather than cached, so rotating the password or
        moving the inbox in it takes effect WITHOUT restarting the service. The cost is one small
        file read per notification.
        """
        path = _usable_path(settings.config_file)
        parsed: dict[str, str] = {}
        if path is not None:
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
        email = {k: parsed.get(k, "").strip() for k in EMAIL_KEYS}
        # ⭐ `SMTP_USER` IS OPTIONAL AND DEFAULTS TO `EMAIL_FROM`. They are the same value in this
        # fleet today and they are NOT the same thing: `SMTP_USER` is the AUTH IDENTITY the relay
        # checks, `EMAIL_FROM` is the header the recipient reads. They diverge on a verified alias
        # and on a relay whose user is literally `apikey`, so the key stays — it just is not
        # something an operator should have to type twice.
        #
        # ⚠️ APPLIED HERE, NOT IN THE LOADER, because this file is re-read on EVERY notification
        # so that rotating the password takes effect without a restart. A default injected once at
        # boot would be discarded by the first send.
        #
        # ⚠️ AND IT PRESERVES "NOTHING IS CONFIGURED". An absent or empty file leaves `EMAIL_FROM`
        # blank, so `SMTP_USER` stays blank too and `email_ready()` still reports the channel
        # unconfigured rather than misconfigured — the distinction its own comment calls
        # load-bearing.
        #
        # ⛔⛔ AND ONLY WHEN `EMAIL_FROM` IS A BARE MAILBOX. This is the half the first version got
        # wrong, and it got it wrong in the worst available direction: `EMAIL_FROM` is a mail
        # HEADER and may legitimately read `Alerts <box@host>` or carry a non-ASCII display name,
        # while `SMTP_USER` is an AUTH IDENTITY that must be a plain ASCII mailbox. Copying the
        # first into the second turned an honest "email alerts DISABLED — SMTP_USER missing" into
        # `email_ready() == True` for a channel that then failed 100% of sends — the "dead while
        # looking configured" failure this module's docstrings call its worst — and the eventual
        # refusal told the operator to re-set a key they had deliberately left out.
        #
        # So the default is a CONVENIENCE for the case it was asked for (the two are the same
        # value), and it declines the moment they cannot be. A deployment whose From header
        # carries a display name genuinely does need `SMTP_USER`, and the existing diagnostic
        # already says so.
        if not email["SMTP_USER"] and _is_bare_mailbox(email["EMAIL_FROM"]):
            email["SMTP_USER"] = email["EMAIL_FROM"]
        return cls(ntfy_url=settings.ntfy_url or "",
                   email=email,
                   config_file=path,
                   allow_cleartext_ntfy=_opted_into_cleartext(settings))

    # --- readiness (blank gating) ---------------------------------------------------------
    def ntfy_ready(self) -> bool:
        """True only for a full topic URL this module is willing to send to.

        Five things are refused, each of them loudly and each leaving the boot warning to see the
        channel as unset rather than letting it fail per-alert forever:

        * **whitespace or a control character** — survives `urlsplit`, raises out of
          `http.client` on every send, and the message quotes the path;
        * **a bare topic** (no scheme/host) — `unknown url type: '<topic>'` on every send, so the
          channel is dead while looking configured;
        * **userinfo** (`user:password@host`) — never worked, and its failure printed the
          password;
        * **a non-ASCII topic path** — `http.client` ASCII-encodes the request line, and its
          `UnicodeEncodeError` prints the offending character plus an index into the topic;
        * **`http://`**, unless `AlertSettings(allow_cleartext_ntfy=True)` — a topic URL is a
          write capability.

        ⭐⭐ THE HOST IS JUDGED AS THE SEND PATH WILL SEE IT, NOT AS IT IS WRITTEN, and that is the
        difference between this check working and merely appearing to. `urlsplit` returns the RAW
        netloc; `urllib.request.Request` then does `unquote(self.host)` before `http.client` gets
        it. So the two disagree on every percent-escape, and a URL written
        `https://user:pw%40host/topic` has NO `@` for a raw check to see while arriving at
        `http.client` as `user:pw@host` — the identical `InvalidURL: nonnumeric port: 'pw@host'`,
        printing the password, on every alert, with the boot report saying "ntfy ready".
        Measured; two independent verification passes found it the same way. `%20` and `%0d`
        walked past the control-character check for the same reason.

        Enumerating the escapes would be an arms race. Asking the SEND PATH'S OWN PARSER what the
        host will be is not: `Request(url).host` is the exact expression `_post_ntfy`'s request
        uses, so the two cannot drift.

        NOTHING here echoes the URL. It is a capability, so every branch names the shape of the
        problem and stops there.
        """
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
        if parts.scheme not in ("http", "https") or not parts.netloc:
            log.error("the ntfy URL must be the FULL topic URL (https://<host>/<topic>), not a "
                      "bare topic — ntfy alerts are DISABLED until it is fixed")
            return False
        if not parts.path.isascii() or not parts.query.isascii():
            # ⭐ THE SMTP CREDENTIAL REFUSAL'S MISSING SIBLING. `http.client` encodes the request
            # line as ASCII, so one non-ASCII character in the topic raises
            # `UnicodeEncodeError: ... can't encode character '\xf6' in position 14` — the
            # character, plus an index into the topic, which is a capability. It is rendered as an
            # escaped `repr`, so `_redact` structurally cannot reach it, exactly as for the SMTP
            # password. `_send_email` refuses its own; this refuses ntfy's, and the asymmetry
            # between them was the defect.
            #
            # The HOST is not covered HERE — it is covered below, by the latin-1 check, which is
            # the constraint that actually binds on this path. (An earlier version of this
            # comment claimed `http.client` IDNA-encodes the host so a non-ASCII hostname works.
            # Measured: it does not, because `urllib` pre-adds the `Host` header and the IDNA
            # branch is skipped.)
            log.error("the ntfy URL's topic contains a non-ASCII character, which http.client "
                      "cannot put in a request line and whose failure would print it — ntfy "
                      "alerts are DISABLED until it is fixed. Percent-encode the topic.")
            return False
        try:
            # THE SEND PATH'S OWN PARSE. See the docstring: `urlsplit` reads the raw netloc and
            # this reads the `unquote`d one, which is what `http.client` is handed.
            # Suppressed below because nothing is OPENED here: this constructs the request object
            # purely to read how its parser resolves the host, which is the whole point.
            host = urllib.request.Request(self.ntfy_url).host or ""  # noqa: S310
        except Exception as exc:  # noqa: BLE001 — a readiness check must not raise; see below
            # Same reasoning as the `urlsplit` guard above, and the same discipline: the class
            # name only. `Request` raises `ValueError` for a URL it cannot type, and its message
            # quotes the URL.
            #
            # ⚠️ BELIEVED UNREACHABLE, and said out loud rather than left to imply coverage: once
            # the scheme/netloc check above has passed, no input was found that makes `Request`
            # raise (~20 candidates tried during verification — odd ports, `%ff` hosts, `[v1...]`,
            # a bare `%`, IDNA forms). It is kept because `Request` is third-party code on the
            # alerting path and a readiness check that throws takes the OTHER channel's
            # notification with it. It is the one refusal branch with no test, for want of an
            # input that reaches it.
            log.error("the ntfy URL could not be parsed the way the send path parses it (%s) — "
                      "ntfy alerts are DISABLED until it is fixed", type(exc).__name__)
            return False
        if "@" in host or _UNSAFE_IN_URL.search(host):
            # ⭐ USERINFO IS REFUSED, AND IT COSTS NOTHING TO REFUSE. `urllib` puts the whole
            # netloc — userinfo included — into the Host it hands `http.client`. A
            # `user:password@host` URL never worked: it failed on every send, and the failure log
            # printed the password. `user@host` (no colon) fails differently — it resolves
            # nowhere — but it is equally dead while looking configured, so it gets the same
            # answer: refused ONCE at readiness, visible in the boot report.
            #
            # `_UNSAFE_IN_URL` is applied to the DECODED host as well as to the raw URL above,
            # because `%0d`/`%20` are invisible to the raw check and arrive at `http.client` as a
            # carriage return and a space.
            #
            # The message names the SHAPE, never the value: this is a capability.
            log.error("the ntfy URL carries userinfo (user:password@host) or a character that "
                      "cannot appear in a host, which urllib cannot send and whose failure would "
                      "print it — ntfy alerts are DISABLED until it is fixed. Put an ntfy token "
                      "in a header-bearing proxy, not in the URL.")
            return False
        try:
            # ⭐⭐ THE SEND PATH'S OWN VALIDATOR, NOT A PREDICATE THAT IMITATES IT — and the
            # difference is a second, deeper bypass of exactly the same defect.
            #
            # Checking `"@" in host` looked like the userinfo rule, but `http.client` has never
            # looked for an `@`: `_get_hostport` splits on the LAST COLON and raises
            # `InvalidURL: nonnumeric port: '<the rest>'`. The `@` was incidental; the COLON is
            # the trigger. So every one of these reported READY and then printed its own
            # configuration on every alert, forever:
            #
            #     https://tok:hunter2%2540host/t   host is unquoted ONCE, so %2540 -> %40, not @
            #     https://tok:hunter2/t            no `@` anywhere at all
            #     https://host:abc/t               an ordinary port typo
            #
            # Imitating a rule is an arms race; ASKING is not. Constructing the connection object
            # runs `_get_hostport` and `_validate_host` — the exact code the send path runs —
            # and OPENS NOTHING (no socket exists until `.connect()`).
            #
            # ⚠️ ONE SHAPE IS STILL ADMITTED ON SOME INTERPRETERS, scoped here rather than
            # claimed closed. A BRACKETED-IPv6 host carrying colon-bearing userinfo —
            # `https://tok:secret[::1]/topic` — is refused only because `urlsplit` raises
            # `Invalid IPv6 URL`, and that comes from `urllib.parse._check_bracketed_netloc`, a
            # CPython hardening shipped in a PATCH release. Measured: present on 3.10.20 and
            # 3.14.7, ABSENT on 3.12.0 — and `requires-python = ">=3.10"` admits both. Where it
            # is absent, such a URL reports READY and is dead on every send.
            #
            # Left alone deliberately. The CREDENTIAL half does not occur: the send fails with
            # `getaddrinfo failed`, which names neither the host nor the userinfo (measured on
            # 3.12.0), so the containment property holds on every interpreter in range. Only the
            # "dead while looking configured" half gets through, for a shape an operator has to
            # construct on purpose. Closing it would mean a FOURTH host-shape predicate, and
            # predicates are precisely what this check has already been wrong about twice.
            #
            # The latin-1 check is the other half, and it corrects a claim this file made and had
            # wrong: a non-ASCII host is NOT IDNA-encoded on this path. `urllib` pre-adds the
            # `Host` header, so `putrequest`'s IDNA branch is skipped (`skip_host=1`) and
            # `putheader` encodes latin-1 — measured. A host outside latin-1 is therefore READY
            # and permanently dead, and its `UnicodeEncodeError` quotes the character, which is
            # the leak shape the topic check above exists to stop.
            host.encode("latin-1")
            http.client.HTTPConnection(host)
        except Exception as exc:  # noqa: BLE001 — a readiness check must never raise
            # Class name only. `InvalidURL`'s message QUOTES the offending part of the host,
            # which is the credential this whole branch exists to keep out of the log.
            log.error("the ntfy URL's host is not one http.client can send to (%s) — ntfy alerts "
                      "are DISABLED until it is fixed. Check for a stray ':' (a non-numeric port "
                      "is the usual cause) or a character outside latin-1.",
                      type(exc).__name__)
            return False
        if parts.scheme == "http" and not self.allow_cleartext_ntfy:
            # ⭐ A TOPIC URL IS A WRITE CAPABILITY, NOT AN ADDRESS. Anyone who observes it can
            # page the operator from then on, and over cleartext the URL, the Title (which is
            # where operators put the identifying half of the message) and the body all cross the
            # wire in the clear. The exposure is permanent in a way an intercepted alert is not.
            #
            # Secure by DEFAULT, with a supported opt-in, rather than refused outright: a
            # self-hosted ntfy on a trusted network is a real deployment, and a library that
            # simply turns such a channel off leaves the operator with no path except a fork.
            # `AlertSettings(allow_cleartext_ntfy=True)` is that path.
            log.error("the ntfy URL is http:// — a topic URL is a write capability, and over "
                      "cleartext it and every alert Title are readable by anything on the path. "
                      "ntfy alerts are DISABLED. Use https://, or pass "
                      "AlertSettings(allow_cleartext_ntfy=True) to accept the exposure "
                      "deliberately on a trusted network.")
            return False
        return True

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
    # ⭐ REFUSED HERE, BEFORE smtplib IS HANDED THE CREDENTIAL — because smtplib's own refusal
    # PRINTS PART OF IT. `SMTP.auth` does `("\0%s\0%s" % (user, password)).encode("ascii")`, so a
    # single non-ASCII character in either value raises
    #     UnicodeEncodeError: 'ascii' codec can't encode character '\xe4' in position 6
    # — the character itself, plus an offset that converts directly to an index into the password
    # once the (unsecret) username's length is known. That message then reached the failure log on
    # every alert, forever.
    #
    # Redaction cannot catch this one: the character is rendered as an escaped `repr`, not as the
    # substring it came from, so `_redact` has nothing to match. The only containment that works
    # is to never let the value reach the code that quotes it. Same answer, same reason, as
    # `_fault_shape` gives for `SMTP_PORT`: name the setting and its length, never its value.
    for key in ("SMTP_USER", "SMTP_PASSWORD"):
        value = email.get(key, "")
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            # `from None`: chaining would hang the original exception — the one that quotes the
            # character — off `__cause__`, where any handler rendering a traceback prints it. The
            # point of raising our own is that the original never travels.
            raise ValueError(
                f"{key} contains a non-ASCII character, which SMTP AUTH cannot carry: "
                f"{_fault_shape(value)} is REJECTED and this alert is not being emailed. "
                f"Re-set it to an ASCII value.") from None
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


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that refuses every redirect.

    ⭐ A PUBLISH ENDPOINT HAS NO BUSINESS REDIRECTING, and following one hands the alert to a host
    the operator never configured. `urlopen` re-issues the request AT THE NEW LOCATION WITH THE
    HEADERS INTACT — including `Title`, which is `[SEV] <title>` and is where an operator puts the
    identifying half of the message. (`_post_ntfy` builds it from the severity's own prefix and
    the title it was handed. The SERVICE NAME has never been in it; on a shared topic
    `AlertSettings.title_prefix` puts `[<env>][<service>] ` at the front of the title itself, which
    is a property of the topic rather than of this header.) The target need not share the original
    host or even the scheme, so an `https` topic can be handed to a cleartext one by a single
    `302` from a compromised or merely misconfigured endpoint. `ntfy_ready()` validates the URL
    the OPERATOR chose; it has nothing to say about where that server then points.

    Returning `None` here makes `HTTPRedirectHandler.http_error_3xx` decline, so the `3xx` falls
    through to `HTTPDefaultErrorHandler` and is raised as an ordinary `HTTPError` — i.e. it
    becomes a visible channel failure, which is the correct reading of an ntfy endpoint that has
    started redirecting.
    """

    def redirect_request(self, req: object, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        return None


# ⭐ BUILT LAZILY, ON FIRST SEND — NOT AT IMPORT, and the difference is measurable.
# `build_opener()` constructs a `ProxyHandler()`, which SNAPSHOTS `getproxies()` at the moment it
# is built. `urlopen` builds its default opener on first use, so a consumer that resolves its
# proxy environment in `main()` — after `import kw_common.alerting` — was still proxied. Built at
# import, this opener captured the environment as it was BEFORE that, and such a consumer lost
# ntfy proxying entirely: a behaviour change nobody asked for, in a patch release. Matching
# `urlopen`'s timing means the ONLY thing this opener changes is the redirect handling.
#
# The race is benign and is the same one `urlopen` has: two threads may each build one, and the
# loser's is simply discarded.
_NTFY_OPENER: urllib.request.OpenerDirector | None = None


def _ntfy_opener() -> urllib.request.OpenerDirector:
    """The opener `_post_ntfy` sends through: `urlopen`'s handler set with the redirect handler
    replaced. `build_opener` DROPS a default class when a passed handler subclasses it, which is
    what makes `_RefuseRedirects` a substitution rather than an addition — two redirect handlers
    would leave the stock one free to follow."""
    global _NTFY_OPENER
    if _NTFY_OPENER is None:
        _NTFY_OPENER = urllib.request.build_opener(_RefuseRedirects)
    return _NTFY_OPENER


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
    #
    # `_ntfy_opener()`, never `urlopen`: that opener is the one that refuses redirects. `urlopen`
    # uses a DIFFERENT, shared opener that follows them.
    #
    # CLOSED, not just read — but honestly scoped, because the first version of this comment
    # claimed a measurement it did not have. `http.client` DOES close the connection itself once
    # a body with a known `Content-Length` is fully consumed by `.read()`, and dropping the `with`
    # was measured NOT to reproduce a `ResourceWarning`. (The warning that prompted this came from
    # the `HTTPError` a refused redirect raises, which is also a response holding a socket — the
    # TEST closes that one.) The `with` stays because the auto-close depends on the server
    # sending a length and on the read completing, neither of which this code controls; it is
    # correct defensive practice, not a fix for a reproduced leak.
    with _ntfy_opener().open(req, timeout=NTFY_TIMEOUT_S) as response:
        response.read()


_CHANNELS: tuple[tuple[str, Callable[[AlertConfig, SeveritySpec, str, str], None]], ...] = (
    ("email", _send_email),
    ("ntfy", _post_ntfy),
)


# --- error-log helpers (pure; the Alerter supplies the paths) ----------------------------------
def _restrict(path: str, mode: int) -> None:
    """Narrow an EXISTING path's permissions. Silent on success; WARNS when it cannot.

    The `mode=` arguments to `os.makedirs` and `os.open` apply only when the thing is CREATED —
    `open(2)` ignores the mode for an existing file, and `makedirs(exist_ok=True)` never chmods.
    So on any upgrade path they achieve nothing: a build that wrote this file 0644, or a
    directory an operator made over SMB, stays wide open forever while the docs claim otherwise.

    ⭐⭐ THE FAILURE IS REPORTED, AND IT USED TO BE `except OSError: pass`. On a bind mount whose
    uid does not match the container's user — the ordinary case for host-managed appdata — `chmod`
    raises `EPERM` and the swallowed exception made a no-op indistinguishable from success. The
    caller, the docstrings and the standard all then claimed a file was 0600 that was 0644, which
    is worse than not narrowing it at all: a hardening nobody can rely on is one nobody checks.
    The first adopter hit the identical shape in its own code on the same volume and escalated it
    from `debug` to a warning for this reason.

    ⚠️ IT WARNS RATHER THAN RAISING, deliberately. Every call site is inside `_append_error_record`,
    whose contract is that recording an alert never raises — a `chmod` that fails must not cost the
    record it was trying to protect. So the failure is loud and the alert still lands. The cost is
    one warning per failed call on a service that alerts often; that is proportional to alerts
    rather than to time, and it stops the moment the mount is fixed.

    A path that does not exist is NOT a failure and stays silent: this function narrows what is
    already there, and every caller creates the thing separately.
    """
    try:
        if os.path.exists(path):
            os.chmod(path, mode)
    except OSError as exc:
        # A filesystem that does not model these bits, or a file owned by someone else. Named
        # rather than swallowed: `errno` is the difference between "this volume cannot do
        # permissions" (EPERM/EOPNOTSUPP) and "this path is wrong" (ENOENT), and an operator can
        # act on the first two immediately.
        log.warning("alerting: could not restrict %s to %s (%s) — it keeps whatever permissions "
                    "it already had, so treat it as readable by anything with this volume "
                    "mounted. On a bind mount this is usually a uid mismatch between the host "
                    "directory and the user this process runs as.",
                    path, oct(mode), errno.errorcode.get(exc.errno or 0, exc.errno))


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
                    backups: int | None = ERROR_LOG_BACKUPS) -> list[dict]:
    """The most recent records from a rotating jsonl file, oldest-first. Never raises on I/O.

    Reads the rotated generations oldest → newest and then the live file, so a `since`/`limit`
    window can span a rotation. `since_iso` filters on each record's `ts` by comparing parsed
    instants (see `parse_since`); `keep(record)` is an optional extra predicate. Returns at most
    the newest `limit` records — `limit<=0` means no cap.

    `backups` MUST match what wrote the file, or the reader silently loses the oldest generation.
    `Alerter.read_errors()` passes the settings' value and is the call to prefer. `None` is the
    sentinel the reference implementation used for "the module default", and it is accepted again
    (#5): a caller ported from that code passes it explicitly, and the extraction had turned that
    call into a `TypeError` at the generation count — a behavioural difference for exactly the
    call site an adopter moves across first.

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
    generations = max(1, ERROR_LOG_BACKUPS if backups is None else backups)
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
def _title_prefix(settings: object) -> str:
    """The outbound title prefix for these settings, or `""`. NEVER raises.

    ⭐⭐ DEFENSIVE ON PURPOSE, AND THE REASON IS ALREADY WRITTEN DOWN ONE FUNCTION BELOW:
    "an `Alerter` takes any settings-shaped object and validates nothing". A stand-in without this
    attribute, a `dataclass` predating it, or a `property` that raises are all real inputs — and
    reading the attribute straight in `_dispatch` made every one of them cost the WHOLE
    notification. Measured: three existing tests went from `"sent"` to `"failed"`, with the
    outer guard reporting `AttributeError` and no channel attempted.

    The prefix is COSMETIC. Nothing about it may cost a delivery, so anything that is not a plain
    string becomes no prefix at all.
    """
    try:
        value = getattr(settings, "title_prefix", "")
    except Exception:  # noqa: BLE001 — a property on caller-supplied settings can raise anything
        return ""
    return value if isinstance(value, str) else ""


# The characters that end a path in "a directory, not a file". `altsep` is `None` off Windows, so
# a backslash stays an ordinary filename character there, where it is one.
_SEPARATORS = tuple(sep for sep in (os.sep, os.altsep) if sep)


def _sink_problem(path: str, *, label: str, creates_directories: bool,
                  appends_in_place: bool) -> str:
    """Why a file this module will WRITE cannot be, or `""` — asked the way the writer will ask.

    ⭐⭐ THE QUESTION IS "CAN THE SINK BE CREATED", NOT "DOES ITS DIRECTORY EXIST" (#23). The
    previous detectors asked the second question and answered "fine" for five states in which the
    write provably fails — a file (or a symlink to nowhere) standing where the directory should
    be, a trailing separator, the sink being itself a directory, a NUL in the path — because
    `os.path.isdir` is False for all of them and the parent arm then found a perfectly writable
    grandparent. Each of those is a different way of asking the same wrong question, and adding a
    fifth special case would have left a sixth. So this walks to the FIRST ANCESTOR THAT EXISTS
    and asks about that, which is what `os.makedirs` and `os.open` are about to do.

    ⚠️ AND THE OPPOSITE SIGN. The old "even its parent does not exist, so the volume is probably
    not mounted" arm reddened `<mount>/svc/logs/errors.log` with `<mount>` mounted and `svc/` not
    yet created — a configuration `makedirs` handles on the first alert — because its premise held
    only when the mounted root was exactly one level up. A detector that reddens a working
    configuration is one that gets switched off, so that arm now fires only when NOTHING on the
    way to the directory exists except the filesystem root itself, which no volume ever is.

    `creates_directories` — the error-log sink `makedirs` its directory; the state file does not
    (`_write_state` opens its temp file directly), so for it a missing directory is a problem
    outright and the wording says so. `appends_in_place` — the error log is opened for append, so
    an existing sink must be writable; the state file is replaced by rename, which does not need
    the old file to be.

    The three answers this distinguishes are still: checked and fine (`""`), checked and broken
    (a sentence), and could not check (the caller's `except`). The third is not the first.
    """
    if "\x00" in path:
        return (f"{label} contains a NUL character, which no filesystem call accepts — the sink "
                f"cannot be opened")
    if path.endswith(_SEPARATORS):
        return (f"{label} {path!r} ends with a path separator, so it names a directory rather "
                f"than a file, and nothing can be opened at it")
    full = os.path.abspath(path)
    directory = os.path.dirname(full)
    # Walk up to the first thing that exists. `lexists`, not `exists`: a symlink to nowhere is an
    # entry that `mkdir` refuses, so it is something in the way rather than an absence.
    probe, missing = directory, 0
    while not os.path.lexists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe, missing = parent, missing + 1
    if not os.path.lexists(probe) or (missing and os.path.dirname(probe) == probe):
        # Nothing on the way exists but the filesystem (or drive) root. On the containers this
        # library ships to that is a volume that was never mounted: `makedirs` would then succeed
        # inside the disposable layer, which is the retrieval failure this sink exists to fix,
        # arriving disguised as success.
        return (f"{label} points into {directory!r}, and nothing on the way to it exists except "
                f"the filesystem root — in a container that usually means the volume is not "
                f"mounted, so these records would be written into the disposable layer and "
                f"could not be read from the host")
    if not os.path.isdir(probe):
        return (f"{label} points into {directory!r}, but {probe!r} is not a directory — a file, "
                f"or a symlink to nowhere, is standing where the directory should be, and the "
                f"sink cannot be created through it")
    if missing:
        if not creates_directories:
            return f"{label} points into {directory!r}, which does not exist"
        if not os.access(probe, os.W_OK):
            return (f"{label} points into {directory!r}, which does not exist and cannot be "
                    f"created: its parent {probe!r} is not writable by this process. The "
                    f"sink will fail on the first WARN or ERROR alert — in a container this "
                    f"is usually a uid mismatch on a bind-mounted volume")
        return ""  # the fresh-container case: `makedirs` creates the rest on the first write
    if os.path.lexists(full):
        if os.path.isdir(full):
            return f"{label} {path!r} is a directory, not a file — nothing can be written to it"
        if appends_in_place and not os.access(full, os.W_OK):
            return f"{label} {path!r} exists but is not writable by this process"
        return ""
    if not os.access(directory, os.W_OK):
        return f"{label} points into {directory!r}, which is not writable"
    return ""


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
            # ⭐ THE BLANK BRANCH IS A BACKSTOP, AND DELETING IT WAS A MISTAKE WORTH RECORDING.
            # It was removed once, on the argument that `AlertSettings` now refuses a blank path
            # so this state is unreachable. That argument is FALSE, and measurably so: an
            # `Alerter` takes any settings-shaped object and validates nothing, a `@dataclass`
            # subclass can override `__post_init__`, `object.__setattr__` writes through the
            # frozen instance, and a `str` subclass whose `strip()` lies passes the public
            # constructor outright. With the branch gone, all four produced `""` — "no problem" —
            # while de-duplication was silently OFF. That is strictly worse than the misleading
            # message it replaced: a wrong diagnostic still says something is wrong.
            #
            # The constructor refusal is the PRIMARY guard and catches the common case at boot.
            # This is the second line, for the objects that never went through it.
            path = _usable_path(path)
            if path is None:
                return ("state_file is blank, so de-duplication is OFF — every notify() call is "
                        "delivered and an escalating condition re-pages at its caller's cadence "
                        "instead of backing off. This is almost always an unset environment "
                        "variable; pass None to disable de-duplication deliberately, or a path "
                        "to enable it")
            # Asked the way `_write_state` writes: no `makedirs`, and a rename over the leaf.
            return _sink_problem(path, label="state_file", creates_directories=False,
                                 appends_in_place=False)
        except Exception as exc:  # noqa: BLE001 — a diagnostic must not become the failure
            # Broad on purpose, and not narrowed to the one failure that came to mind: this runs
            # on the alerting path, called from notify(), so ANY escape takes down the alert it
            # was only annotating. Answer "unusable" instead.
            return f"state_file could not be checked ({type(exc).__name__})"

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
            # The blank backstop, for the same reason and by the same routes — see the note in
            # `state_file_problem`.
            path = _usable_path(path)
            if path is None:
                return ("error_log is blank, so the retrievable sink is OFF — WARN and ERROR "
                        "alerts are in the process log only. This is almost always an unset "
                        "environment variable; pass None to disable the sink deliberately")
            # Asked the way `_append_error_record` writes: `makedirs` the directory, then open
            # the leaf for append. `_sink_problem` walks to the first existing ancestor and asks
            # about THAT (#22, #23) — see its docstring for the states the old arms got wrong.
            return _sink_problem(path, label="error_log", creates_directories=True,
                                 appends_in_place=True)
        except Exception as exc:  # noqa: BLE001 — a diagnostic must not become the failure
            return f"error_log could not be checked ({type(exc).__name__})"

    def warn_if_unconfigured(self) -> list[str]:
        """Boot check: report which channels are live, and WARN loudly if none are.

        A service whose alerts go nowhere looks exactly like a service with nothing to report,
        which is how two services quietly stopped alerting. Returns the ready channels.

        Like `notify()`, this cannot raise: it runs at the top of the service's main loop, so an
        exception here is a boot crash — a restart loop over a mistyped config file.
        """
        try:
            # ⛔ INSIDE a guard, for exactly the reason `_append_error_record` resolves its path
            # inside one: `self.settings` is an attribute access, and an attribute access can run
            # arbitrary code. This was left unguarded when that sibling was fixed — one instance
            # repaired, the identical pattern one function over untouched, in the function whose
            # docstring above promises it cannot raise. A duck-typed or lazily-resolved settings
            # object made THIS the boot crash the paragraph warns about.
            service = self.settings.service
        except Exception as exc:  # noqa: BLE001 — a boot check must not be the boot crash
            log.warning("ALERTING: this service's own name could not be resolved from its "
                        "settings (%s) — reporting alerting readiness without it",
                        type(exc).__name__)
            service = "<unknown service>"
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
        path = _usable_path(self.settings.error_log)
        if path is None:
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
            path = _usable_path(self.settings.error_log)
            if path is None:
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

        ⭐ `str(path)`, MATCHING `state_file_problem()` EXACTLY. These two ask the same question —
        "is there a usable state file here?" — and they must not be able to answer it
        differently. They could: the diagnostic normalised with `str(path)` while this did a raw
        `path.strip()`, so a settings object carrying a `Path` (which only reaches here by
        bypassing `AlertSettings`, the same population the diagnostic's backstop exists for) made
        this raise `AttributeError`. The fail-open guard in `notify()` swallowed it, so
        de-duplication was silently OFF while the boot report said nothing was wrong — measured:
        two identical edge-triggered alerts both delivered.

        Same shape as the roller/reader generation-count invariant elsewhere in this module: two
        sides of one question, so they read it from one expression.
        """
        path = self.settings.state_file
        return _usable_path(path) is not None

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
            # ⭐ CREATED 0600 BY `os.open`, THE SAME WAY THE MARKER AND THE ERROR LOG ARE — not by
            # `open()` at the umask's mode, which is what this was. The file persists every
            # firing condition's TITLE, and an operator's titles are the identifying half of the
            # message; a state file written 0644 into a shared appdata volume was readable by
            # anything with that volume mounted. `os.replace` carries the temp's mode with it, so
            # an existing wide file from an earlier build is narrowed on its next write with no
            # `chmod` — the call that fails on a uid-mismatched mount (#21).
            #
            # REMOVED FIRST, THEN `O_EXCL`: a temp left behind by a killed process would
            # otherwise lend its own mode to the marker-style rename. And removed again on
            # failure, so a read-only mount does not accumulate a `.tmp` beside every boot.
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._prune(state), fh)
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001 — a read-only mount must not break alerting
            with contextlib.suppress(OSError):
                os.unlink(tmp)
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
        # ⭐ THE PREFIX IS APPLIED AT THE SEND BOUNDARY AND NOWHERE ELSE. Everything upstream (the
        # de-duplication key, the error record, the log line) has already used the raw title,
        # which is what keeps a condition's identity independent of the deployment it is running
        # in. See `AlertSettings.title_prefix`.
        prefix = _title_prefix(self.settings)
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
                # ⭐⭐ AND THE CONCATENATION IS INSIDE THE GUARD FOR EXACTLY THE SAME REASON, which
                # the comment above states and the first version of this line broke. An f-string
                # calls `format()` on the caller's title, and a caller CAN pass a non-string — the
                # error-record code anticipates "a caller passing a dict". Built once above the
                # loop, a title whose `__format__` raises took BOTH channels down and reported
                # `failed` for one that was never attempted. Measured.
                #
                # The `if prefix` is not an optimisation: with no prefix the raw title object is
                # passed through untouched, so the default configuration behaves exactly as it did
                # before this field existed.
                send(cfg, spec, f"{prefix}{title}" if prefix else title, message)
                results[name] = "sent"
            except Exception as exc:  # noqa: BLE001 — one dead channel must not silence the other
                results[name] = "failed"
                # ⭐ REDACTED, BOTH BRANCHES. The exception here was raised by third-party code
                # holding this module's configuration, and several stdlib exceptions quote the
                # value that upset them. This log line repeats on EVERY alert, in the log an
                # operator is most likely to paste into a bug report. See `_redact` for what that
                # backstop does and does not reach.
                detail = _redact(_safe_text(exc), cfg)
                if _is_cert_failure(exc):
                    # Louder than a transient outage on purpose: a certificate that does not
                    # verify is the signature of something sitting between this service and the
                    # server.
                    log.error("%s alert failed TLS VERIFICATION — the server's certificate did "
                              "not validate, which is what an intercepted connection looks "
                              "like: %s", name, detail)
                else:
                    log.warning("%s alert failed: %s: %s", name, type(exc).__name__, detail)
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
            #
            # ⭐ REDACTED TOO — the SIBLING of the two lines in `_dispatch`, and fixing those
            # while leaving this one would have been half a fix. Redacted against a config built
            # from the settings alone, because reaching here means the real load is what failed:
            # the ntfy URL is available (it is a plain setting) and the SMTP password is not (it
            # lives in the file that could not be read, so it is not in this message either).
            try:
                partial = AlertConfig(ntfy_url=str(getattr(self.settings, "ntfy_url", "") or ""))
            except Exception:  # noqa: BLE001 — the settings object is why we are already here
                partial = AlertConfig()
            log.error("alerting failed before any channel could be tried: %s: %s",
                      type(exc).__name__, _redact(_safe_text(exc), partial))
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
