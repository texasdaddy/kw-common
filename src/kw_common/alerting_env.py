"""The fleet's alerting CONVENTION, executable — the environment layer `alerting` refuses to be.

⭐⭐ WHY THIS IS A SEPARATE MODULE, AND WHY IT MUST STAY ONE.

`kw_common.alerting` is deliberately environment-free. v1.0.1 stripped five environment variables
and three default paths out of it, and that removal is what made it portable: it takes an
`AlertSettings` and assumes nothing about where the values came from. Putting file and environment
reading back into it would undo exactly that.

So the deployment knowledge lives HERE, in a sibling, and the split is the design:

    alerting        given settings, deliver a notification. Knows nothing about deployments.
    alerting_env    turn ONE shared file plus THREE variables into those settings.

An app imports both. An app that is not in this deployment — a consumer elsewhere, or Keystone
later reading its configuration out of a database — imports only the first, calls
`load_alert_settings(...)` with explicit arguments, or builds an `AlertSettings` by hand.

⭐ THE POINT OF THE PACKAGE: THE CONVENTION IS A FUNCTION SIGNATURE, NOT PROSE.

Five container templates currently declare five different shapes of alerting variable and not one
of them declares the variable the standard has specified since the alerting redesign. Every new
build re-invented its own configuration because the convention existed only as documentation, and
documentation is interpreted. A missing variable is now an ERROR AT BOOT rather than a silent
divergence discovered when an alert does not arrive.

⛔ NO OPERATOR PATH IS A DEFAULT HERE, IN CODE OR AS A FALLBACK. Not the shared root, not the
config directory, not the environment. All three arrive as values. A default path is one that
silently "works" in the exact deployment it is wrong for, and this library is installed by six
repositories on machines this file has never seen.

## The three variables

======================  =========  =================================================
variable                mode       purpose
======================  =========  =================================================
``SHARED_ROOT``         read-only  carries ``configs/alerting.env`` and nothing else
``CONFIG_PATH``         read-write the app's OWN directory; holds the boot marker
``DEPLOY_ENV``          --         ``prod`` or ``dev``, case-insensitive
======================  =========  =================================================

⚠️ `CONFIG_PATH` IS NOT THE CONFIG FILE. `load_alert_settings`'s first parameter is called
`config_path` and is the **alerting.env file**; the `CONFIG_PATH` variable is the app's own
**read-write directory** where the marker is written. Two different things wearing one name — the
signature is fixed by the standard, so every function here says which it means, and the marker
parameter is spelled `marker_dir` rather than repeating the ambiguity.

## The file

    EMAIL_TO EMAIL_FROM SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD
    NTFY_URL_DEV NTFY_URL_PROD NTFY_URL_<SERVICE>

`DEPLOY_ENV` selects between `NTFY_URL_DEV` and `NTFY_URL_PROD`. A per-service override
`NTFY_URL_<SERVICE>` wins when present — and the choice of key carries a second consequence that
is easy to miss:

⭐ THE TITLE PREFIX IS A PROPERTY OF THE TOPIC, NOT OF THE SERVICE. A service sharing the
environment topic with every other service gets `[<env>][<service>]` in front of its alert titles,
because on a shared topic nothing else says who is talking. A service with its OWN topic gets NO
prefix, because the topic is already the identifier and the prefix would be noise on every push
notification. Getting this backwards is invisible in a test that only checks the URL.

## Failure posture

Everything here is fail-LOUD, which is the opposite of `alerting`'s posture and is correct for
both. `alerting.notify()` never raises: an alerting failure must not take down the thing it is
reporting on. This module runs at BOOT, before anything is running, where a refusal is a startup
crash the operator sees rather than a service that comes up and quietly alerts nobody.

⛔ AND A VALIDATION FAILURE NEVER TRIES TO ALERT. It cannot report a broken alerting channel
*through* that channel; it logs and raises. `AlertEnvError` is the only exception this module
raises on purpose.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .alerting import (
    OK,
    Alerter,
    AlertSettings,
    _parse_env_text,
    current_alerter,
)

__all__ = [
    # failure
    "AlertEnvError",
    # the convention, as names rather than prose
    "SHARED_ROOT_VAR",
    "CONFIG_PATH_VAR",
    "DEPLOY_ENV_VAR",
    "CONFIG_RELPATH",
    "MARKER_NAME",
    "ENVIRONMENTS",
    # the manifest
    "EMAIL_REQUIRED_KEYS",
    "OPTIONAL_KEYS",
    "required_keys",
    # helpers a consumer and the setup document both need
    "normalise_env",
    "ntfy_key",
    "read_config",
    "config_file_for",
    # the two layers
    "load_alert_settings",
    "load_alert_settings_from_env",
    # boot
    "validate_boot",
    "validate_boot_from_env",
]

log = logging.getLogger("kw_common.alerting_env")


class AlertEnvError(RuntimeError):
    """Refuse to boot: the alerting configuration is absent, unreadable or incomplete.

    Its own type on purpose. An adopter's `main()` catches THIS to print a final line and exit
    non-zero; catching `RuntimeError` would swallow unrelated failures, and catching `Exception`
    around a boot sequence is how a broken deployment comes up looking healthy.
    """


# --- the convention, spelled once ---------------------------------------------------------------
# ⭐ THE VARIABLE NAMES ARE CONSTANTS, NOT STRING LITERALS SPRINKLED THROUGH THE CODE. A consumer
# writing its container template reads them from here (or from the setup document, which is
# generated from the same names), so a template and the code that reads it cannot disagree about
# spelling — which is precisely how five templates ended up declaring five different variables.
SHARED_ROOT_VAR = "SHARED_ROOT"
CONFIG_PATH_VAR = "CONFIG_PATH"
DEPLOY_ENV_VAR = "DEPLOY_ENV"

# The ONE path this module knows, and it is a RELATIVE one — the shared root it hangs off is
# always supplied. `configs/` and nothing else lives under the shared root.
CONFIG_RELPATH = "configs/alerting.env"

# Written into the app's own read-write `CONFIG_PATH`, NEVER into the read-only shared root. A
# dotfile so it does not clutter a directory an operator looks at, and empty because the only
# thing it carries is its modification time.
MARKER_NAME = ".alerting-validated"

ENVIRONMENTS = ("dev", "prod")

# ⭐⭐ THE REQUIRED-KEY MANIFEST LIVES HERE, NEXT TO THE LOADER, AND NOWHERE ELSE.
# Otherwise every app answers "what does prod require" independently, and they disagree the first
# time a key is added — which is the same failure as five templates with five variable shapes, one
# layer down.
#
# ⚠️ `SMTP_USER` IS ABSENT FROM THIS LIST ON PURPOSE and is listed as optional below. It defaults
# to `EMAIL_FROM`. They are the same value in this deployment today but they are different things:
# `SMTP_USER` is the AUTH IDENTITY and `EMAIL_FROM` is the HEADER. They diverge on a verified alias
# and on a relay whose user is literally `apikey`, so the key exists — it just is not required.
#
# ⚠️ `SMTP_PORT` IS REQUIRED even though `alerting.smtp_port()` has a documented default. That
# default exists so a RUNNING service degrades instead of dying mid-notification; boot is where
# being explicit costs nothing.
EMAIL_REQUIRED_KEYS = ("EMAIL_TO", "EMAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_PASSWORD")

OPTIONAL_KEYS = ("SMTP_USER", "NTFY_URL_<SERVICE>")


def required_keys(deploy_env: str) -> tuple[str, ...]:
    """Every key this environment's config file must carry with a non-blank value.

    ⭐ THE ENVIRONMENT TOPIC IS REQUIRED EVEN FOR A SERVICE THAT HAS ITS OWN. That looks strict
    from inside one service and is right from outside all of them: the file is FLEET-SHARED, so a
    file missing `NTFY_URL_PROD` is broken for every service that has no override — and the next
    service to adopt is exactly the one with no override. An incomplete shared file is a fleet
    fault, and the cheapest place to find it is the first boot that reads it.
    """
    env = normalise_env(deploy_env)
    return (*EMAIL_REQUIRED_KEYS, f"NTFY_URL_{env.upper()}")


# --- small, exact answers -----------------------------------------------------------------------
def normalise_env(value: object) -> str:
    """`DEPLOY_ENV` as this library uses it: stripped and lowercased, or a refusal.

    ⭐ NORMALISED HERE, INSIDE THE LIBRARY, so `Prod`, `PROD` and `prod` cannot behave differently
    across apps. Six repositories each lowercasing at their own call site is six chances to forget
    one, and the symptom of forgetting is not an error — it is a service that looks up
    `NTFY_URL_PROD` in a file whose key is `NTFY_URL_PROD` and finds it, right up until one app
    spells it `Prod` and silently lands on no topic at all.
    """
    if not isinstance(value, str):
        raise AlertEnvError(
            f"{DEPLOY_ENV_VAR} must be a string naming one of {', '.join(ENVIRONMENTS)} — "
            f"got {type(value).__name__}")
    env = value.strip().lower()
    if env not in ENVIRONMENTS:
        raise AlertEnvError(
            f"{DEPLOY_ENV_VAR}={value!r} is not one of {', '.join(ENVIRONMENTS)}. It is "
            f"case-insensitive, so 'Prod' and 'PROD' are fine; anything else is refused rather "
            f"than guessed, because guessing selects the WRONG ntfy topic and nothing says so.")
    return env


# Everything that is not a letter or a digit collapses to a single `_`. Service names in this
# fleet are hyphenated (`reauth-bot`, `cef-tracker`) and a hyphen cannot appear in an environment
# key, so SOME transform is unavoidable — and an unavoidable transform that is not written down is
# where two repositories silently disagree.
_NON_KEY_CHARS = re.compile(r"[^A-Za-z0-9]+")


def ntfy_key(service: str) -> str:
    """The per-service override key for `service`: `NTFY_URL_` + its name, upper-cased.

    ⚠️ ONE RULE, NO CANDIDATES. `reauth-bot` is `NTFY_URL_REAUTH_BOT` — not `NTFY_URL_REAUTH`, not
    both. Accepting several spellings would make the file's meaning depend on which one an
    operator happened to type, and a service reading the wrong one lands on the SHARED topic with
    a prefix it should not have: a change nobody sees until an alert arrives looking different.

    The setup document and `alerting.env.template` print this rule; `config_file_for` and the
    loader are the only things that apply it.
    """
    if not isinstance(service, str) or not service.strip():
        raise AlertEnvError("service must be a non-blank name")
    stem = _NON_KEY_CHARS.sub("_", service.strip()).strip("_").upper()
    if not stem:
        raise AlertEnvError(
            f"service={service!r} has no letters or digits, so it cannot name a config key")
    return f"NTFY_URL_{stem}"


def config_file_for(shared_root: str | os.PathLike[str]) -> Path:
    """`<shared_root>/configs/alerting.env`, as a `Path`. No default for `shared_root`."""
    return Path(shared_root) / Path(CONFIG_RELPATH)


def read_config(config_path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse the shared alerting file, or raise `AlertEnvError` saying why it could not be.

    ⭐⭐ THE PARSER IS `alerting`'s OWN, IMPORTED PRIVATELY AND ON PURPOSE. `alerting` re-reads
    this file on EVERY notification, so if this module parsed it even slightly differently — one
    module honouring `export `, or quotes, or a CRLF line ending, and the other not — boot
    validation would pass a file the send path then read as something else. A second parser is a
    second answer to the same question. There is exactly one, and this is the seam.

    ⚠️ THE ERROR POLICY IS THE OPPOSITE ONE, AND THAT IS THE WHOLE REASON THIS WRAPPER EXISTS.
    `alerting._parse_env_file` returns `{}` for a missing or unreadable file, which is right for a
    notification in flight — the email channel is simply unconfigured and ntfy still goes. At BOOT
    that same silence is the failure: "the file is empty" and "the file is UTF-16" must not look
    alike. So the read happens here, with the errors surfaced.

    `ValueError`, NOT ONLY `OSError` — the trap the standard names explicitly. A hand-edited file
    saved as cp1252 or UTF-16 raises `UnicodeDecodeError`, which is a `ValueError` and is not an
    `OSError`; a boot check that catches only `OSError` lets one bad byte through as a traceback.
    """
    path = Path(config_path)
    try:
        # `newline=""` matches `alerting._parse_env_file` exactly: universal-newline translation
        # rewrites a lone `\r` before the parser can see it, which silently truncates a value
        # rather than rejecting it. The two readers must make the same choice or the parse they
        # share is being fed different text.
        with open(path, encoding="utf-8", newline="") as fh:
            raw = fh.read()
    except FileNotFoundError as exc:
        raise AlertEnvError(
            f"the shared alerting config {path} does not exist. It is the file every service in "
            f"the fleet reads; see the setup document for how to create it.") from exc
    except OSError as exc:
        raise AlertEnvError(
            f"the shared alerting config {path} could not be read ({type(exc).__name__}: {exc}). "
            f"If the mount is read-only that is expected and fine — this is a READ.") from exc
    except ValueError as exc:
        # UnicodeDecodeError lands here. Named in the message because "not UTF-8" is a thing an
        # operator can act on immediately and "ValueError" is not.
        raise AlertEnvError(
            f"the shared alerting config {path} is not UTF-8 text ({type(exc).__name__}). A file "
            f"saved as cp1252 or UTF-16 by a desktop editor reads like this; save it as UTF-8.",
        ) from exc
    return _parse_env_text(raw)


