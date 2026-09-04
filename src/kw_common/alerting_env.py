"""The fleet's alerting CONVENTION, executable — the environment layer `alerting` refuses to be.

⭐⭐ WHY THIS IS A SEPARATE MODULE, AND WHY IT MUST STAY ONE.

`kw_common.alerting` is deliberately environment-free. v1.0.1 stripped five environment variables
and three default paths out of it, and that removal is what made it portable: it takes an
`AlertSettings` and assumes nothing about where the values came from. Putting file and environment
reading back into it would undo exactly that.

So the deployment knowledge lives HERE, in a sibling, and the split is the design:

    alerting        given settings, deliver a notification. Knows nothing about deployments.
    alerting_env    turn ONE shared file plus THREE variables into those settings.

An app imports both. An app that is not in this deployment — a consumer elsewhere, or a service
that later reads its configuration out of a database — imports only the first, calls
`load_alert_settings(...)` with explicit arguments, or builds an `AlertSettings` by hand.

⭐ THE POINT OF THE PACKAGE: THE CONVENTION IS A FUNCTION SIGNATURE, NOT PROSE.

Container templates in this deployment each declared a different shape of alerting variable, and
not one declared the variable the standard has specified since the alerting redesign. Every new
build re-invented its own configuration because the convention existed only as documentation, and
documentation is interpreted. A missing variable is now an ERROR AT BOOT rather than a silent
divergence discovered when an alert does not arrive.

⛔ NO OPERATOR PATH IS A DEFAULT HERE, IN CODE OR AS A FALLBACK. Not the shared root, not the
config directory, not the environment. All three arrive as values. A default path is one that
silently "works" in the exact deployment it is wrong for, and this library is installed on
machines this file has never seen.

## The three variables

======================  =========  =================================================
variable                mode       purpose
======================  =========  =================================================
``SHARED_ROOT``         read-only  carries ``configs/alerting.env`` and nothing else
``CONFIG_PATH``         read-write the app's OWN directory; holds the validation marker
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

import hashlib
import logging
import os
import re
from pathlib import Path

from .alerting import (
    OK,
    AlertConfig,
    Alerter,
    AlertSettings,
    _parse_env_text,
    current_alerter,
    smtp_port_fault,
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
    "marker_name",
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
# spelling — which is precisely how a set of templates ended up declaring different variables.
SHARED_ROOT_VAR = "SHARED_ROOT"
CONFIG_PATH_VAR = "CONFIG_PATH"
DEPLOY_ENV_VAR = "DEPLOY_ENV"

# The ONE path this module knows, and it is a RELATIVE one — the shared root it hangs off is
# always supplied. `configs/` and nothing else lives under the shared root.
CONFIG_RELPATH = "configs/alerting.env"

# Written into the app's own read-write `CONFIG_PATH`, NEVER into the read-only shared root. A
# dotfile so it does not clutter a directory an operator looks at.
#
# ⭐ THE ACTUAL FILENAME IS `.alerting-validated-<env>` — see `marker_name`. One marker for all
# environments was measured wrong: a dev→prod promotion does not touch the fleet-shared file, so
# the single marker still said "validated" and the prod topic was never checked.
#
# ⭐ ITS CONTENT IS ONE LINE, `sha256:<hex>`, being the digest of the config file that was
# validated. It used to be EMPTY, with the whole meaning carried by its modification time — and a
# timestamp cannot answer "did the contents change", so a config restored by `rsync -a` / `cp -p`
# / `tar -x` / a volume restore booted clean on a file nobody had checked (#15).
MARKER_NAME = ".alerting-validated"

ENVIRONMENTS = ("dev", "prod")

# ⭐⭐ THE REQUIRED-KEY MANIFEST LIVES HERE, NEXT TO THE LOADER, AND NOWHERE ELSE.
# Otherwise every app answers "what does prod require" independently, and they disagree the first
# time a key is added — which is the same failure as templates with differing variable shapes,
# one layer down.
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
    across apps. Every consumer lowercasing at its own call site is one more chance to forget
    one, and the symptom of forgetting is not an error — it is a service that looks up
    `NTFY_URL_PROD` in a file whose key is `NTFY_URL_PROD` and finds it, right up until one app
    spells it `Prod` and silently lands on no topic at all.
    """
    if not isinstance(value, str):
        raise _refuse(
            f"{DEPLOY_ENV_VAR} must be a string naming one of {', '.join(ENVIRONMENTS)} — "
            f"got {type(value).__name__}")
    env = value.strip().lower()
    if env not in ENVIRONMENTS:
        raise _refuse(
            f"{DEPLOY_ENV_VAR}={value!r} is not one of {', '.join(ENVIRONMENTS)}. It is "
            f"case-insensitive, so 'Prod' and 'PROD' are fine; anything else is refused rather "
            f"than guessed, because guessing selects the WRONG ntfy topic and nothing says so.")
    return env