# --- layer one: pure -----------------------------------------------------------------------------
def load_alert_settings(config_path: str | os.PathLike[str], deploy_env: str,
                        service: str) -> AlertSettings:
    """Build `AlertSettings` from an explicit config file, environment and service name.

    Pure in the sense that matters: it reads NO environment variable and has NO default path.
    Everything it needs is an argument, which is what makes it usable outside this deployment and
    testable without touching `os.environ`.

    ⚠️ `config_path` IS THE alerting.env FILE, not the `CONFIG_PATH` variable (which is the app's
    own read-write directory — see the module docstring; `validate_boot` calls that one
    `marker_dir`).

    What it decides, in order:

    * the environment, normalised — `Prod`, `PROD` and `prod` all produce identical settings;
    * the ntfy topic — the per-service override if the file carries one, else this environment's
      shared topic;
    * the title prefix — EMPTY when the override won, `[<env>][<service>] ` when it did not,
      because the prefix belongs to the topic and not to the service.

    What it deliberately does NOT decide: whether the file is COMPLETE. A missing key leaves the
    corresponding channel unconfigured, which `alerting.warn_if_unconfigured` reports and
    `validate_boot` refuses to boot on. Two responsibilities, two functions — a loader that also
    validated would have to be bypassed by any caller that wanted one without the other.

    ⭐ THE SIZING AND PATH SETTINGS ARE NOT ARGUMENTS HERE, AND THAT IS DELIBERATE. `state_file`
    and `error_log` are the app's OWN paths — they live under its read-write volume, not in the
    fleet's shared file — so this loader has nothing to say about them. An adopter that wants them
    writes one stdlib call:

        from dataclasses import replace
        settings = replace(load_alert_settings_from_env("my-service"),
                           state_file="/data/alerts.json",
                           error_log="/data/logs/my-service-errors.log")
    """
    env = normalise_env(deploy_env)
    key = ntfy_key(service)          # validates `service` before anything else is read
    values = read_config(config_path)

    override = values.get(key, "").strip()
    if override:
        ntfy_url = override
        title_prefix = ""
        log.info("alerting: %s has its own ntfy topic (%s); alert titles carry no prefix",
                 service, key)
    else:
        shared_key = f"NTFY_URL_{env.upper()}"
        ntfy_url = values.get(shared_key, "").strip()
        # ⭐ THE PREFIX IS DECIDED BY WHICH KEY WON, NOT BY WHETHER A URL WAS FOUND. A shared
        # topic that is missing from the file is still the shared topic: the service is
        # unconfigured, not suddenly the owner of a private one. Deciding on the URL's presence
        # would silently drop the prefix for exactly the deployment that is already broken.
        title_prefix = f"[{env}][{service}] "
        log.info("alerting: %s uses the shared %s topic (%s); alert titles carry the prefix %r",
                 service, env, shared_key, title_prefix)
        # A BLANK override is treated as ABSENT above, deliberately: a container platform passes
        # every unset optional Variable as an empty string, and an operator clearing a key means
        # "stop using my own topic", not "use no topic at all".

    return AlertSettings(
        service=service,
        # The FILE, not its contents. `alerting` re-reads it on every notification so that
        # rotating the SMTP password or moving the inbox takes effect without a restart — a
        # property that would be thrown away by parsing the email block into the settings here.
        config_file=str(Path(config_path)),
        ntfy_url=ntfy_url,
        title_prefix=title_prefix,
    )


# --- layer two: the convention -------------------------------------------------------------------
def _require_env(name: str) -> str:
    """One environment variable, or a refusal naming what it is for.

    ⛔ NO DEFAULT, EVER. A default here is the divergence this package exists to remove: the app
    boots, alerts land on a topic nobody is watching, and the first anyone knows is an incident
    that produced no page. A missing variable is an error at boot.
    """
    value = os.environ.get(name)
    if value is None:
        raise AlertEnvError(
            f"{name} is not set. Every app in this fleet declares {SHARED_ROOT_VAR}, "
            f"{CONFIG_PATH_VAR} and {DEPLOY_ENV_VAR}; there is no default for any of them, "
            f"because a default would point at the wrong deployment silently.")
    if not value.strip():
        # A container platform passes an unset Variable as an EMPTY STRING, so blank is the
        # common way this arrives — and treating blank as "set" is how a template with the
        # variable declared but never filled in reads as configured.
        raise AlertEnvError(
            f"{name} is set to an empty value, which is what an unfilled container Variable "
            f"looks like. Give it a value or remove it; blank is not a default.")
    return value.strip()


def load_alert_settings_from_env(service: str) -> AlertSettings:
    """The fleet convention, executable: read `SHARED_ROOT` and `DEPLOY_ENV`, build the settings.

    This is the call every app in this deployment makes. `load_alert_settings` exists for anything
    genuinely different — a consumer outside this deployment, or a service that later reads its
    configuration from a database.

    Raises `AlertEnvError` if either variable is unset or blank. It does not fall back, and it
    does not guess an environment.
    """
    shared_root = _require_env(SHARED_ROOT_VAR)
    deploy_env = _require_env(DEPLOY_ENV_VAR)
    return load_alert_settings(config_file_for(shared_root), deploy_env, service)