# A RUN of characters that are not a letter or a digit collapses to a SINGLE `_`, and leading and
# trailing ones are dropped. Service names are commonly hyphenated and a hyphen cannot appear in an
# environment key, so SOME transform is unavoidable — and an unavoidable transform that is not
# written down is where two repositories silently disagree.
_NON_KEY_CHARS = re.compile(r"[^A-Za-z0-9]+")

# The keys the shared file reserves for the ENVIRONMENT topics. A service whose name derives one of
# these would read a shared topic as though it were its own private one.
_RESERVED_KEYS = frozenset(f"NTFY_URL_{env.upper()}" for env in ENVIRONMENTS)


def ntfy_key(service: str) -> str:
    """The per-service override key for `service`: `NTFY_URL_` + its name, upper-cased.

    ⚠️ ONE RULE, NO CANDIDATES. `feed-poller` is `NTFY_URL_FEED_POLLER` — not `NTFY_URL_FEED`, not
    both. Accepting several spellings would make the file's meaning depend on which one an
    operator happened to type, and a service reading the wrong one lands on the SHARED topic with
    a prefix it should not have: a change nobody sees until an alert arrives looking different.

    Precisely: a RUN of characters that are not ASCII letters or digits becomes ONE underscore and
    the ends are trimmed, so `feed--poller` is `NTFY_URL_FEED_POLLER` rather than `FEED__POLLER`,
    and `poller-` is `NTFY_URL_POLLER`.

    ⚠️ ASCII, and the word is load-bearing. A letter with an accent is NOT a letter here:
    `café-poller` derives `NTFY_URL_CAF_POLLER`, not `CAFE_` or `CAFÉ_`. Stated because it is
    surprising, and because guessing it wrong lands the service back on the shared topic
    SILENTLY — the whole failure this rule exists to prevent. A name with no ASCII letters or
    digits at all is refused outright rather than reduced to nothing.

    ⛔ AND A SERVICE MAY NOT DERIVE A RESERVED ENVIRONMENT KEY. A service literally named `dev` or
    `prod` would otherwise read `NTFY_URL_DEV`/`NTFY_URL_PROD` — keys the fleet-shared file always
    carries — as its own dedicated override: the wrong topic, no title prefix, and a validation
    alert announcing it is "on its own ntfy topic". Refused at the only moment anyone can act on it.

    The setup document and `alerting.env.template` print this rule; the loader is the only thing
    that applies it.
    """
    if not isinstance(service, str) or not service.strip():
        raise _refuse("service must be a non-blank name")
    # ⛔ REFUSED HERE, WHERE THE CALLER IS ALREADY CATCHING `AlertEnvError`. `.strip()` removes only
    # LEADING AND TRAILING whitespace, so an INTERIOR control character survived into the title
    # prefix and was then refused by `AlertSettings.__post_init__` with a bare `ValueError` — which
    # walks straight through an adopter's `except AlertEnvError`, the one thing the setup document
    # tells them to catch. Same refusal, this module's own exception type.
    #
    # ⚠️ ASKED OF THE STRIPPED VALUE, and the first attempt asked it of the raw one — which refused
    # a name with a TRAILING newline that `.strip()` handles perfectly well. A false refusal of a
    # configuration that was fine is the worse error of the two, and the suite caught it.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in service.strip()):
        raise _refuse(
            f"service={service!r} contains a control character. It would be carried into the "
            f"alert title prefix, and from there into an HTTP header and a mail Subject — a "
            f"newline or a carriage return is refused outright by both, and the rest travel as "
            f"junk in every alert you receive. A service name is not the place for one.")
    stem = _NON_KEY_CHARS.sub("_", service.strip()).strip("_").upper()
    if not stem:
        raise _refuse(
            f"service={service!r} has no ASCII letters or digits, so it cannot name a config key")
    key = f"NTFY_URL_{stem}"
    if key in _RESERVED_KEYS:
        raise _refuse(
            f"service={service!r} derives {key}, which is the shared {stem.lower()} environment "
            f"topic rather than a per-service override. A service cannot be named after an "
            f"environment: it would read the shared topic as its own and lose its title prefix. "
            f"Rename the service.")
    return key


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
        raise _refuse(
            f"the shared alerting config {path} does not exist. It is the file every service in "
            f"the fleet reads; see the setup document for how to create it.") from exc
    except OSError as exc:
        raise _refuse(
            f"the shared alerting config {path} could not be read ({type(exc).__name__}: {exc}). "
            f"If the mount is read-only that is expected and fine — this is a READ.") from exc
    except UnicodeDecodeError as exc:
        # Named in the message because "not UTF-8" is a thing an operator can act on immediately
        # and "ValueError" is not.
        raise _refuse(
            f"the shared alerting config {path} is not UTF-8 text ({type(exc).__name__}). A file "
            f"saved as cp1252 or UTF-16 by a desktop editor reads like this; save it as UTF-8.",
        ) from exc
    except ValueError as exc:
        # ⚠️ CAUGHT SEPARATELY FROM THE DECODE ERROR, AND THE DIAGNOSIS IS THE WHOLE POINT.
        # `UnicodeDecodeError` IS a `ValueError`, so one combined branch reported an embedded NUL
        # in the PATH — which `open()` rejects with a plain `ValueError` — as "the file is not
        # UTF-8 text". The right refusal, pointing the wrong way: an operator would go looking at
        # the file's encoding for a fault in the variable that named it.
        raise _refuse(
            f"the shared alerting config path {path!r} is not a usable path "
            f"({type(exc).__name__}: {exc}). Check the {SHARED_ROOT_VAR} value — this is a fault "
            f"in the path itself, not in the file's contents.") from exc
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
    # ⭐ NORMALISED ONCE, HERE, AND USED EVERYWHERE BELOW. `AlertSettings` strips the service name
    # in its own constructor, so building the prefix from the RAW argument produced two different
    # spellings of the same service — and the prefix is the one that reaches an HTTP header and a
    # mail Subject, so a name carrying a newline killed every channel while the settings object
    # looked clean. Two normalisations of one value, disagreeing, is the shape this library keeps
    # having to fix; one is the fix.
    service = service.strip()
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
        raise _refuse(
            f"{name} is not set. Every app in this fleet declares {SHARED_ROOT_VAR}, "
            f"{CONFIG_PATH_VAR} and {DEPLOY_ENV_VAR}; there is no default for any of them, "
            f"because a default would point at the wrong deployment silently.")
    if not value.strip():
        # A container platform passes an unset Variable as an EMPTY STRING, so blank is the
        # common way this arrives — and treating blank as "set" is how a template with the
        # variable declared but never filled in reads as configured.
        raise _refuse(
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
def marker_name(deploy_env: str) -> str:
    """The marker filename for `deploy_env` — `.alerting-validated-<env>`.

    ⭐⭐ PER ENVIRONMENT, AND THAT IS A FIX RATHER THAN A FLOURISH. With ONE marker for every
    environment, promoting a service dev -> prod — a routine operation that does not touch the
    fleet-shared file — left the marker already saying "validated", so validation NEVER RAN in the
    new environment. `NTFY_URL_PROD` was never checked; the service came up with an empty topic
    URL and did not refuse. Measured exactly that way, against the mtime rule this predates the
    digest of; the digest does not fix it, because a promotion does not change the file either.

    The environment is what the check is ABOUT — `required_keys` differs per environment and so
    does the topic — so "this configuration has been validated" is not a fact about the file alone.
    The marker lives in `CONFIG_PATH`, one per environment, exactly as the standard specifies; it
    constrains neither the filename nor the count.
    """
    return f"{MARKER_NAME}-{normalise_env(deploy_env)}"


def _config_digest(config: str | os.PathLike[str]) -> str:
    """`sha256:<hex>` for the config file's RAW BYTES, or `""` if it cannot be read.

    ⚠️ BYTES, NOT PARSED TEXT, and not decoded text either. A file re-saved in another encoding
    parses to the same keys and is a different file — cp1252 is the encoding this module refuses
    at boot, so a silent re-encode is precisely a change worth re-validating. Hashing the bytes
    also means the digest can be taken before anything has decided the file is readable.

    `""` on failure rather than a raise: the caller's question is "may this boot skip validation",
    and the answer to "I could not read the config" is no. Whatever went wrong is then reported
    properly by `_check_layout` / `read_config`, which say WHICH thing was wrong.
    """
    try:
        return "sha256:" + hashlib.sha256(Path(config).read_bytes()).hexdigest()
    except OSError:
        return ""


def _marker_matches(marker: Path, config: Path) -> bool:
    """Whether this exact config file has already been validated, i.e. this boot may skip.

    ⭐⭐ THE DIGEST, NOT THE TIMESTAMP (#15). The marker used to be empty and the question used to
    be "is the marker NEWER than the config". `rsync -a`, `cp -p`, `tar -x` and a volume restore
    all PRESERVE mtime, so a config restored at an older timestamp left the marker still newer: a
    broken configuration booted clean, announced nothing, and the service came up alerting nobody.
    Measured exactly that way. A timestamp cannot answer "did the contents change"; a digest can,
    and it answers it whatever the clock says.

    Three things fall out of the change, all of them wanted:

      * the ORDER of the two writes stops mattering, so the whole coarse-filesystem problem goes
        away — a one-second-granularity ext4 made the old marker unable to outrank the config at
        all, and every boot re-alerted forever. That fix (`_outrank`) is deleted, not kept.
      * the one-second window it left — a config edited in the second AFTER a boot was dated no
        later than the marker and got skipped — closes too. Any edit changes the digest.
      * a marker written by 1.2.0 is EMPTY, so it matches nothing and the first boot after the
        upgrade validates once, ANNOUNCES ONCE, and rewrites it. That is the correct migration and
        it needs no operator step — but it is not silent, and an operator upgrading a fleet should
        expect one confirmation per service rather than none.

    Anything unreadable, absent, empty or malformed answers False: the safe reading of "I cannot
    establish that this file was validated" is to validate it.
    """
    try:
        recorded = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return False
    if not recorded.startswith("sha256:"):
        return False
    current = _config_digest(config)
    # ⚠️ `and` on a non-empty current, because `_config_digest` answers `""` for an unreadable
    # config — and `"" == ""` would make an unreadable config match an unreadable marker. It
    # cannot happen through the branch above, which already required the `sha256:` prefix, and it
    # is written out anyway: this is the comparison that decides whether validation runs at all.
    return bool(current) and recorded == current


def validate_boot(settings: AlertSettings, deploy_env: str,
                  marker_dir: str | os.PathLike[str], *,
                  shared_root: str | os.PathLike[str] | None = None,
                  alerter: Alerter | None = None) -> bool:
    """Verify the alerting configuration at startup. Refuse to boot if it is not usable.

    Returns `True` if this boot validated (and therefore alerted and wrote the marker), `False` if
    it skipped because the marker records the digest of this exact config file. Raises
    `AlertEnvError` on any failure — the caller's `main()` is expected to let that stop the
    process.

    `marker_dir` is the directory the marker is written to, and it must be the app's OWN
    read-write directory — the `CONFIG_PATH` variable. ⚠️ That is a REQUIREMENT ON THE CALLER, not
    something this function can enforce: point it at the shared root and it will try to write
    there, and the shared root is mounted read-only precisely so that fails.

    `shared_root`, when given, is checked to be a directory — that is the "is the mount actually
    there" question, and it is worth asking separately because a missing MOUNT and a missing FILE
    send an operator to different places.

    `alerter` is the `Alerter` the success notification goes through; `None` uses the process
    default installed by `alerting.configure()`. With NO alerter available the marker is NOT
    written, so the confirmation is not lost — see `_announce`.

    ⛔ A FAILURE NEVER NOTIFIES. Not once, not "best effort". Every path below that raises has
    logged first and sent nothing: the channel being validated is the one that would carry the
    report, so an attempt to alert about it either goes nowhere or, worse, appears to succeed.

    ⚠️ WHAT THE MARKER CANNOT SEE, stated because a check that implies coverage it lacks is worse
    than none. It records the digest of the CONFIG FILE, so any change to that file re-validates,
    whatever the file's timestamp says (#15). What it does not cover is a change OUTSIDE the file:
    the ntfy endpoint going away, the SMTP password being revoked at the provider, DNS moving. A
    boot check answers "is this configuration well-formed and sendable-looking", never "is the
    remote end still there" — the periodic self-test is what answers that one.
    """
    env = normalise_env(deploy_env)
    config = Path(settings.config_file) if settings.config_file else None
    if config is None:
        raise _refuse(
            "these settings carry no config_file, so there is nothing to validate. Build them "
            "with load_alert_settings() or load_alert_settings_from_env().")

    marker = Path(marker_dir) / marker_name(env)
    if _marker_matches(marker, config):
        log.info("alerting: %s configuration validated on an earlier boot (%s records the current "
                 "digest of %s); skipping. Any edit to the config file forces a re-check; delete "
                 "the marker to force one without editing it.",
                 env, marker, config)
        return False

    _check_layout(config, shared_root)
    # ⭐⭐ THE DIGEST IS TAKEN HERE — BEFORE ANYTHING READS THE FILE'S CONTENTS — AND CARRIED TO
    # THE MARKER. (`_check_layout` above it only stats directories, so it cannot see a rewrite;
    # what must not get in front of this line is `read_config` and everything after it.)
    #
    # Taking it at WRITE time instead was a defect with #15's own outcome. `_announce` is an SMTP
    # round-trip plus an HTTP POST, so the gap between validating and marking is SECONDS of wall
    # clock — and the file it spans is the FLEET-SHARED one, which a config push rewrites while
    # services restart. Recording the bytes on disk at the end of that window records a file
    # nobody checked as validated, and then no later boot re-checks it: a broken configuration
    # boots clean and the service alerts nobody. Measured exactly that way.
    #
    # The remaining window is between this line and `read_config` below, two adjacent reads, and
    # it fails in the SAFE direction: a file that changes in between is validated as its new
    # contents and marked as its old ones, so the next boot re-validates. Stated rather than
    # called atomic, because it is not.
    digest = _config_digest(config)
    values = read_config(config)          # raises AlertEnvError, having named the reason
    _check_required(values, env, config)
    _check_usable(settings, config)

    # ⭐ ALERT FIRST, MARKER SECOND, in that order. A crash between the two re-validates and
    # re-alerts on the next boot; the reverse order would mark a boot validated whose alert never
    # left, which is silence — the one failure this whole standard exists to prevent.
    if not _announce(settings, env, values, config, alerter):
        # ⛔ AND NO ALERTER IS THAT SAME FAILURE, so the marker is withheld. Writing it anyway
        # recorded the boot as validated with the confirmation never sent — and then no LATER boot
        # would send one either, because the marker suppresses them, so the proof was lost until
        # somebody edited the file. The next boot after `configure()` validates and announces.
        return True
    _write_marker(marker, digest)
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


def _refuse(message: str) -> AlertEnvError:
    """Log the refusal, then hand back the exception for the caller to `raise`.

    ⭐ THE LOG IS PART OF THE CONTRACT, NOT DECORATION. The standard says a failure "logs loudly
    and refuses to boot", and the README, the CHANGELOG and this module's own docstrings all say
    "logs and raises". Before this existed the raising half was true and the logging half was not:
    `read_config`, `_check_layout` and `_check_required` all raised having emitted NOTHING. An
    adopter that catches `AlertEnvError` to print its own one-liner then had no record of which
    check failed anywhere.

    Returned rather than raised so every call site still reads `raise _refuse(...)` — the control
    flow stays visible at the point it happens, and a function that raises from inside a helper is
    exactly the shape that makes a traceback point at the wrong line.
    """
    log.error("alerting: refusing to boot — %s", message)
    return AlertEnvError(message)


def _check_layout(config: Path, shared_root: str | os.PathLike[str] | None) -> None:
    """The mount and the structure, asked as separate questions from "is the file readable"."""
    if shared_root is not None:
        root = Path(shared_root)
        if not root.is_dir():
            raise _refuse(
                f"the shared root {root} is not a directory. In a container this is a MOUNT: it "
                f"reads like this when the volume was never added to the template, or was added "
                f"with a host path that does not exist.")
    parent = config.parent
    if not parent.is_dir():
        raise _refuse(
            f"{parent} does not exist, so the shared root is mounted but its structure is not "
            f"there. The shared root must contain {CONFIG_RELPATH!r} — see the setup document.")


# ⭐ THE ONE PLACEHOLDER MARKER, AND IT IS DELIBERATELY NOT `example.com`.
# `alerting.env.template` asserts that a file left as-is will refuse to boot, and before this the
# claim was FALSE: every placeholder is non-blank, so the untouched template validated, announced
# that the configuration "checks out", and wrote the marker — so no later boot re-validated
# either. The key it mattered most for is `SMTP_PASSWORD`, the one most likely to survive an edit
# pass.
#
# ⛔ REFUSING `example.com` INSTEAD WOULD BE THE OBVIOUS FIX AND IT IS THE WRONG ONE. The reserved
# documentation domains are this repository's APPROVED synthetic vocabulary — its leak guard's own
# must-pass corpus is built from them and every fixture in the suite uses them — so a check that
# refused them would refuse the very values the internal-info rules require. One unmistakable
# marker, spelled in the template, is the version that cannot collide.
_PLACEHOLDER = "CHANGE-ME"


def _is_placeholder(value: str) -> bool:
    """Whether this value is still the template's marker rather than a setting.

    ⚠️ THE BOUNDARY IS A HYPHEN, NOT A BARE PREFIX. The template spells several of its markers
    `CHANGE-ME-to-your-...`, so an exact match is not enough — but a bare `startswith` FALSE-
    REFUSED a real value that merely begins the same way (`CHANGE-MEMORABLE` as a password was
    measured refused). A boot check that refuses a CORRECT configuration is worse than one that is
    slightly permissive: it gets switched off, and net safety goes down.
    """
    text = value.strip().upper()
    return text == _PLACEHOLDER or text.startswith(_PLACEHOLDER + "-")


def _check_required(values: dict[str, str], env: str, config: Path) -> None:
    """Every key this environment requires carries a real value — not blank, not a placeholder."""
    required = required_keys(env)
    missing = [k for k in required if not values.get(k, "").strip()]
    if missing:
        # ⭐ NAMES ONLY, NEVER VALUES. One of these keys is the SMTP password, and this message is
        # the line an operator pastes into a bug report.
        raise _refuse(
            f"{config} is missing or blank for: {', '.join(missing)}. Those are the keys "
            f"{env!r} requires; {', '.join(OPTIONAL_KEYS)} are optional. Refusing to boot rather "
            f"than starting a service whose alerts go nowhere.")
    unfilled = [k for k in required if _is_placeholder(values.get(k, ""))]
    if unfilled:
        raise _refuse(
            f"{config} still carries the {_PLACEHOLDER} placeholder for: {', '.join(unfilled)}. "
            f"That is the template as it ships, not a configuration — fill those in.")


def _check_usable(settings: AlertSettings, config: Path) -> None:
    """Every configured channel is one `alerting` can actually send on.

    ⭐⭐ ASKED OF `alerting`'s OWN READINESS FUNCTIONS, never re-implemented here. A second answer
    to "is this channel usable" is a second thing to keep in step, and the whole point of this
    module is to stop two places disagreeing about one convention.

    Why it is worth asking at all: `_check_required` establishes that a key carries a VALUE, and a
    value is not a working channel. `NTFY_URL_PROD=my-topic` — a bare topic instead of the full
    URL, and the single likeliest operator typo — is non-blank, so validation used to pass, the
    confirmation alert went out saying the configuration "checks out", and the ntfy channel was
    dead the whole time. `validate_boot`'s own refusal text promises "rather than starting a
    service whose alerts go nowhere"; this is what makes that sentence true.
    """
    cfg = AlertConfig.load(settings)
    dead = []
    if settings.ntfy_url and not cfg.ntfy_ready():
        dead.append(
            "the ntfy URL is set but unusable — it must be the FULL topic URL "
            "(https://<host>/<topic>), not a bare topic, and it may not carry userinfo")
    if not cfg.email_ready():
        dead.append(
            "the email settings are present but unusable — the process log line above names "
            "which one, and SMTP_USER defaults to EMAIL_FROM only when that is a bare mailbox")
    if settings.ntfy_url and settings.title_prefix:
        # ⭐ ASKED ONLY WHEN ntfy IS CONFIGURED, and that scoping is the point. `http.client`
        # encodes a header value as LATIN-1, so a prefix carrying (say) a currency sign fails every
        # ntfy send with a `UnicodeEncodeError` while email is perfectly fine — which is why this
        # cannot be a refusal in `AlertSettings.__post_init__`, where it would reject a legitimate
        # non-ASCII prefix for an email-only deployment. The constructor refuses CONTROL characters,
        # which break both channels; this refuses what breaks only the one that is configured.
        try:
            settings.title_prefix.encode("latin-1")
        except UnicodeEncodeError:
            dead.append(
                "the alert title prefix contains a character ntfy's Title header cannot carry "
                "(it is encoded latin-1), so every ntfy send would fail — the prefix is built "
                "from the service name, so rename the service or give it its own topic")
    # ⭐ `smtp_port_fault` EXISTS FOR EXACTLY THIS CALL — pure, logs nothing, sends nothing,
    # and documented as the way to ask "is this port acceptable" at BOOT rather than by provoking
    # a send-time ERROR that logs on every notification and pages on none.
    fault = smtp_port_fault((cfg.email or {}).get("SMTP_PORT", ""))
    if fault:
        dead.append(f"SMTP_PORT is unusable: {fault}")
    if dead:
        # ⚠️ THE CLOSING SENTENCE IS BRANCH-DEPENDENT, because the blanket one was FALSE for the
        # port. `smtp_port()` falls back to the documented default and `email_ready()` deliberately
        # does not consult the port, so a port fault alone would still have delivered on both
        # channels: "your alerts go nowhere" would have been a claim-inflated reason for a refusal
        # that is really about being explicit at boot. The refusal stays; the reason gets honest.
        goes_nowhere = len(dead) > 1 or not dead[0].startswith("SMTP_PORT")
        why = ("Refusing to boot rather than starting a service whose alerts go nowhere."
               if goes_nowhere else
               "Refusing to boot: the port would fall back to the default and mail would very "
               "likely still be delivered, but a setting this file states and states wrongly is "
               "fixed at boot, not discovered later.")
        raise _refuse(
            f"{config} parses and carries every required key, but: " + "; ".join(dead) + ". "
            + why)


def _announce(settings: AlertSettings, env: str, values: dict[str, str], config: Path,
              alerter: Alerter | None) -> bool:
    """Send the one-time validation alert. Returns whether there was anything to send it through.

    Best effort by construction once an `Alerter` exists — `notify` never raises.
    """
    target = alerter or current_alerter()
    if target is None:
        # ⛔ NOT A CONFIG FAILURE, BUT NOT A SUCCESS EITHER, AND THE CALLER MUST KNOW. The
        # configuration IS valid. What is missing is the channel to say so through, and if the
        # marker were written anyway this boot would be recorded as validated with the
        # confirmation never sent — after which no later boot would send one either, because the
        # marker suppresses them. That is the "marked validated, alert never left" state the
        # ordering comment in `validate_boot` exists to prevent, arrived at from the other side.
        log.warning("alerting: configuration validated, but no Alerter is installed, so the "
                    "one-time validation alert was NOT sent and the marker was NOT written — the "
                    "next boot will validate again. Call alerting.configure(settings) before "
                    "validate_boot(), or pass alerter=.")
        return False
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
    return True


def _write_marker(marker: Path, digest: str) -> None:
    """Record the digest of the config that was VALIDATED, in the app's own read-write directory.

    ⭐ ONE LINE, `sha256:<hex>`, AND NOTHING ELSE. The marker used to be empty and its whole
    meaning lived in its modification time; it now carries the answer itself, so nothing about the
    ORDER of these two writes matters any more. That deleted a real defect rather than a nicety:
    on a one-second-granularity filesystem — an ext4 with 128-byte inodes, which several CI
    runners' scratch disks are — the config and the marker landed on the same second, the marker
    could never be strictly newer, and every boot re-validated and re-alerted forever. Measured
    green on NTFS and red on both Linux jobs at the same commit. A digest has no such failure mode.

    ⛔ THE DIGEST IS A PARAMETER, NOT RE-READ HERE, AND THAT IS THE WHOLE POINT. An earlier version
    hashed the file at this moment — after the checks and after an SMTP round-trip — so a config
    rewritten during that window was recorded as validated although nothing had looked at it, and
    no later boot re-checked. That reproduced #15's outcome through a narrower door. What is
    recorded must be what was CHECKED; `validate_boot` takes it before anything reads the file.

    ⚠️ AND THE CALLER NOW OWNS THE FORMAT, which the old signature guaranteed by computing it. A
    digest without the `sha256:` prefix would be written happily and then match nothing forever —
    a marker that re-validates and re-announces on EVERY boot, silently, which is precisely the
    failure the deleted `_outrank` existed to prevent. So it is checked here rather than trusted.

    ⚠️ A FAILURE HERE IS NOT A BOOT FAILURE. The configuration IS valid — that has already been
    established and announced. An unwritable `CONFIG_PATH` costs one redundant validation and one
    duplicate alert per boot, which is noise; refusing to start over it would turn a
    missing-read-write-volume into an outage of the service itself.
    """
    if not digest.startswith("sha256:"):
        # Empty means the CONFIG could not be read when the digest was taken, one statement before
        # `read_config` read it successfully — a flap on the shared mount. Anything else means a
        # caller passed a shape this file does not write. Both end the same way: no marker, one
        # more validation on the next boot, and a line saying so rather than silence.
        log.warning("alerting: no usable digest for the configuration file (%r), so no validation "
                    "marker was written to %s — the configuration is valid and was announced, but "
                    "the next boot will validate and announce again.", digest, marker)
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(digest + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("alerting: could not write the validation marker %s (%s) — the configuration "
                    "is valid and was announced, but every boot will re-validate and re-announce "
                    "until this directory is writable. It is the CONFIG_PATH variable.",
                    marker, type(exc).__name__)