# --- boot validation -----------------------------------------------------------------------------
def _marker_is_fresh(marker: Path, config: Path) -> bool:
    """Whether the marker post-dates the config file, i.e. this boot may skip validation.

    ⭐ STRICTLY NEWER. Equal timestamps validate AGAIN. Filesystem timestamp granularity is coarse
    — one second on some filesystems — so "the config was edited in the same tick the marker was
    written" is a real ordering, and the safe reading of it is to re-check. Skipping is an
    optimisation; validating is the point.
    """
    try:
        return marker.stat().st_mtime > config.stat().st_mtime
    except OSError:
        # Either file may be absent or unreadable. Both mean "cannot establish that the marker is
        # newer", and the answer to that is to validate.
        return False


def validate_boot(settings: AlertSettings, deploy_env: str,
                  marker_dir: str | os.PathLike[str], *,
                  shared_root: str | os.PathLike[str] | None = None,
                  alerter: Alerter | None = None) -> bool:
    """Verify the alerting configuration at startup. Refuse to boot if it is not usable.

    Returns `True` if this boot validated (and therefore alerted and wrote the marker), `False` if
    it skipped because the marker is newer than the config file. Raises `AlertEnvError` on any
    failure — the caller's `main()` is expected to let that stop the process.

    `marker_dir` is the app's OWN read-write directory: the `CONFIG_PATH` variable. The marker is
    never written into the shared root, which is mounted read-only and is not this app's to write.

    `shared_root`, when given, is checked to be a directory — that is the "is the mount actually
    there" question, and it is worth asking separately because a missing MOUNT and a missing FILE
    send an operator to different places.

    `alerter` is the `Alerter` the success notification goes through; `None` uses the process
    default installed by `alerting.configure()`.

    ⛔ A FAILURE NEVER NOTIFIES. Not once, not "best effort". Every path below that raises has
    logged first and sent nothing: the channel being validated is the one that would carry the
    report, so an attempt to alert about it either goes nowhere or, worse, appears to succeed.
    """
    env = normalise_env(deploy_env)
    config = Path(settings.config_file) if settings.config_file else None
    if config is None:
        raise AlertEnvError(
            "these settings carry no config_file, so there is nothing to validate. Build them "
            "with load_alert_settings() or load_alert_settings_from_env().")

    marker = Path(marker_dir) / MARKER_NAME
    if _marker_is_fresh(marker, config):
        log.info("alerting: configuration validated on an earlier boot (%s is newer than %s); "
                 "skipping. Touch or edit the config file to force a re-check.", marker, config)
        return False

    _check_layout(config, shared_root)
    values = read_config(config)          # raises AlertEnvError, having named the reason
    _check_required(values, env, config)

    # ⭐ ALERT FIRST, MARKER SECOND, in that order. A crash between the two re-validates and
    # re-alerts on the next boot; the reverse order would mark a boot validated whose alert never
    # left, which is silence — the one failure this whole standard exists to prevent.
    _announce(settings, env, values, config, alerter)
    _write_marker(marker)
    return True


def validate_boot_from_env(settings: AlertSettings, *,
                           alerter: Alerter | None = None) -> bool:
    """`validate_boot` reading `DEPLOY_ENV`, `CONFIG_PATH` and `SHARED_ROOT` from the environment.

    The same reason `load_alert_settings_from_env` exists: if the app reads those three variables
    itself in order to call `validate_boot`, the divergence this package removes reopens at the
    validation call site — one app spelling the marker directory variable differently is a marker
    written somewhere nothing looks for it, and validation that runs on every boot instead of once.
    """
    deploy_env = _require_env(DEPLOY_ENV_VAR)
    marker_dir = _require_env(CONFIG_PATH_VAR)
    shared_root = _require_env(SHARED_ROOT_VAR)
    return validate_boot(settings, deploy_env, marker_dir,
                         shared_root=shared_root, alerter=alerter)


def _check_layout(config: Path, shared_root: str | os.PathLike[str] | None) -> None:
    """The mount and the structure, asked as separate questions from "is the file readable"."""
    if shared_root is not None:
        root = Path(shared_root)
        if not root.is_dir():
            raise AlertEnvError(
                f"the shared root {root} is not a directory. In a container this is a MOUNT: it "
                f"reads like this when the volume was never added to the template, or was added "
                f"with a host path that does not exist.")
    parent = config.parent
    if not parent.is_dir():
        raise AlertEnvError(
            f"{parent} does not exist, so the shared root is mounted but its structure is not "
            f"there. The shared root must contain {CONFIG_RELPATH!r} — see the setup document.")


def _check_required(values: dict[str, str], env: str, config: Path) -> None:
    """Every key this environment requires carries a non-blank value."""
    missing = [k for k in required_keys(env) if not values.get(k, "").strip()]
    if missing:
        # ⭐ NAMES ONLY, NEVER VALUES. One of these keys is the SMTP password, and this message is
        # the line an operator pastes into a bug report.
        raise AlertEnvError(
            f"{config} is missing or blank for: {', '.join(missing)}. Those are the keys "
            f"{env!r} requires; {', '.join(OPTIONAL_KEYS)} are optional. Refusing to boot rather "
            f"than starting a service whose alerts go nowhere.")


def _announce(settings: AlertSettings, env: str, values: dict[str, str], config: Path,
              alerter: Alerter | None) -> None:
    """The one-time validation alert. Best effort by construction — `notify` never raises."""
    target = alerter or current_alerter()
    if target is None:
        # Not a failure: an app may validate before it configures, or in a test. Say so loudly
        # enough that "no alert arrived" is explicable, and carry on — the CONFIG is valid, which
        # is what was asked.
        log.warning("alerting: configuration validated, but no Alerter is installed, so the "
                    "one-time validation alert was NOT sent. Call alerting.configure(settings) "
                    "before validate_boot(), or pass alerter=.")
        return
    dedicated = bool(values.get(ntfy_key(settings.service), "").strip())
    # ⛔ NO VALUES IN THE BODY. This message goes to email and to a push topic; the file it is
    # describing holds an SMTP password. It says which KEYS were used, never what they hold.
    target.notify(
        OK,
        "Alerting configuration validated",
        f"{settings.service} started in {env} and its alerting configuration checks out: "
        f"{config.name} carries every key {env} requires, and this service "
        f"is on {'its own ntfy topic' if dedicated else f'the shared {env} topic'}. "
        f"This message is the proof that the channel works; it is sent once per configuration "
        f"change, not on every boot.",
    )


def _write_marker(marker: Path) -> None:
    """Write the empty marker into the app's own read-write directory.

    ⚠️ A FAILURE HERE IS NOT A BOOT FAILURE. The configuration IS valid — that has already been
    established and announced. An unwritable `CONFIG_PATH` costs one redundant validation and one
    duplicate alert per boot, which is noise; refusing to start over it would turn a
    missing-read-write-volume into an outage of the service itself.
    """
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"")
    except OSError as exc:
        log.warning("alerting: could not write the validation marker %s (%s) — the configuration "
                    "is valid and was announced, but every boot will re-validate and re-announce "
                    "until this directory is writable. It is the CONFIG_PATH variable.",
                    marker, type(exc).__name__)
