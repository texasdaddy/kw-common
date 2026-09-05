"""Tests for `kw_common.alerting_env` — the fleet convention, and the two `alerting` changes it
needed.

These travel with the module for the same reason every other suite here does: a behaviour that is
not tested here becomes an untested copy in every consumer the moment it ships. That is not
rhetorical for this package — the whole reason it exists is that a set of container templates each
declared a different shape of alerting variable, every one of them somebody's reasonable reading
of the same prose.

⭐ WHAT THIS FILE IS ADVERSARIAL ABOUT. Three properties are easy to write a test that CANNOT
fail, and each of them is the one that matters:

* **"no default"** — a test that sets a variable and checks the value proves nothing about what
  happens when it is unset. Every convention-layer test here DELETES the variables first.
* **"the prefix is right"** — checking the returned string is half. The prefix has to reach the
  outbound title and NOT reach the de-duplication key, and only one of those is visible in the
  return value.
* **"it refuses"** — a refusal that alerts about itself through the channel it is refusing over
  is not a refusal. Every failure case below asserts the channels recorded NOTHING.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from kw_common import alerting, alerting_env
from kw_common.alerting import ERROR, OK, AlertConfig, Alerter, AlertSettings
from kw_common.alerting_env import (
    CONFIG_PATH_VAR,
    CONFIG_RELPATH,
    DEPLOY_ENV_VAR,
    ENVIRONMENTS,
    MARKER_NAME,
    SHARED_ROOT_VAR,
    AlertEnvError,
    config_file_for,
    load_alert_settings,
    load_alert_settings_from_env,
    marker_name,
    normalise_env,
    ntfy_key,
    read_config,
    required_keys,
    validate_boot,
    validate_boot_from_env,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "kw_common" / "alerting_env.py"
REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "alerting.env.template"
SETUP_DOC = REPO_ROOT / "docs" / "alerting-setup.md"

# The three variables, as this suite deletes them. Named from the module's own constants so a
# rename cannot leave a test setting a variable nothing reads.
ENV_VARS = (SHARED_ROOT_VAR, CONFIG_PATH_VAR, DEPLOY_ENV_VAR)


# --------------------------------------------------------------------------- fixtures / helpers
class Spy:
    """Records every (config, spec, title, message) it is handed. Never raises by itself.

    Same shape and same reason as the spy in `test_alerting.py`: an assertion raised in here would
    be swallowed by the `except Exception` around each channel, so the test would pass whether or
    not the behaviour under test exists. The assertions live in the tests, reading `.calls`.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[AlertConfig, alerting.SeveritySpec, str, str]] = []

    def __call__(self, cfg: AlertConfig, spec: alerting.SeveritySpec, title: str,
                 message: str) -> None:
        self.calls.append((cfg, spec, title, message))


@pytest.fixture(autouse=True)
def channels(monkeypatch: pytest.MonkeyPatch) -> dict[str, Spy]:
    """Both channels replaced with spies. AUTOUSE, so a failure-path test that forgets to ask for
    them still cannot reach a real send — and "the refusal alerted nobody" is then a claim about
    a recording rather than about a connection that happened to fail."""
    spies = {"email": Spy(), "ntfy": Spy()}
    monkeypatch.setattr(alerting, "_CHANNELS",
                        tuple((name, spies[name]) for name in ("email", "ntfy")))
    return spies


@pytest.fixture(autouse=True)
def _no_inherited_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐⭐ THE THREE VARIABLES ARE DELETED FOR EVERY TEST IN THIS FILE, AUTOUSE.

    Without this the suite is only as honest as the machine it runs on: a developer with
    `DEPLOY_ENV` exported, or a CI job that happens to set one, turns "it refuses when unset" into
    "it read the ambient value and worked", and the test still passes. Deleting them up front
    makes the ONLY way a variable is set the one a test sets deliberately.
    """
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


GOOD_CONFIG = {
    "EMAIL_TO": "ops@example.com",
    "EMAIL_FROM": "svc@example.com",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": "587",
    "SMTP_PASSWORD": "not-a-real-password",
    "NTFY_URL_DEV": "https://ntfy.example.com/shared-dev",
    "NTFY_URL_PROD": "https://ntfy.example.com/shared-prod",
}


def write_shared(root: Path, **overrides: str) -> Path:
    """Create `<root>/configs/alerting.env` and return its path.

    `overrides` adds or replaces keys; a value of `None` is not accepted — to REMOVE a key, pass
    it as `""` and the callers that care about the difference say which they mean.
    """
    values = {**GOOD_CONFIG, **overrides}
    path = config_file_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def drop(root: Path, *keys: str) -> Path:
    """The same file with `keys` ABSENT — not blank. Absent and blank are different mistakes."""
    values = {k: v for k, v in GOOD_CONFIG.items() if k not in keys}
    path = config_file_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def silence(channels: dict[str, Spy]) -> list[str]:
    """Every title either channel was asked to send. Empty is the assertion most tests here want."""
    return [title for spy in channels.values() for _, _, title, _ in spy.calls]


def reset(channels: dict[str, Spy]) -> None:
    """Forget what has been sent so far — EVERY channel, which is the point.

    ⚠️ Clearing only `channels["ntfy"]` and then asserting `silence(channels) == []` is a test that
    can never pass, because the email spy still holds the first boot's confirmation. It reads as a
    production failure ("a refusal alerted anyway") when it is nothing of the sort, so the reset
    is a named helper rather than two lines a test can get half right.
    """
    for spy in channels.values():
        spy.calls.clear()


# ====================================================================== acceptance 1: it is pure
def test_the_loader_runs_with_no_environment_set_at_all(tmp_path: Path) -> None:
    """⭐ THE ACCEPTANCE CRITERION, ASKED THE ONLY WAY THAT MEANS ANYTHING.

    The three variables are already deleted by the autouse fixture. If `load_alert_settings` read
    any of them — or fell back to a default path — this raises rather than returning settings.
    """
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    assert settings.service == "feed-poller"
    assert settings.ntfy_url == GOOD_CONFIG["NTFY_URL_PROD"]
    assert settings.config_file == str(config_file_for(tmp_path))


def test_the_loader_reads_no_environment_variable_other_than_the_three_documented_ones() -> None:
    """⭐ ASKED OF THE AST, NOT OF THE TEXT.

    A comment explaining a variable must not fail this, and a real `os.environ` read must not be
    able to hide behind one. Everything the module reads out of the environment must come through
    `_require_env`, whose only callers pass one of the three module constants.

    This is the drift-resistant form of "the convention is a function signature": a fourth
    variable appearing here is a fourth thing a container template has to know, which is the
    divergence the package exists to close.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    environ_reads = 0
    for node in ast.walk(tree):
        # `os.environ[...]`, `os.environ.get(...)`, `os.getenv(...)` — every spelling.
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            environ_reads += 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            target = node.func.value
            if (isinstance(target, ast.Attribute) and target.attr == "environ") or \
                    node.func.attr == "getenv":
                for arg in node.args[:1]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        names.add(arg.value)
                    elif isinstance(arg, ast.Name):
                        names.add(f"<{arg.id}>")
    assert environ_reads >= 1, "this test is no longer looking at an environment read at all"
    # The only literal-or-name argument is the parameter of `_require_env`, so no test can be
    # satisfied by a variable spelled inline somewhere else.
    assert names <= {"<name>"}, f"the module reads environment variables directly: {sorted(names)}"

    # ⭐ AND THE SECOND HALF, WHICH THE FIRST DOES NOT COVER. "Everything comes through one helper"
    # is satisfied by a FOURTH variable read through that same helper — measured: a copy with one
    # added still passed this test, and was caught only behaviourally, two files away. So the CALL
    # SITES are checked too: every argument to `_require_env` must be one of the three constants.
    allowed = {"SHARED_ROOT_VAR", "CONFIG_PATH_VAR", "DEPLOY_ENV_VAR"}
    passed: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_require_env"):
            arg = node.args[0] if node.args else None
            passed.add(arg.id if isinstance(arg, ast.Name) else f"<not a bare name: {arg!r}>")
    assert passed, "no _require_env call site was found — this half of the test proves nothing"
    assert passed <= allowed, (
        f"the module reads environment variables beyond the three the standard declares: "
        f"{sorted(passed - allowed)}. Each one is a fourth thing a container template has to "
        f"know, which is the divergence this package exists to close.")


def test_the_path_fault_and_the_encoding_fault_are_reported_as_different_things(
        tmp_path: Path) -> None:
    """⚠️ `UnicodeDecodeError` IS a `ValueError`, so one combined branch reported an embedded NUL
    in the PATH — which `open()` rejects with a plain `ValueError` — as "the file is not UTF-8
    text". The right refusal pointing the wrong way: an operator would go looking at the file's
    encoding for a fault in the variable that named it."""
    with pytest.raises(AlertEnvError, match="not a usable path"):
        read_config(str(tmp_path / "with\x00nul.env"))

    bad = tmp_path / "utf16.env"
    bad.write_bytes("EMAIL_TO=ops@example.com\n".encode("utf-16"))
    with pytest.raises(AlertEnvError, match="UTF-8"):
        read_config(bad)


def test_no_operator_path_is_a_default_or_a_fallback_anywhere_in_the_module() -> None:
    """⭐ ACCEPTANCE: "No operator path appears anywhere in the package as a default or fallback."

    Every string constant in the module is checked for an absolute path — a leading `/` or a
    Windows drive letter. Docstrings are the one exception and are excluded explicitly, because a
    docstring showing `/data/alerts.json` in an example is documentation, not a default.

    The leak guard catches an operator path with a SHAPE (a pool, an IP, a domain). This catches
    the shapeless kind: `/opt/whatever` is not a leak and is still a default that will be wrong on
    somebody's machine.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)
                  and isinstance(node.body[0].value.value, str)}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if text.startswith(("/", "\\")) or (len(text) > 2 and text[1] == ":" and text[2] in "/\\"):
            offenders.append(f"line {node.lineno}: {text!r}")
    assert offenders == [], (
        "these look like absolute paths compiled into the module:\n" + "\n".join(offenders))


def test_the_relative_config_path_is_the_only_path_the_module_knows() -> None:
    """A vacuity guard for the test above: it would also pass on a module with no strings at all."""
    assert CONFIG_RELPATH == "configs/alerting.env"
    assert not Path(CONFIG_RELPATH).is_absolute()


def test_the_marker_name_is_pinned_to_its_literal() -> None:
    """⭐ THE SAME VACUITY GUARD, FOR THE CONSTANT THAT DID NOT HAVE ONE.

    Every marker test builds its expected path FROM `MARKER_NAME`, so the constant is asserted
    against itself: renaming it, or dropping the leading dot the comment calls deliberate, is
    invisible to all of them. `CONFIG_RELPATH` has had this guard from the start; the asymmetry
    was the gap.
    """
    assert MARKER_NAME == ".alerting-validated"
    assert MARKER_NAME.startswith("."), "the marker is a dotfile on purpose"
    assert marker_name("prod") == ".alerting-validated-prod"
    assert marker_name("DEV") == ".alerting-validated-dev"


# ============================================================ acceptance 2: it fails loudly
@pytest.mark.parametrize("missing", [SHARED_ROOT_VAR, DEPLOY_ENV_VAR])
def test_the_env_layer_refuses_a_missing_variable_rather_than_defaulting(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    write_shared(tmp_path)
    monkeypatch.setenv(SHARED_ROOT_VAR, str(tmp_path))
    monkeypatch.setenv(DEPLOY_ENV_VAR, "prod")
    monkeypatch.delenv(missing)

    with pytest.raises(AlertEnvError) as exc:
        load_alert_settings_from_env("feed-poller")
    # ⭐ IT MUST LEAD WITH THE ONE THAT IS MISSING. `missing in str(exc.value)` was satisfied
    # UNCONDITIONALLY: the message ends by listing all three variable names as guidance, so a
    # refusal hardcoded to say the wrong one passed both of these tests. Measured.
    assert str(exc.value).startswith(missing), str(exc.value)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_variable_set_to_blank_is_refused_because_that_is_what_an_unfilled_template_looks_like(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    """⚠️ THE COMMON CASE, NOT THE EXOTIC ONE. A container platform passes every unset optional
    Variable as an EMPTY STRING, so "declared in the template and never filled in" arrives here as
    blank rather than as absent. Treating blank as set is how such a template reads as configured.
    """
    write_shared(tmp_path)
    monkeypatch.setenv(SHARED_ROOT_VAR, blank)
    monkeypatch.setenv(DEPLOY_ENV_VAR, "prod")
    with pytest.raises(AlertEnvError, match="empty"):
        load_alert_settings_from_env("feed-poller")


def test_the_env_layer_reads_shared_root_and_builds_the_documented_subpath(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_shared(tmp_path)
    monkeypatch.setenv(SHARED_ROOT_VAR, str(tmp_path))
    monkeypatch.setenv(DEPLOY_ENV_VAR, "dev")
    settings = load_alert_settings_from_env("feed-poller")
    assert settings.config_file == str(tmp_path / "configs" / "alerting.env")
    assert settings.ntfy_url == GOOD_CONFIG["NTFY_URL_DEV"]


@pytest.mark.parametrize("value", ["", "staging", "production", "DEV ELOPMENT", "1"])
def test_an_environment_that_is_not_dev_or_prod_is_refused_rather_than_guessed(value: str) -> None:
    with pytest.raises(AlertEnvError):
        normalise_env(value)


def test_a_non_string_environment_is_refused_without_a_TypeError() -> None:
    """The caller catches `AlertEnvError`; anything else escapes their boot handler."""
    with pytest.raises(AlertEnvError):
        normalise_env(None)


# ================================================== acceptance 3: casing cannot change behaviour
@pytest.mark.parametrize("spelling", ["prod", "Prod", "PROD", "  pRoD  "])
def test_every_spelling_of_an_environment_produces_identical_settings(
        tmp_path: Path, spelling: str) -> None:
    """⭐ IDENTICAL SETTINGS, not merely the same URL. The prefix carries the environment name
    too, so a loader that selected the right topic while writing `[PROD][feed-poller]` into each
    title would pass a URL-only assertion and ship two spellings of one deployment to the operator.
    """
    write_shared(tmp_path)
    canonical = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    assert load_alert_settings(config_file_for(tmp_path), spelling, "feed-poller") == canonical
    assert canonical.title_prefix == "[prod][feed-poller] "


def test_the_manifest_is_case_insensitive_the_same_way() -> None:
    assert required_keys("PROD") == required_keys("prod")
    assert required_keys("prod")[-1] == "NTFY_URL_PROD"
    assert required_keys("dev")[-1] == "NTFY_URL_DEV"


def test_the_manifest_requires_the_email_block_and_leaves_smtp_user_optional() -> None:
    """⭐ THE MANIFEST IS OWNED HERE, so this is the test that stops apps disagreeing about
    what prod requires. `SMTP_USER` is deliberately NOT in it — it defaults to `EMAIL_FROM`."""
    for env in ("dev", "prod"):
        keys = required_keys(env)
        assert "SMTP_USER" not in keys
        for required in ("EMAIL_TO", "EMAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_PASSWORD"):
            assert required in keys


def test_the_environment_topic_is_required_even_for_a_service_that_has_its_own(
        tmp_path: Path) -> None:
    """⭐ THE ONE JUDGEMENT CALL IN THE MANIFEST, PINNED SO IT CANNOT BE QUIETLY REVERSED.

    The file is FLEET-SHARED. A file missing `NTFY_URL_PROD` is broken for every service that has
    no override, and the next service to adopt is exactly that one — so an override does not
    excuse the shared key. Refusing here costs one boot; not refusing costs an incident that
    produced no page, in a service nobody was thinking about.
    """
    config = drop(tmp_path, "NTFY_URL_PROD")
    config.write_text(
        config.read_text(encoding="utf-8")
        + "NTFY_URL_FEED_POLLER=https://ntfy.example.com/own\n", encoding="utf-8")
    settings = load_alert_settings(config, "prod", "feed-poller")
    assert settings.ntfy_url == "https://ntfy.example.com/own"   # the service itself is fine
    with pytest.raises(AlertEnvError, match="NTFY_URL_PROD"):
        validate_boot(settings, "prod", tmp_path / "appconfig")


# ============================================ acceptance 4: the topic decides the prefix
def test_a_dedicated_topic_wins_and_carries_no_prefix(tmp_path: Path) -> None:
    write_shared(tmp_path, NTFY_URL_BACKUP_AGENT="https://ntfy.example.com/backup")
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "backup-agent")
    assert settings.ntfy_url == "https://ntfy.example.com/backup"
    assert settings.title_prefix == ""


def test_the_live_example_keeps_its_behaviour_in_both_environments(tmp_path: Path) -> None:
    """⚠️ backup-agent IS the live example the standard names, and this package must not change what
    it does. Its own topic, in either environment, with no prefix — and the environment topic
    sitting right there in the same file, unused by it."""
    write_shared(tmp_path, NTFY_URL_BACKUP_AGENT="https://ntfy.example.com/backup")
    for env in ("dev", "prod"):
        settings = load_alert_settings(config_file_for(tmp_path), env, "backup-agent")
        assert settings.ntfy_url == "https://ntfy.example.com/backup"
        assert settings.title_prefix == ""


def test_a_service_on_the_shared_topic_carries_the_env_and_service_prefix(tmp_path: Path) -> None:
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "dev", "report-mailer")
    assert settings.ntfy_url == GOOD_CONFIG["NTFY_URL_DEV"]
    assert settings.title_prefix == "[dev][report-mailer] "


def test_a_blank_override_means_the_shared_topic_and_therefore_the_prefix(tmp_path: Path) -> None:
    """A cleared key means "stop using my own topic", not "use no topic at all"."""
    write_shared(tmp_path, NTFY_URL_FEED_POLLER="   ")
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    assert settings.ntfy_url == GOOD_CONFIG["NTFY_URL_PROD"]
    assert settings.title_prefix == "[prod][feed-poller] "


def test_a_missing_shared_topic_still_gets_the_prefix(tmp_path: Path) -> None:
    """⭐ THE PREFIX FOLLOWS WHICH KEY WON, NOT WHETHER A URL WAS FOUND.

    Deciding it on the URL's presence would silently drop the prefix for exactly the deployment
    that is already broken — so when the file is later fixed, the first alerts arrive unlabelled
    from a service the operator has to guess at.
    """
    settings = load_alert_settings(drop(tmp_path, "NTFY_URL_PROD"), "prod", "feed-poller")
    assert settings.ntfy_url == ""
    assert settings.title_prefix == "[prod][feed-poller] "


def test_the_prefix_reaches_BOTH_outbound_titles(tmp_path: Path,
                                                 channels: dict[str, Spy]) -> None:
    """⭐ THE HALF A RETURN-VALUE ASSERTION CANNOT SEE. `title_prefix` being correct on the
    settings object says nothing about whether anything applies it."""
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    Alerter(settings).notify(ERROR, "Backup failed", "disk full")
    for name, spy in channels.items():
        assert spy.calls, f"the {name} channel was not asked to send anything"
        assert spy.calls[0][2] == "[prod][feed-poller] Backup failed"


def test_the_prefix_does_NOT_reach_the_dedup_key_the_error_record_or_the_log_line(
        tmp_path: Path, channels: dict[str, Spy], caplog: pytest.LogCaptureFixture) -> None:
    """⭐⭐ THE PROPERTY THAT BREAKS SILENTLY AND EXPENSIVELY.

    A condition's identity must not change when its deployment does. If the prefix reached the
    de-duplication key, promoting a service from dev to prod would make every escalating
    condition it had already reported look new and re-page — and the same alert would appear
    twice in the retrievable error log under two names.

    Three surfaces, one test, because they share one cause: the prefix is applied at the send
    boundary and nowhere upstream of it.
    """
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    state = tmp_path / "state.json"
    errors = tmp_path / "logs" / "errors.log"
    from dataclasses import replace
    settings = replace(settings, state_file=str(state), error_log=str(errors))

    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        Alerter(settings).notify(ERROR, "Backup failed", "disk full", escalating=True)

    assert list(json.loads(state.read_text(encoding="utf-8"))) == ["Backup failed"]
    record = json.loads(errors.read_text(encoding="utf-8").splitlines()[0])
    assert record["title"] == "Backup failed"
    assert "[prod][feed-poller]" not in caplog.text
    # ...and the outbound copy really did carry it, so this is not passing because nothing works.
    assert channels["ntfy"].calls[0][2] == "[prod][feed-poller] Backup failed"


def test_the_prefix_is_cosmetic_and_cannot_cost_a_delivery(channels: dict[str, Spy]) -> None:
    """⭐ THE DEFECT THIS PACKAGE INTRODUCED AND THEN FIXED, PINNED.

    `Alerter` takes any settings-shaped object and validates nothing — a documented property, and
    three existing tests rely on it. Reading `settings.title_prefix` straight in `_dispatch` made
    every such stand-in fail EVERY channel with an `AttributeError` swallowed by the outer guard.
    A cosmetic prefix must never be able to do that.
    """
    class NoPrefix:
        service = "svc"
        config_file = None
        ntfy_url = "https://ntfy.example.com/svc"
        state_file = None
        error_log = None
        max_record_field = 4000
        max_tracked_conditions = 200
        allow_cleartext_ntfy = False

    class PrefixRaises(NoPrefix):
        @property
        def title_prefix(self) -> str:
            raise RuntimeError("this settings object is hostile")

    class PrefixIsNone(NoPrefix):
        # `None` is the obvious spelling of "no prefix", and a settings object predating the field
        # can carry it.
        title_prefix = None

    class PrefixIsTruthyNonString(NoPrefix):
        # ⭐⭐ THE CASE THAT MAKES THE `isinstance` GUARD LOAD-BEARING — and the first version of
        # this test used `None` ALONE, which SURVIVED removing the guard. Measured, not reasoned:
        # `_dispatch` only prefixes when the value is truthy, so a FALSY non-string is already
        # handled one layer down and proves nothing about the guard. A truthy non-string is the
        # only input that separates the two, and without it `_title_prefix`'s stated contract —
        # "anything that is not a plain string becomes no prefix at all" — was asserted by nothing.
        title_prefix = 5

    for stand_in in (NoPrefix(), PrefixRaises(), PrefixIsNone(), PrefixIsTruthyNonString()):
        channels["ntfy"].calls.clear()
        results = Alerter(stand_in).notify(ERROR, "still delivered", "...")  # type: ignore[arg-type]
        assert results["ntfy"] == "sent", f"{type(stand_in).__name__} cost the notification"
        # ⚠️ THE TITLE, not just the delivery: a guard that returned `str(value)` would deliver
        # happily and put "None" in front of it.
        assert channels["ntfy"].calls[0][2] == "still delivered"


def test_a_non_string_prefix_is_refused_by_the_constructor() -> None:
    """`None` is the obvious thing to pass for "no prefix" and would render as the text `None` in
    front of every alert title — visible, wrong, and only in production."""
    with pytest.raises(ValueError, match="title_prefix"):
        AlertSettings(service="svc", title_prefix=None)  # type: ignore[arg-type]


# ================================================================ the service→key transform
@pytest.mark.parametrize(("service", "key"), [
    ("feed-poller", "NTFY_URL_FEED_POLLER"),
    ("backup-agent", "NTFY_URL_BACKUP_AGENT"),
    ("report-mailer", "NTFY_URL_REPORT_MAILER"),
    # A DOT separator, which is a different character class from the hyphens above and is the
    # spelling the transform used to be asked about with a real consumer's name. Invented here:
    # this file ships in the sdist, so a real one would publish the fleet's inventory (#16).
    ("edge.relay", "NTFY_URL_EDGE_RELAY"),
    ("  spaced  name ", "NTFY_URL_SPACED_NAME"),
])
def test_the_service_key_transform_has_exactly_one_answer(service: str, key: str) -> None:
    """⚠️ ONE RULE, NO CANDIDATE LIST. Accepting several spellings would make the file's meaning
    depend on which one an operator typed, and a service reading the wrong one lands on the SHARED
    topic with a prefix it should not have."""
    assert ntfy_key(service) == key


@pytest.mark.parametrize("service", ["", "   ", "---", None, 7])
def test_a_service_name_that_cannot_name_a_key_is_refused(service: object) -> None:
    with pytest.raises(AlertEnvError):
        ntfy_key(service)  # type: ignore[arg-type]


def test_the_loader_validates_the_service_name_before_it_reads_anything(tmp_path: Path) -> None:
    """A blank service name must not surface as "the config file does not exist"."""
    with pytest.raises(AlertEnvError, match="non-blank"):
        load_alert_settings(config_file_for(tmp_path), "prod", "  ")


# ==================================================== acceptance 5: SMTP_USER defaults to FROM
class _FakeSMTP:
    """Records the credentials `login()` was given. Class-level, because `_send_email` constructs
    its own instance and the test cannot reach it."""

    seen: ClassVar[list[tuple[str, str]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, **kwargs: object) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        type(self).seen.append((user, password))

    def send_message(self, msg: object) -> None:
        return None


@pytest.fixture
def smtp_login(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    _FakeSMTP.seen = []
    monkeypatch.setattr(alerting.smtplib, "SMTP", _FakeSMTP)
    return _FakeSMTP.seen


def _send_one(settings: AlertSettings) -> None:
    alerting._send_email(AlertConfig.load(settings), alerting.SEVERITIES[OK], "t", "m")


def test_smtp_user_absent_authenticates_as_email_from(
        tmp_path: Path, smtp_login: list[tuple[str, str]]) -> None:
    """⭐ ASSERTED ON WHAT THE RELAY WAS ASKED FOR, not on a dictionary. The claim is that the
    default reaches SMTP AUTH; a dict assertion is satisfied by a default that a later line
    overwrites."""
    config = drop(tmp_path, "SMTP_USER")   # absent, not blank
    settings = load_alert_settings(config, "prod", "feed-poller")
    _send_one(settings)
    assert smtp_login == [("svc@example.com", GOOD_CONFIG["SMTP_PASSWORD"])]


def test_smtp_user_present_and_different_is_honoured(
        tmp_path: Path, smtp_login: list[tuple[str, str]]) -> None:
    """⚠️ THE DIRECTION A DEFAULT BREAKS. `SMTP_USER` and `EMAIL_FROM` are the same value in this
    fleet today and are different things — the auth identity and the header. They diverge on a
    verified alias and on a relay whose user is literally `apikey`, and a default that overwrote
    the configured value would fail authentication for exactly those deployments."""
    config = write_shared(tmp_path, SMTP_USER="apikey")
    settings = load_alert_settings(config, "prod", "feed-poller")
    _send_one(settings)
    assert smtp_login == [("apikey", GOOD_CONFIG["SMTP_PASSWORD"])]


def test_a_blank_smtp_user_also_falls_back(tmp_path: Path,
                                           smtp_login: list[tuple[str, str]]) -> None:
    """Blank is how a cleared container Variable arrives, so it must mean the same as absent."""
    config = write_shared(tmp_path, SMTP_USER="")
    settings = load_alert_settings(config, "prod", "feed-poller")
    _send_one(settings)
    assert smtp_login == [("svc@example.com", GOOD_CONFIG["SMTP_PASSWORD"])]


def test_an_empty_config_is_still_UNCONFIGURED_rather_than_misconfigured(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """⭐ THE SHORT-CIRCUIT THE DEFAULT COULD HAVE BROKEN. `email_ready()` distinguishes "nothing
    is configured" (a boot-time report) from "something is wrong" (an error on every send). If
    `SMTP_USER` defaulted to something non-empty, an empty file would have started reporting the
    second — one error line per notification, for a service that simply has no email channel."""
    path = config_file_for(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    settings = AlertSettings(service="svc", config_file=str(path))
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert AlertConfig.load(settings).email_ready() is False
    assert caplog.text == ""


# ================================================================ reading the file: one parser
def test_read_config_and_the_send_path_agree_on_a_hostile_file(tmp_path: Path) -> None:
    """⭐⭐ ONE PARSER, TWO ERROR POLICIES — the property that keeps boot validation honest.

    If this module parsed the file even slightly differently from the one `alerting` re-reads on
    every notification, validation could pass a file the send path then reads as something else.
    The sample carries every tolerance the format has: comments, blank lines, `export `, quoting,
    surrounding whitespace and CRLF endings.
    """
    path = tmp_path / "sample.env"
    path.write_bytes(
        b"# a comment\r\n"
        b"\r\n"
        b"export EMAIL_TO='ops@example.com'\r\n"
        b'  SMTP_HOST = "smtp.example.com"  \r\n'
        b"NOT A SETTING\r\n"
        b"NTFY_URL_PROD=https://ntfy.example.com/x\r\n"
    )
    assert read_config(path) == alerting._parse_env_file(str(path))
    assert read_config(path)["SMTP_HOST"] == "smtp.example.com"
    assert read_config(path)["EMAIL_TO"] == "ops@example.com"


def test_read_config_sees_a_LONE_CR_the_same_way_the_send_path_does(tmp_path: Path) -> None:
    r"""⭐ THE HALF CRLF CANNOT REACH, and the half that makes `newline=""` load-bearing.

    Universal-newline translation rewrites `\r\n` AND a lone `\r` to `\n` before the parser sees a
    character, so a CRLF sample proves nothing about the flag: strip it and CRLF still parses
    identically. A LONE CR does not — without `newline=""` the value is split and a recipient is
    SILENTLY DROPPED, which is the exact defect `alerting`'s parser comments describe. Measured
    both ways: dropping the flag here survived every other test in this file.

    Written with `write_bytes`, because a text write would translate it back.
    """
    path = tmp_path / "lone-cr.env"
    path.write_bytes(b"EMAIL_TO=a@example.com\rb@example.invalid\n")
    assert read_config(path)["EMAIL_TO"] == "a@example.com\rb@example.invalid"
    assert read_config(path) == alerting._parse_env_file(str(path))


def test_a_missing_file_is_an_error_here_and_not_an_error_in_the_send_path(
        tmp_path: Path) -> None:
    """⚠️ THE TWO POLICIES, SIDE BY SIDE, so neither can be "harmonised" into the other. Silence
    is right for a notification in flight and wrong at boot."""
    missing = tmp_path / "nope.env"
    assert alerting._parse_env_file(str(missing)) == {}
    with pytest.raises(AlertEnvError, match="does not exist"):
        read_config(missing)


def test_a_mis_encoded_file_raises_a_ValueError_that_is_caught_and_named(tmp_path: Path) -> None:
    """⭐ THE TRAP THE STANDARD NAMES EXPLICITLY: `UnicodeDecodeError` is a `ValueError` and is
    NOT an `OSError`, so a boot check catching only `OSError` lets one bad byte through as a
    traceback. Written here as UTF-16, which is what a desktop editor produces."""
    path = tmp_path / "utf16.env"
    path.write_bytes("EMAIL_TO=ops@example.com\n".encode("utf-16"))
    with pytest.raises(AlertEnvError, match="UTF-8"):
        read_config(path)


def test_a_cp1252_byte_is_the_same_class_of_failure(tmp_path: Path) -> None:
    path = tmp_path / "cp1252.env"
    path.write_bytes(b"EMAIL_FROM=caf\xe9@example.com\n")
    with pytest.raises(AlertEnvError, match="UTF-8"):
        read_config(path)


def test_a_directory_where_the_file_should_be_is_reported_as_unreadable(tmp_path: Path) -> None:
    """Not a `FileNotFoundError` and not a crash: an operator who mounted the wrong thing gets a
    line naming the path."""
    path = tmp_path / "configs"
    path.mkdir()
    with pytest.raises(AlertEnvError) as exc:
        read_config(path)
    # The docstring above promises a line NAMING the path; without this the message could be the
    # single word "unreadable" and the test would still pass.
    assert str(path) in str(exc.value)


# ======================================================== acceptance 6 & 7: boot validation
def _validated(tmp_path: Path, service: str = "feed-poller",
               env: str = "prod") -> tuple[AlertSettings, Path, Path]:
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), env, service)
    marker_dir = tmp_path / "appconfig"
    return settings, marker_dir, marker_dir / marker_name(env)


def test_a_good_config_records_ITS_DIGEST_in_the_marker_and_alerts_exactly_once(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE MARKER CARRIES THE ANSWER NOW, not just its own modification time (#15).

    Computed here from the config's BYTES rather than read back from the module, so the assertion
    fails if the recorded value stops being the digest of the file that was validated.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    alerter = Alerter(settings)

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    assert marker.is_file()
    expected = hashlib.sha256(Path(settings.config_file).read_bytes()).hexdigest()
    assert marker.read_text(encoding="utf-8").strip() == f"sha256:{expected}"
    assert len(channels["ntfy"].calls) == 1
    assert channels["ntfy"].calls[0][1] is alerting.SEVERITIES[OK]


def test_a_second_boot_with_an_unchanged_config_alerts_nobody(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    settings, marker_dir, _marker = _validated(tmp_path)
    alerter = Alerter(settings)
    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    reset(channels)

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is False
    assert silence(channels) == []


def test_editing_the_config_makes_the_next_boot_validate_again(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """No timestamp arithmetic any more: a different file is a different digest, full stop."""
    settings, marker_dir, _marker = _validated(tmp_path)
    alerter = Alerter(settings)
    validate_boot(settings, "prod", marker_dir, alerter=alerter)
    reset(channels)

    config = Path(settings.config_file)
    config.write_text(config.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    assert len(channels["ntfy"].calls) == 1


def test_a_config_RESTORED_at_an_OLDER_timestamp_is_STILL_validated(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ #15, THE MEASURED REPRO, AND THE REASON THE MECHANISM CHANGED.

    `rsync -a`, `cp -p`, `tar -x` and a Docker volume restore all PRESERVE mtime. Under the old
    empty-marker-plus-timestamp rule the marker stayed newer than the restored file, so a config
    that had never been checked booted clean, announced nothing, and the service came up alerting
    nobody. The restored file here is BROKEN — a required key is gone — so the correct outcome is
    not merely "validate again" but "refuse".
    """
    settings, marker_dir, marker = _validated(tmp_path)
    alerter = Alerter(settings)
    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    reset(channels)

    config = Path(settings.config_file)
    good = config.read_text(encoding="utf-8")
    assert "EMAIL_TO=" in good, "the fixture no longer carries the key this test removes"
    config.write_text("\n".join(ln for ln in good.splitlines()
                                if not ln.startswith("EMAIL_TO=")) + "\n", encoding="utf-8")
    older = marker.stat().st_mtime - 3600
    os.utime(config, (older, older))
    assert config.stat().st_mtime < marker.stat().st_mtime, "the restore is not actually older"

    with pytest.raises(AlertEnvError) as exc:
        validate_boot(settings, "prod", marker_dir, alerter=alerter)
    assert "EMAIL_TO" in str(exc.value)
    assert silence(channels) == [], "a failed validation must not alert through the channel"


def test_a_config_restored_with_IDENTICAL_contents_does_NOT_re_validate(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """The other half, and the reason a digest beats `(mtime, size, inode)`.

    An archive tool that restores the SAME bytes has changed nothing, so re-validating would mean
    a duplicate confirmation alert after every backup restore — noise that trains an operator to
    ignore the one message this whole mechanism exists to send. Contents decide; metadata does
    not, in either direction.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    alerter = Alerter(settings)
    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    reset(channels)

    config = Path(settings.config_file)
    raw = config.read_bytes()
    config.unlink()
    config.write_bytes(raw)                       # a restore: same bytes, new inode
    older = marker.stat().st_mtime - 3600
    os.utime(config, (older, older))

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is False
    assert silence(channels) == []


def test_the_skip_does_not_depend_on_the_TIMESTAMPS_at_all(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE OLD MECHANISM, ASSERTED GONE RATHER THAN ASSUMED GONE.

    A marker dated an hour BEFORE the config it validated would have failed the old
    "strictly newer" test outright. Under the digest rule it is simply correct, and this is what
    would go red if a timestamp comparison were ever put back in front of the digest as an
    "optimisation".
    """
    settings, marker_dir, marker = _validated(tmp_path)
    alerter = Alerter(settings)
    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    reset(channels)

    config = Path(settings.config_file)
    older = config.stat().st_mtime - 3600
    os.utime(marker, (older, older))
    assert marker.stat().st_mtime < config.stat().st_mtime

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is False
    assert silence(channels) == []


def test_a_RE_ENCODED_config_that_parses_identically_re_validates(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """The digest is over BYTES, not over the parsed keys, and that is deliberate.

    A file rewritten with different line endings parses to the same mapping and is a different
    file. Since this module refuses a cp1252 or UTF-16 config at boot, a silent re-encode is
    exactly the change worth re-checking — and a digest over the parsed dictionary would miss it.
    """
    settings, marker_dir, _marker = _validated(tmp_path)
    alerter = Alerter(settings)
    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    reset(channels)

    config = Path(settings.config_file)
    config.write_bytes(config.read_bytes().replace(b"\n", b"\r\n"))

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    assert len(channels["ntfy"].calls) == 1


@pytest.mark.parametrize("content", [b"", b"\n", b"validated\n", b"sha256:\n",
                                     b"sha256:not-the-digest\n", b"\xff\xfe\x00"])
def test_a_marker_that_records_no_usable_digest_validates_again(
        tmp_path: Path, channels: dict[str, Spy], content: bytes) -> None:
    """⭐ THE UPGRADE PATH IS THE FIRST CASE IN THAT LIST, and it is why this is parametrized.

    A marker written by 1.2.0 is EMPTY — the whole meaning used to be its modification time — so
    after the upgrade it matches nothing, the first boot validates once, re-announces once, and
    rewrites the marker with a digest. That is the correct migration and it needs no operator
    step; the alternative (treating an empty marker as "validated") would carry the very defect
    this change removes across the upgrade.

    The rest are the ways a marker can be damaged: truncated, half-written, replaced by prose,
    or binary. Every one of them means "I cannot establish that this file was validated", and the
    answer to that is to validate it.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(content)

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert len(channels["ntfy"].calls) == 1


def test_a_marker_recording_a_TRUNCATED_digest_does_not_count_as_a_match(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE WHOLE 64 CHARACTERS, NOT A PREFIX OF THEM.

    A mutation comparing `sha256:` plus the first eight hex characters survived the entire suite,
    because no test ever presented a marker that agrees with the config for part of the digest and
    disagrees after it. A prefix comparison is not a hash comparison — it is a 32-bit one — and the
    cost of getting it wrong is the failure this mechanism exists to remove: a config nobody
    checked, recorded as checked.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    real = hashlib.sha256(Path(settings.config_file).read_bytes()).hexdigest()
    marker_dir.mkdir(parents=True, exist_ok=True)
    # Agrees for 8 hex characters, then does not. `f` -> `0` unless it already is, so this is
    # guaranteed to differ from the real digest whatever the fixture hashes to.
    tail = real[8:]
    wrong = ("f" if tail == "0" * len(tail) else "0") * len(tail)
    marker.write_text(f"sha256:{real[:8]}{wrong}\n", encoding="utf-8")

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert len(channels["ntfy"].calls) == 1


def test_an_UNREADABLE_config_with_a_BLANK_marker_still_refuses_to_boot(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⛔ THE ONE CASE WHERE BOTH GUARDS IN `_marker_matches` HAVE TO HOLD AT ONCE.

    `_config_digest` answers `""` for a config it cannot read, and a blank marker strips to `""`.
    Remove BOTH the `sha256:` prefix check and the `bool(current)` conjunct and `"" == ""` becomes
    a match — an unreadable config, skipped, on a service that would have refused. It is reachable:
    a marker left by 1.2.0 is blank, and a shared mount that failed to attach makes the config
    unreadable, so the two arrive together on exactly the boot that matters.
    Each guard alone covers it, which is why removing either one survives — and why nothing
    noticed that removing both does not.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"")                      # a 1.2.0-era marker
    Path(settings.config_file).unlink()          # the shared mount is not there

    with pytest.raises(AlertEnvError):
        validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings))
    assert silence(channels) == []


def test_the_marker_records_the_config_that_was_CHECKED_not_the_one_on_disk_afterwards(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ #15's OWN OUTCOME, THROUGH A NARROWER DOOR — and the reason the digest is a parameter.

    `_announce` is an SMTP round-trip plus an HTTP POST, so the gap between "these bytes check out"
    and "record what checked out" is seconds of wall clock, and the file it spans is the
    FLEET-SHARED one that a config push rewrites while services restart. Hashing the file at the
    END of that window records a file nobody looked at as validated — and then no later boot
    re-checks it, because the marker matches what is on disk. Measured: the marker equalled the
    digest of a config with `NTFY_URL_PROD` removed, and the next boot skipped.

    The alerter here stands in for anything that takes time. It is a SPY on the ordering, not a
    contrived race: any writer touching the shared file during a restart produces it.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    config = Path(settings.config_file)
    # ⚠️ BYTES, not `read_text`. The digest is over the raw bytes, and `read_text` applies
    # universal newlines — so a text round-trip produces a different digest on any file with CRLF
    # in it and this assertion would fail for a reason that has nothing to do with the race.
    good = config.read_bytes()
    broken = good.replace(b"NTFY_URL_PROD=", b"# removed NTFY_URL_PROD=")
    assert broken != good, "the fixture no longer carries the key this test removes"

    class RewritesDuringTheSend:
        def notify(self, *args: object, **kwargs: object) -> dict[str, str]:
            config.write_bytes(broken)
            return {}

    assert validate_boot(settings, "prod", marker_dir,
                         alerter=RewritesDuringTheSend()) is True  # type: ignore[arg-type]
    # ⚠️ THE STAND-IN HAS TO HAVE RUN. Without this the two assertions below would hold on a file
    # nothing ever rewrote — the test would pass while proving nothing, which is the vacuous-setup
    # shape rather than a defect in the code. Its sibling below carries the same guard.
    assert config.read_bytes() == broken, "the alerter never ran, so no race was created"
    recorded = marker.read_text(encoding="utf-8").strip()
    assert recorded == "sha256:" + hashlib.sha256(good).hexdigest(), (
        "the marker records the file as it stands AFTER validation, so a config rewritten during "
        "the announce is recorded as validated although nothing checked it")
    assert recorded != "sha256:" + hashlib.sha256(broken).hexdigest()

    # ...and therefore the next boot must NOT skip: the file on disk is not the file that passed.
    reset(channels)
    with pytest.raises(AlertEnvError):
        validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings))
    assert silence(channels) == []


def test_the_digest_is_taken_BEFORE_the_file_is_parsed_so_the_window_fails_SAFE(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐ THE REMAINING WINDOW, AND WHICH WAY IT LEANS.

    Hashing and parsing are two reads of the same file, so a writer can land between them. There
    is no way to make that atomic without changing `read_config`'s signature, and the docstring
    says so rather than claiming otherwise — but the ORDER decides which way the window fails.

      digest, then parse: the file that was VALIDATED is newer than the file that was RECORDED, so
        the next boot sees a mismatch and re-validates. Wasteful; safe.
      parse, then digest: the file that was RECORDED is the one nobody checked, and the next boot
        MATCHES it and skips. That is the whole defect, one statement further along.

    Swapping the two lines survived every other test in this suite, because nothing else writes to
    the config between them. This does, through `read_config` itself.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    config = Path(settings.config_file)
    original = config.read_bytes()
    rewritten = original + b"\n# a config push landed mid-boot\n"

    real_read_config = alerting_env.read_config

    def rewrite_then_read(path: object) -> dict[str, str]:
        config.write_bytes(rewritten)
        return real_read_config(path)

    monkeypatch.setattr(alerting_env, "read_config", rewrite_then_read)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert config.read_bytes() == rewritten, "the stand-in did not actually rewrite the file"

    recorded = marker.read_text(encoding="utf-8").strip()
    assert recorded == "sha256:" + hashlib.sha256(original).hexdigest(), (
        "the digest is taken AFTER the parse, so a file written in between is recorded as "
        "validated — and the next boot skips it")

    # ...and therefore the next boot re-validates rather than trusting the marker.
    reset(channels)
    monkeypatch.setattr(alerting_env, "read_config", real_read_config)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert len(channels["ntfy"].calls) == 1


@pytest.mark.parametrize("digest", ["", "deadbeef", "md5:0123456789abcdef"])
def test_a_digest_that_is_not_a_sha256_LINE_writes_no_marker_and_says_so(
        tmp_path: Path, caplog: pytest.LogCaptureFixture, digest: str) -> None:
    """⛔ THE CALLER OWNS THE FORMAT NOW, so the format is checked rather than trusted.

    While `_write_marker` computed the digest itself the `sha256:` prefix was guaranteed. As a
    parameter it is not, and a bare hex digest would be written happily and then match NOTHING on
    every subsequent boot — re-validating and re-announcing forever, silently. That is the failure
    the deleted `_outrank` existed to prevent, reachable again through a signature change.

    `""` is the reachable case rather than a hypothetical: `_config_digest` answers it when the
    shared mount flaps between the digest and the parse, one statement apart.
    """
    from kw_common.alerting_env import _write_marker

    marker = tmp_path / "app" / marker_name("prod")
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting_env"):
        _write_marker(marker, digest)

    assert not marker.exists(), (
        f"a marker was written for {digest!r}, which `_marker_matches` can never match — every "
        f"boot from now on re-validates and re-announces")
    # ⚠️ ASSERTED ON WHAT THE MESSAGE MUST CARRY, not on a phrase. A warning that says something
    # went wrong without naming the file it did not write, or what it was handed, sends the reader
    # to `CONFIG_PATH` to look for a marker that was never the problem.
    assert caplog.text.strip(), "it happened silently"
    assert str(marker) in caplog.text, "the warning does not say which marker was not written"
    assert repr(digest) in caplog.text, "the warning does not say what it was handed"


def test_an_alert_that_never_left_does_not_mark_the_boot_validated(tmp_path: Path) -> None:
    """⭐⭐ THE ORDER, WHICH THE SUCCESS PATH CANNOT DISTINGUISH.

    `validate_boot` sends and then marks. Both orderings look identical when the send works, so
    the whole suite was blind to swapping them — measured: the swap survived every other test.
    The difference only shows when the send does NOT work, and then it is permanent: a marker
    written for an alert that never left suppresses every later boot's alert too, so the service
    goes quiet and stays quiet.
    """
    settings, marker_dir, marker = _validated(tmp_path)

    class Hostile:
        """An `Alerter` whose `notify` raises. `_announce` does not guard the call, deliberately —
        the real `Alerter.notify` is the thing that never raises."""

        def notify(self, *args: object, **kwargs: object) -> dict[str, str]:
            raise RuntimeError("the channel exploded")

    with pytest.raises(RuntimeError):
        validate_boot(settings, "prod", marker_dir, alerter=Hostile())  # type: ignore[arg-type]
    assert not marker.exists(), "an alert that never left must not mark this boot validated"


def test_a_refusal_names_EVERY_missing_key_not_just_the_first(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """An operator with three missing keys must not get three sequential boot failures. Every
    other case here drops exactly ONE key, so reporting only the first survived all of them."""
    config = drop(tmp_path, "EMAIL_TO", "SMTP_HOST", "SMTP_PASSWORD")
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError) as exc:
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    for key in ("EMAIL_TO", "SMTP_HOST", "SMTP_PASSWORD"):
        assert key in str(exc.value)
    # ...and it says which keys are OPTIONAL, so the operator is not left guessing whether the
    # ones it did not name are also required.
    assert "SMTP_USER" in str(exc.value)
    assert silence(channels) == []


def test_a_marker_and_a_config_written_in_the_SAME_TICK_still_skip_the_second_boot(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ THE COARSE-FILESYSTEM DEFECT, PINNED AS GONE RATHER THAN AS FIXED.

    An ext4 built with 128-byte inodes has ONE-SECOND timestamps, which is what several CI
    runners' scratch disks are. Under the old rule both writes landed on the same second, the
    marker could never be strictly newer than the config, and every boot re-validated and re-sent
    the confirmation alert — forever, until somebody edited the file. Measured green on Windows
    (NTFS, 100 ns) and RED on both Linux jobs at the same commit, which is what a mechanism that
    depends on clock granularity does.

    The tie is created deliberately here. With a digest it simply does not matter, and this is the
    test that would go red if any timestamp comparison came back.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    config = Path(settings.config_file)
    stamp = config.stat().st_mtime
    os.utime(marker, (stamp, stamp))
    assert marker.stat().st_mtime == config.stat().st_mtime   # the tie really exists
    reset(channels)

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is False
    assert silence(channels) == []


def test_the_marker_is_written_into_the_apps_own_directory_and_never_the_shared_root(
        tmp_path: Path) -> None:
    """⚠️ The shared root is mounted READ-ONLY. A marker written there fails on every boot in
    production while passing every test that used one writable directory for both."""
    shared = tmp_path / "shared"
    app = tmp_path / "app"
    write_shared(shared)
    settings = load_alert_settings(config_file_for(shared), "prod", "feed-poller")
    validate_boot(settings, "prod", app, shared_root=shared, alerter=Alerter(settings))

    assert (app / marker_name("prod")).is_file()
    assert list(shared.rglob(MARKER_NAME + "*")) == []


@pytest.mark.parametrize("missing_key", ["EMAIL_TO", "SMTP_PASSWORD", "SMTP_PORT",
                                         "NTFY_URL_PROD"])
def test_a_missing_required_key_refuses_to_boot_and_alerts_nobody(
        tmp_path: Path, channels: dict[str, Spy], caplog: pytest.LogCaptureFixture,
        missing_key: str) -> None:
    """⛔ THE REFUSAL MUST NOT REPORT ITSELF THROUGH THE CHANNEL IT IS REFUSING OVER."""
    config = drop(tmp_path, missing_key)
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError) as exc:
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))

    assert missing_key in str(exc.value)
    # ⭐ AND NOT THE OTHERS. `missing_key in message` is equally true of a message that lists
    # EVERY required key, which is what a refusal reduced to "these are the keys prod requires"
    # would say — the operator then loses the whole diagnostic and the test stays green.
    present = [k for k in required_keys("prod") if k != missing_key]
    assert not [k for k in present if k in str(exc.value)], str(exc.value)
    assert silence(channels) == []
    assert not (tmp_path / "app" / marker_name("prod")).exists()


def test_a_blank_required_key_is_refused_the_same_way_as_an_absent_one(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    config = write_shared(tmp_path, EMAIL_TO="   ")
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="EMAIL_TO"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert silence(channels) == []


def test_the_refusal_names_the_keys_and_never_their_values(tmp_path: Path) -> None:
    """One of these keys is the SMTP password, and this message is the line an operator pastes
    into a bug report."""
    secret = "s3cret-app-password"  # noqa: S105 — a fixture value, and the point of the test
    config = write_shared(tmp_path, SMTP_PASSWORD=secret, EMAIL_TO="")
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError) as exc:
        validate_boot(settings, "prod", tmp_path / "app")
    assert "EMAIL_TO" in str(exc.value)
    assert secret not in str(exc.value)
    # ...and the password really is in the file the check just read.
    assert read_config(config)["SMTP_PASSWORD"] == secret


def test_an_unreadable_config_refuses_to_boot_and_alerts_nobody(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    config = config_file_for(tmp_path)
    config.parent.mkdir(parents=True, exist_ok=True)
    settings = AlertSettings(service="feed-poller", config_file=str(config))
    with pytest.raises(AlertEnvError, match="does not exist") as exc:
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert str(config) in str(exc.value)      # which file, not just that one was missing
    assert silence(channels) == []


def test_a_mis_encoded_config_refuses_to_boot_and_alerts_nobody(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    config = config_file_for(tmp_path)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes("EMAIL_TO=ops@example.com\n".encode("utf-16"))
    settings = AlertSettings(service="feed-poller", config_file=str(config))
    with pytest.raises(AlertEnvError, match="UTF-8"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert silence(channels) == []


def test_a_shared_root_that_is_not_mounted_is_reported_as_a_mount_problem(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ A MISSING MOUNT AND A MISSING FILE SEND AN OPERATOR TO DIFFERENT PLACES, so they are
    different messages. One is a template volume that was never added; the other is a file that
    was never created."""
    settings = AlertSettings(service="feed-poller",
                             config_file=str(config_file_for(tmp_path / "absent")))
    with pytest.raises(AlertEnvError, match="MOUNT"):
        validate_boot(settings, "prod", tmp_path / "app",
                      shared_root=tmp_path / "absent", alerter=Alerter(settings))
    assert silence(channels) == []


def test_a_mounted_root_with_no_configs_directory_is_reported_as_a_structure_problem(
        tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    settings = AlertSettings(service="feed-poller", config_file=str(config_file_for(shared)))
    with pytest.raises(AlertEnvError, match="structure"):
        validate_boot(settings, "prod", tmp_path / "app", shared_root=shared)


def test_settings_with_no_config_file_are_refused_rather_than_silently_passing(
        tmp_path: Path) -> None:
    settings = AlertSettings(service="feed-poller")
    with pytest.raises(AlertEnvError, match="config_file"):
        validate_boot(settings, "prod", tmp_path / "app")


def test_validation_without_an_installed_alerter_withholds_the_marker(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """⭐⭐ THE "MARKED VALIDATED, ALERT NEVER LEFT" STATE, APPROACHED FROM THE OTHER SIDE.

    `validate_boot` sends before it marks, precisely so a crash between the two cannot record a
    boot as validated whose confirmation never went out. Writing the marker when there is no
    `Alerter` at all produces exactly that state anyway — and worse, permanently: the marker then
    suppresses every LATER boot's alert too, so the confirmation is lost until somebody edits the
    config file. The configuration is valid, which is what was asked; the proof is what is
    missing, so the marker is withheld and the next boot tries again.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting_env"):
        assert validate_boot(settings, "prod", marker_dir) is True
    assert "no Alerter" in caplog.text
    assert not marker.exists()


def test_the_boot_after_an_alerter_is_installed_does_announce(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """The other half of the test above: withholding the marker has to actually buy something."""
    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir) is True          # no alerter
    assert silence(channels) == []

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert len(channels["ntfy"].calls) == 1
    assert marker.is_file()


def test_an_unwritable_marker_directory_costs_a_warning_and_not_the_boot(
        tmp_path: Path, channels: dict[str, Spy],
        caplog: pytest.LogCaptureFixture) -> None:
    """⭐ THE CONFIGURATION IS VALID AND HAS BEEN ANNOUNCED. Refusing to start over a marker would
    turn a missing read-write volume into an outage of the service itself — the failure direction
    is a duplicate alert per boot, which is noise.

    ⭐⭐ THE FAILURE IS PLANTED, NOT PATCHED, AND THAT IS THE THIRD SPELLING OF THIS TEST. It
    patched `Path.write_bytes` while the marker was empty; when the marker gained content the
    patch silently stopped intercepting and the test asserted a warning nothing was going to log.
    Re-pointing it at `write_text` fixed that instance and left the class: the write is now
    `os.open` + `os.replace`, and a fourth name would have gone the same way. A monkeypatched
    method name is an assertion about HOW the marker is written, and the test is about what
    happens when it CANNOT be. So the condition is real — the marker directory's own parent is a
    regular file, so no directory of that name can exist — and it will keep being real whatever
    the write is made of.
    """
    settings, _, _ = _validated(tmp_path)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file where the marker directory would have to go\n", encoding="utf-8")
    marker_dir = blocked / "app"

    with caplog.at_level(logging.WARNING, logger="kw_common.alerting_env"):
        assert validate_boot(settings, "prod", marker_dir,
                             alerter=Alerter(settings)) is True
    assert CONFIG_PATH_VAR in caplog.text
    assert not marker_dir.exists()
    assert len(channels["ntfy"].calls) == 1


# ============================== #20: the marker is a digest of the file holding the SMTP password
posix_only = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX permission bits. Windows synthesises st_mode from the read-only attribute, so a "
           "mode assertion there measures nothing; CI runs ubuntu-latest, which is also what the "
           "fleet deploys to.")


@posix_only
def test_the_marker_is_created_unreadable_to_anyone_but_the_service(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ #20. THE MARKER'S CONTENT IS A DIGEST OF THE FILE THAT HOLDS `SMTP_PASSWORD`.

    v1.3.0 wrote it 0644 into a shared appdata volume, which makes it an offline verification
    oracle: a party who can guess the rest of that file can confirm a candidate password against
    the digest with no rate limit, no SMTP connection and no log line. The empty marker it
    replaced had nothing to leak, so this was introduced by the fix for #15 — a security property
    traded for a correctness one without noticing the trade.

    Group and other bits are asserted as EXACTLY zero rather than "not world-readable": a marker
    readable by the volume's group is readable by every other container mounting it, which is the
    same oracle with one more step.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert marker.is_file()
    mode = stat.S_IMODE(marker.stat().st_mode)
    # ⚠️ THE PROPERTY, NOT THE NUMBER. `umask` subtracts from a creation mode and never adds to it,
    # so a runner with a hardened umask would produce 0o400 — still correct, and `== 0o600` would
    # call it a regression. What must hold is that nobody else can read it and that the service
    # itself still can, which is what these two assertions say.
    assert mode & 0o077 == 0, f"the marker is readable beyond its owner ({oct(mode)})"
    assert mode & stat.S_IRUSR, "the service could not read back its own marker"


@posix_only
def test_a_marker_an_earlier_version_left_wide_open_is_replaced_not_chmodded(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ THE UPGRADE PATH, AND THE REASON THE WRITE IS `os.replace` RATHER THAN A `chmod`.

    Every service that ran v1.3.0 already has a 0644 marker sitting in its config directory. A
    `chmod` is the obvious way to narrow it and it is the wrong one: on a uid-mismatched bind
    mount — the ordinary case for host-managed appdata, and the whole of #21 — that call fails,
    and before #21 it failed silently. Creating a NEW file 0600 and renaming it over the old one
    needs only the directory to be writable, which it must already be or there would be no marker.

    The inode is what makes this a test of the mechanism rather than of the outcome: a `chmod` in
    place would satisfy the mode assertion and fail the identity one.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")          # the shape v1.2.0 wrote
    os.chmod(marker, 0o644)
    before = marker.stat().st_ino

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert stat.S_IMODE(marker.stat().st_mode) & 0o077 == 0
    assert marker.stat().st_ino != before, "the marker was chmodded in place, not replaced"
    assert marker.read_text(encoding="utf-8").startswith("sha256:")


def test_the_marker_is_replaced_rather_than_written_in_place(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE MECHANISM BEHIND #20's FIX, ASKED WHERE PERMISSION BITS ARE NOT AVAILABLE.

    Rewriting a file keeps its identity; creating a new one and renaming it over the old one does
    not. That identity is exactly what makes the narrowing survive a mount where a `chmod` would
    be refused — the new file carries the mode it was created with, and the rename only needs the
    directory. It is observable on any filesystem that numbers its files, so the mechanism can be
    pinned here rather than only in the POSIX-gated mode assertions that CI runs.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"sha256:{'0' * 64}\n", encoding="utf-8")   # a stale, non-matching digest
    before = marker.stat().st_ino
    if not before:
        pytest.skip("this filesystem does not number its files, so identity cannot be observed")

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert marker.stat().st_ino != before, "the marker was written in place, not replaced"


def test_an_already_restricted_marker_is_not_rewritten_on_every_boot(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """The other half of the upgrade repair: it has to stop. A rewrite on every skip would put a
    write into the boot path of every service forever to fix a state that only exists once."""
    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    before = marker.stat().st_ino
    if not before:
        pytest.skip("this filesystem does not number its files, so identity cannot be observed")

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is False
    assert marker.stat().st_ino == before, "a marker that was already restricted was rewritten"


def test_exposed_bits_reads_the_group_and_other_permissions(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐ THE PLATFORM GATE, ASSERTED IN BOTH DIRECTIONS ON WHICHEVER PLATFORM RUNS IT.

    The helper answers `0` off POSIX on purpose — Windows synthesises `st_mode` from the read-only
    attribute, so every ordinary file reads as 0o666 and a check that trusted it would warn about
    an exposure on every single write while the real access control went unexamined. A guard that
    fires on a correct system is one an operator switches off.

    Both directions are measured against the SAME file, so this cannot pass by the file happening
    to have no group or other bits: the only thing that differs between the two assertions is the
    platform the helper thinks it is on.
    """
    path = tmp_path / "marker"
    path.write_text("x", encoding="utf-8")

    monkeypatch.setattr(os, "name", "posix")
    assert alerting_env._exposed_bits(path) != 0, (
        "the premise failed: this file has no group or other permission bits to find")

    monkeypatch.setattr(os, "name", "nt")
    assert alerting_env._exposed_bits(path) == 0


def test_a_marker_reported_exposed_is_kept_warned_about_and_still_suppresses(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """⭐ THE `KEEP IT` DECISION, ASKED ON EVERY PLATFORM. Deleting a marker whose mode could not
    be applied would fail open into re-validating and re-announcing on every boot — a duplicate
    confirmation per service per restart, which trains an operator to ignore the one message this
    whole mechanism exists to send. So the exposure is reported and the marker stays.

    `_exposed_bits` is stubbed rather than the filesystem coerced, because what is under test here
    is the CALLER's decision. What the helper itself answers is measured one test up, and the
    unstubbed end-to-end version runs on POSIX in CI.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    monkeypatch.setattr(alerting_env, "_exposed_bits", lambda _path: 0o044)

    with caplog.at_level(logging.WARNING, logger="kw_common.alerting_env"):
        assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert marker.is_file(), "the marker was deleted, which fails open into permanent revalidation"
    assert str(marker) in caplog.text
    contents = marker.read_text(encoding="utf-8")

    reset(channels)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is False
    assert silence(channels) == []
    assert marker.read_text(encoding="utf-8") == contents


def test_the_upgrade_repair_rewrites_the_marker_without_revalidating(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐⭐ THE SKIP PATH REWRITE, ASKED ON EVERY PLATFORM (the POSIX end-to-end twin is below).

    A marker that MATCHES never reaches the write, so the repair has to happen where the skip
    happens. The identity change is what proves it ran; the unchanged contents and the silent
    channels are what prove it cost nothing.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    contents = marker.read_text(encoding="utf-8")
    before = marker.stat().st_ino
    if not before:
        pytest.skip("this filesystem does not number its files, so identity cannot be observed")
    reset(channels)

    monkeypatch.setattr(alerting_env, "_exposed_bits", lambda _path: 0o044)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is False
    assert marker.stat().st_ino != before, "the wide-open marker was left exactly as it was"
    assert marker.read_text(encoding="utf-8") == contents
    assert silence(channels) == []


@posix_only
def test_a_matching_marker_left_wide_open_by_1_3_0_is_narrowed_without_revalidating(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ THE UPGRADE PATH THAT THE WRITE PATH ALONE DOES NOT REACH, AND THE ONE EVERY DEPLOYED
    SERVICE TAKES.

    A service already running the previous release has a marker recording the CORRECT digest at
    the wrong mode. So it matches, validation skips, and `_write_marker` is never called —
    narrowing only the write would have fixed new installations and left every existing one
    exposed until somebody happened to edit the shared config. Fixing the instance, not the class.

    The other half of the assertion is that the repair is free: the marker's contents are
    unchanged, the boot still returns `False`, and no confirmation alert is sent. A repair that
    re-validated would page every service in the fleet once on upgrade.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    contents = marker.read_text(encoding="utf-8")
    os.chmod(marker, 0o644)
    reset(channels)

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is False
    assert stat.S_IMODE(marker.stat().st_mode) & 0o077 == 0
    assert marker.read_text(encoding="utf-8") == contents
    assert silence(channels) == []


def test_the_restricted_marker_is_still_the_thing_that_suppresses_the_next_boot(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE OBVIOUS WRONG FIX, ASKED DIRECTLY. A marker the service itself cannot read back
    would fail open into re-validating and re-announcing on every boot — a duplicate alert per
    service per restart, which trains an operator to ignore the one message this mechanism exists
    to send. So the narrowing has to leave the skip working, and that is asserted rather than
    assumed from the mode."""
    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    reset(channels)

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is False
    assert silence(channels) == []
    expected = hashlib.sha256(Path(settings.config_file).read_bytes()).hexdigest()
    assert marker.read_text(encoding="utf-8").strip() == f"sha256:{expected}"


def test_a_successful_marker_write_says_nothing(
        tmp_path: Path, channels: dict[str, Spy], caplog: pytest.LogCaptureFixture) -> None:
    """The direction that decides whether the warnings below are worth anything. A boot that went
    correctly must produce no warning at all, or the ones that matter get filtered out."""
    settings, marker_dir, _ = _validated(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="kw_common.alerting_env"):
        assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_stale_temporary_file_is_not_reused_as_the_marker(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE TEMPORARY FILE IS REMOVED BEFORE IT IS CREATED, AND `O_EXCL` IS WHY THAT MATTERS.

    A process killed between the write and the rename leaves a temp behind. Opening that with
    `O_TRUNC` would reuse ITS mode — so a stale 0644 temp would be renamed into place as the
    marker, with 0600 asked for and never applied: the same defect one layer down, and invisible.
    Removing it first and demanding `O_EXCL` makes that impossible instead of unlikely.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    marker_dir.mkdir(parents=True, exist_ok=True)
    stale = marker_dir / f"{marker.name}.tmp"
    stale.write_text(f"sha256:{'0' * 64}\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(stale, 0o644)

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    expected = hashlib.sha256(Path(settings.config_file).read_bytes()).hexdigest()
    assert marker.read_text(encoding="utf-8").strip() == f"sha256:{expected}"
    assert list(marker_dir.glob("*.tmp")) == [], "a temporary file survived the write"
    if os.name == "posix":
        assert stat.S_IMODE(marker.stat().st_mode) & 0o077 == 0


@posix_only
def test_a_marker_that_lands_readable_anyway_is_reported_and_kept(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """⭐ WHERE THE MODE CANNOT BE APPLIED THE MARKER IS KEPT, AND THE EXPOSURE IS STATED.

    A filesystem that does not model these bits — vfat, most CIFS mounts — hands back whatever its
    mount options say regardless of what was asked for. Deleting the marker there would fail open
    into re-validating and re-announcing forever, so it warns and keeps it: on such a mount
    `alerting.env` itself is equally readable and holds the password in the clear, so the marker
    is not the weak link. Simulated by forcing the creation mode wide, which is exactly the
    outcome those mounts produce.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    real_open = os.open

    def wide(path: object, flags: int, mode: int = 0o777, **kwargs: object) -> int:
        # Only the CREATE is widened. `alerting_env.os` IS the process-wide `os`, so a wrapper
        # that rewrote the mode on every call would reach far past the one write under test.
        if flags & os.O_CREAT:
            mode = 0o644
        return real_open(path, flags, mode, **kwargs)   # type: ignore[arg-type]

    # ⚠️ `umask` SUBTRACTS from a creation mode and never adds to it, so on a runner whose umask is
    # 0o077 the widened mode would come back 0o600 and this test would quietly stop exercising the
    # branch it is named for — passing, while proving nothing. Zeroed here and restored after.
    previous_umask = os.umask(0)
    monkeypatch.setattr(alerting_env.os, "open", wide)
    try:
        with caplog.at_level(logging.WARNING, logger="kw_common.alerting_env"):
            assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    finally:
        os.umask(previous_umask)

    assert marker.is_file(), "the marker was deleted, which fails open into permanent revalidation"
    assert str(marker) in caplog.text
    assert stat.S_IMODE(marker.stat().st_mode) & 0o077, "the premise of this test no longer holds"
    reset(channels)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is False
    assert silence(channels) == []


# =========================== a missing MOUNT and a missing FILE go to different places (#3b claim)
def test_a_missing_config_in_a_directory_that_exists_blames_the_file(tmp_path: Path) -> None:
    """The file was deleted, mistyped, or never created — the volume is fine and the message must
    not send an operator to the template's volume list."""
    path = write_shared(tmp_path)
    path.unlink()
    with pytest.raises(AlertEnvError) as excinfo:
        read_config(path)
    message = str(excinfo.value)
    assert str(path.parent) in message
    assert "MOUNT" not in message


def test_a_missing_config_whose_directory_is_absent_blames_the_mount(tmp_path: Path) -> None:
    """⭐⭐ THE CLAIM THE FIRST ADOPTER MEASURED FALSE. The standard says a missing MOUNT and a
    missing FILE send an operator to different places, and `validate_boot._check_layout` does draw
    that distinction — but on the sequence the setup document prescribes it never runs, because
    the loader reads the config BEFORE validation does anything. Both cases arrived as one
    undifferentiated "does not exist". Now the reader itself answers it."""
    path = config_file_for(tmp_path / "never-mounted")
    with pytest.raises(AlertEnvError) as excinfo:
        read_config(path)
    message = str(excinfo.value)
    assert "MOUNT" in message
    assert CONFIG_RELPATH in message


def test_the_mount_diagnosis_is_reachable_through_the_prescribed_loader(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐ ASKED THROUGH THE DOCUMENTED CALL SEQUENCE, WHICH IS WHERE IT WAS UNREACHABLE.

    `read_config` having the right answer is not the claim; an operator whose volume is missing
    seeing it is. So this goes in the front door — the three variables and
    `load_alert_settings_from_env` — rather than calling the reader directly.
    """
    monkeypatch.setenv(SHARED_ROOT_VAR, str(tmp_path / "never-mounted"))
    monkeypatch.setenv(CONFIG_PATH_VAR, str(tmp_path / "app"))
    monkeypatch.setenv(DEPLOY_ENV_VAR, "prod")
    with pytest.raises(AlertEnvError) as excinfo:
        load_alert_settings_from_env("feed-poller")
    assert "MOUNT" in str(excinfo.value)


def test_the_prescribed_loader_still_blames_the_file_when_the_mount_is_there(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction, and the one that makes the test above mean something: a diagnosis that
    said MOUNT for every missing file would be no more use than the one it replaced."""
    config_file_for(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(SHARED_ROOT_VAR, str(tmp_path))
    monkeypatch.setenv(CONFIG_PATH_VAR, str(tmp_path / "app"))
    monkeypatch.setenv(DEPLOY_ENV_VAR, "prod")
    with pytest.raises(AlertEnvError) as excinfo:
        load_alert_settings_from_env("feed-poller")
    assert "MOUNT" not in str(excinfo.value)


def test_the_validation_alert_names_no_value_from_the_config_file(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⛔ THIS MESSAGE GOES TO EMAIL AND TO A PUSH TOPIC, and the file it describes holds an SMTP
    password. It says which KEYS were used, never what they hold."""
    secret = "s3cret-app-password"  # noqa: S105 — a fixture value, and the point of the test
    write_shared(tmp_path, SMTP_PASSWORD=secret)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))

    _, _, title, message = channels["ntfy"].calls[0]
    body = f"{title} {message}"
    for value in GOOD_CONFIG.values():
        assert value not in body
    assert secret not in body
    assert "feed-poller" in body and "prod" in body


def test_the_validation_alert_says_which_kind_of_topic_the_service_is_on(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    write_shared(tmp_path, NTFY_URL_FEED_POLLER="https://ntfy.example.com/feed-poller")
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert "its own ntfy topic" in channels["ntfy"].calls[0][3]


def test_validate_boot_from_env_reads_all_three_variables(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channels: dict[str, Spy]) -> None:
    shared = tmp_path / "shared"
    app = tmp_path / "app"
    write_shared(shared)
    monkeypatch.setenv(SHARED_ROOT_VAR, str(shared))
    monkeypatch.setenv(CONFIG_PATH_VAR, str(app))
    monkeypatch.setenv(DEPLOY_ENV_VAR, "PROD")

    settings = load_alert_settings_from_env("feed-poller")
    assert validate_boot_from_env(settings, alerter=Alerter(settings)) is True
    assert (app / marker_name("prod")).is_file()
    assert len(channels["ntfy"].calls) == 1


@pytest.mark.parametrize("missing", [SHARED_ROOT_VAR, CONFIG_PATH_VAR, DEPLOY_ENV_VAR])
def test_validate_boot_from_env_refuses_a_missing_variable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channels: dict[str, Spy],
        missing: str) -> None:
    shared = tmp_path / "shared"
    write_shared(shared)
    settings = load_alert_settings(config_file_for(shared), "prod", "feed-poller")
    for name, value in ((SHARED_ROOT_VAR, str(shared)),
                        (CONFIG_PATH_VAR, str(tmp_path / "app")),
                        (DEPLOY_ENV_VAR, "prod")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)

    with pytest.raises(AlertEnvError) as exc:
        validate_boot_from_env(settings)
    assert str(exc.value).startswith(missing), str(exc.value)   # see the loader test above
    assert silence(channels) == []


# ==================================================== what round 1 of the gate found, pinned
def test_promoting_a_service_between_environments_RE_VALIDATES(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ THE DEFECT A SINGLE MARKER HAD, AND IT IS THE ONE THIS PACKAGE EXISTS TO PREVENT.

    Promoting a service dev → prod does not touch the fleet-shared file, so with ONE marker for
    every environment the marker still outranked the config and validation NEVER RAN in the new
    environment: `NTFY_URL_PROD` was never looked at, and the service came up with an empty topic
    URL and did not refuse. "A silent divergence discovered when an alert does not arrive" is the
    module docstring's own description of what it is for.

    Here the prod topic is ABSENT, so a boot that really validates must REFUSE.
    """
    config = drop(tmp_path, "NTFY_URL_PROD")
    marker_dir = tmp_path / "app"
    dev = load_alert_settings(config, "dev", "feed-poller")
    assert validate_boot(dev, "dev", marker_dir, alerter=Alerter(dev)) is True

    prod = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="NTFY_URL_PROD"):
        validate_boot(prod, "prod", marker_dir, alerter=Alerter(prod))


def test_each_environment_keeps_its_own_marker(tmp_path: Path) -> None:
    settings, marker_dir, _ = _validated(tmp_path, env="prod")
    validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings))
    dev = load_alert_settings(config_file_for(tmp_path), "dev", "feed-poller")
    validate_boot(dev, "dev", marker_dir, alerter=Alerter(dev))

    assert (marker_dir / marker_name("prod")).is_file()
    assert (marker_dir / marker_name("dev")).is_file()
    assert marker_name("prod") != marker_name("dev")


def test_a_service_named_after_an_environment_is_refused() -> None:
    """⭐ IT WOULD DERIVE A RESERVED KEY. A service literally named `prod` reads `NTFY_URL_PROD` —
    the shared topic every file carries — as its own dedicated override: the wrong topic, no title
    prefix, and a validation alert announcing it is "on its own ntfy topic"."""
    for name in ("dev", "prod", "PROD", " Dev "):
        with pytest.raises(AlertEnvError, match="environment"):
            ntfy_key(name)


@pytest.mark.parametrize("key", ["NTFY_URL_PROD", "SMTP_PASSWORD", "EMAIL_TO"])
def test_a_key_left_at_the_CHANGE_ME_placeholder_refuses_to_boot(
        tmp_path: Path, channels: dict[str, Spy], key: str) -> None:
    """⭐ THE TEMPLATE ASSERTS THIS, so it has to be true. Before it was, every placeholder was
    non-blank, so the untouched template validated, announced that the configuration "checks out",
    and wrote the marker — after which no later boot re-validated either."""
    config = write_shared(tmp_path, **{key: "CHANGE-ME"})
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match=key):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert silence(channels) == []


def test_the_shipped_template_AS_IS_refuses_to_boot(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐⭐ THE CLAIM PINNED TO THE FILE THAT MAKES IT, rather than to a value this test invented.

    `alerting.env.template` tells an operator that a file left as-is will refuse to boot. This
    copies the SHIPPED template to where the setup document puts it and runs the sequence the
    setup document gives, so the template and the check cannot drift apart: change either and this
    fails.
    """
    config = config_file_for(tmp_path)
    config.parent.mkdir(parents=True)
    config.write_bytes(TEMPLATE.read_bytes())

    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="CHANGE-ME"):
        validate_boot(settings, "prod", tmp_path / "app", shared_root=tmp_path,
                      alerter=Alerter(settings))
    assert silence(channels) == []


@pytest.mark.parametrize("bad_url", ["bare-topic", "http://ntfy.example.com/t",
                                     "https://u:pw@ntfy.example.com/t"])
def test_a_present_but_unusable_ntfy_url_refuses_to_boot(
        tmp_path: Path, channels: dict[str, Spy], bad_url: str) -> None:
    """⭐ NON-BLANK IS NOT USABLE, and a bare topic is the likeliest operator typo of the lot —
    `urllib` cannot post to it, so the channel is dead while looking configured. Boot used to pass
    it AND announce that the configuration checks out."""
    config = write_shared(tmp_path, NTFY_URL_PROD=bad_url)
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="ntfy"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert silence(channels) == []


@pytest.mark.parametrize("port", ["notanumber", "0", "70000", "-1"])
def test_an_unusable_smtp_port_refuses_to_boot(tmp_path: Path, port: str) -> None:
    config = write_shared(tmp_path, SMTP_PORT=port)
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="SMTP_PORT"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))


def test_an_email_from_that_is_not_a_bare_mailbox_refuses_to_boot_rather_than_looking_ready(
        tmp_path: Path) -> None:
    """⭐⭐ THE REGRESSION THE `SMTP_USER` DEFAULT INTRODUCED, PINNED AT BOOT.

    `EMAIL_FROM` is a mail HEADER and may read `Alerts <box@host>`; `SMTP_USER` is an AUTH
    IDENTITY and must be a bare ASCII mailbox. Copying the first into the second turned an honest
    "email alerts DISABLED" into `email_ready() == True` for a channel that failed 100% of sends.
    The default now declines, so the channel reports itself unusable — and boot refuses.
    """
    config = drop(tmp_path, "SMTP_USER")
    config.write_text(config.read_text(encoding="utf-8").replace(
        "EMAIL_FROM=svc@example.com", "EMAIL_FROM=Service Alerts <svc@example.com>"),
        encoding="utf-8")
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="email"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))


def test_every_refusal_LOGS_before_it_raises(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """⭐ "LOG LOUDLY AND REFUSE TO BOOT" IS THE STANDARD'S WORDING AND BOTH HALVES ARE REQUIRED.

    The raising half was true from the start and the logging half was not: the layout, key and
    read checks all raised having emitted nothing at all, while the README, the CHANGELOG and this
    module's own docstrings said "logs and raises". An adopter that catches `AlertEnvError` to
    print its own one-liner then had no record of WHICH check failed.
    """
    cases = {
        "missing mount": lambda: validate_boot(
            AlertSettings(service="svc",
                          config_file=str(config_file_for(tmp_path / "absent"))),
            "prod", tmp_path / "app", shared_root=tmp_path / "absent"),
        "missing file": lambda: validate_boot(
            AlertSettings(service="svc", config_file=str(_touchdir(tmp_path / "b"))),
            "prod", tmp_path / "app"),
        "missing key": lambda: validate_boot(
            load_alert_settings(drop(tmp_path / "c", "EMAIL_TO"), "prod", "svc"),
            "prod", tmp_path / "app"),
        "placeholder": lambda: validate_boot(
            load_alert_settings(_placeholder_config(tmp_path / "d"), "prod", "svc"),
            "prod", tmp_path / "app"),
        "unusable topic": lambda: validate_boot(
            load_alert_settings(write_shared(tmp_path / "e", NTFY_URL_PROD="bare"),
                                "prod", "svc"),
            "prod", tmp_path / "app"),
    }
    for label, run in cases.items():
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="kw_common.alerting_env"), \
                pytest.raises(AlertEnvError):
            run()
        assert caplog.records, f"the {label} refusal logged nothing before raising"


def _placeholder_config(root: Path) -> Path:
    """A config whose password is still the shipped template's marker."""
    return write_shared(root, SMTP_PASSWORD="CHANGE-ME")  # noqa: S106 — a marker, not a secret


def _touchdir(root: Path) -> Path:
    """A config path whose PARENT exists and whose file does not."""
    path = config_file_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_the_prefix_uses_the_same_service_name_the_settings_carry(tmp_path: Path) -> None:
    """⭐ ONE NORMALISATION, NOT TWO. `AlertSettings` strips the service name in its constructor,
    so building the prefix from the RAW argument gave two spellings of one service — and the
    prefix is the one that reaches an HTTP header and a mail Subject, where a newline kills every
    channel while the settings object looks clean."""
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "  feed-poller \n")
    assert settings.service == "feed-poller"
    assert settings.title_prefix == "[prod][feed-poller] "


def test_a_control_character_in_the_prefix_is_refused_by_the_constructor() -> None:
    """It becomes an HTTP header value and a mail Subject, both of which reject a CR or LF — so
    such a prefix does not corrupt a header, it fails EVERY alert on EVERY channel while the boot
    report still says both are ready."""
    for bad in ("[prod][a\nb] ", "[prod][a\rb] ", "[prod][a\x00b] "):
        with pytest.raises(ValueError, match="control character"):
            AlertSettings(service="svc", title_prefix=bad)


def test_a_hostile_title_cannot_take_down_a_channel_it_never_reached(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE PER-CHANNEL CONTAINMENT INVARIANT, WHICH BUILDING THE PREFIXED TITLE ABOVE THE LOOP
    BROKE. An f-string calls `format()` on the caller's title, and a caller can pass a non-string;
    built once for both channels, a title whose `__format__` raises reported `failed` for a
    channel that was never even attempted."""
    class Hostile:
        """Only `__format__` raises, which isolates the defect exactly.

        `notify()`'s log line uses lazy `%s` formatting on purpose — that is what keeps a hostile
        `__str__` inside `logging` — so a stand-in that broke BOTH would fail for a second reason
        and prove nothing about the f-string that was hoisted above the channel loop.
        """

        def __format__(self, spec: str) -> str:
            raise RuntimeError("no format")

        def __str__(self) -> str:
            return "hostile"

    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "feed-poller")
    from dataclasses import replace
    # ntfy unconfigured, so it must report "skipped" — never "failed".
    settings = replace(settings, ntfy_url="")
    results = Alerter(settings).notify(ERROR, Hostile(), "body")  # type: ignore[arg-type]
    assert results["ntfy"] == "skipped"


# ============================ what the CONFIRMING pass found IN the round-one fixes, pinned
@pytest.mark.parametrize("password", ["CHANGE-MEMORABLE", "change-mesmerising", "CHANGEMENOW",
                                      "CHANGE", "CHANGE-ME-NOT!x9"])
def test_a_real_value_that_merely_BEGINS_like_the_placeholder_still_boots(
        tmp_path: Path, password: str) -> None:
    """⭐⭐ A FALSE REFUSAL IS THE WORSE ERROR, and the first version of the placeholder check made
    one: a bare `startswith("CHANGE-ME")` refused `CHANGE-MEMORABLE` as an unfilled template.

    A boot check that refuses a CORRECT configuration is the kind that gets switched off, and then
    net safety goes DOWN — so the boundary is the hyphen the template actually uses.

    ⚠️ `CHANGE-ME-NOT!x9` is here deliberately and IS refused nowhere: it begins `CHANGE-ME-`, so
    it is caught. That is the accepted cost of the template spelling its markers
    `CHANGE-ME-to-your-...`, and it is a password nobody has.
    """
    if password == "CHANGE-ME-NOT!x9":  # noqa: S105 — a fixture value, and the point of the test
        pytest.skip("documented in this test's docstring as the accepted false positive")
    config = write_shared(tmp_path, SMTP_PASSWORD=password)
    settings = load_alert_settings(config, "prod", "feed-poller")
    assert validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings)) is True


def test_the_exact_placeholder_and_its_hyphenated_forms_are_still_refused(tmp_path: Path) -> None:
    """The other direction, so the loosened boundary did not loosen it away entirely."""
    for value in ("CHANGE-ME", "change-me", "  CHANGE-ME  ", "CHANGE-ME-to-your-ops-address"):
        config = write_shared(tmp_path, EMAIL_TO=value)
        settings = load_alert_settings(config, "prod", "feed-poller")
        with pytest.raises(AlertEnvError, match="EMAIL_TO"):
            validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))


def test_a_control_character_in_the_service_name_raises_AlertEnvError_not_ValueError(
        tmp_path: Path) -> None:
    """⛔ THE EXCEPTION TYPE IS PART OF THE CONTRACT. The setup document tells an adopter to let
    `AlertEnvError` stop the process; a bare `ValueError` from the settings constructor walks
    straight through that handler. `.strip()` removes only LEADING and TRAILING whitespace, so an
    INTERIOR control character reached the title prefix and was refused one layer too late."""
    write_shared(tmp_path)
    for name in ("feed\tpoller", "feed\x01poller", "svc\x7f", "a\nb"):
        with pytest.raises(AlertEnvError, match="control character"):
            load_alert_settings(config_file_for(tmp_path), "prod", name)


def test_a_service_name_with_SURROUNDING_whitespace_is_still_fine(tmp_path: Path) -> None:
    """The false refusal the first version of that check made: a trailing newline is whitespace
    `.strip()` handles, and refusing it would refuse a name that was never a problem."""
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "  feed-poller\n")
    assert settings.service == "feed-poller"


@pytest.mark.parametrize("value", ["al\x00erts@example.com", "alerts\x01@example.com",
                                   "alerts@exa\x7fmple.com"])
def test_a_control_character_stops_EMAIL_FROM_standing_in_for_SMTP_USER(
        tmp_path: Path, value: str) -> None:
    """⭐ `_is_bare_mailbox` promises "anything it does not recognise is a NO", and an embedded NUL
    walked past every check: `.strip()` removes only whitespace, so it was copied into `SMTP_USER`
    and `email_ready()` reported True for a channel that cannot authenticate — the exact "dead
    while looking configured" direction that predicate exists to close."""
    assert alerting._is_bare_mailbox(value) is False
    config = drop(tmp_path, "SMTP_USER")
    config.write_text(
        config.read_text(encoding="utf-8").replace("EMAIL_FROM=svc@example.com",
                                                   f"EMAIL_FROM={value}"), encoding="utf-8")
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="email"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))


@pytest.mark.parametrize("mailbox", ["ops+alerts@example.com", "a@b", "ops@example.technology",
                                     "o'brien@example.com", "a.b_c-d@sub.example.com"])
def test_a_legitimate_bare_mailbox_still_stands_in(mailbox: str) -> None:
    """The restrictive direction, so tightening the predicate did not break the case it exists
    for: `+`-addressing, a long TLD, an apostrophe and a subdomain are all real mailboxes."""
    assert alerting._is_bare_mailbox(mailbox) is True


def test_a_prefix_ntfys_header_cannot_carry_refuses_to_boot_only_when_ntfy_is_configured(
        tmp_path: Path) -> None:
    """⭐ SCOPED TO THE CHANNEL IT BREAKS. `http.client` encodes a header value LATIN-1, so a
    currency sign in the prefix fails every ntfy send while email is perfectly fine — which is why
    this cannot live in `AlertSettings.__post_init__`, where it would reject a legitimate
    non-ASCII prefix for an email-only deployment."""
    from dataclasses import replace

    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "poller-€")
    assert settings.title_prefix == "[prod][poller-€] "     # accepted by the constructor
    with pytest.raises(AlertEnvError, match="latin-1"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))

    # ...and with no ntfy topic the same prefix is fine, because nothing would fail.
    email_only = replace(settings, ntfy_url="")
    assert validate_boot(email_only, "prod", tmp_path / "app2",
                         alerter=Alerter(email_only)) is True


def test_a_port_only_fault_does_not_claim_the_alerts_would_go_nowhere(tmp_path: Path) -> None:
    """⚠️ CLAIM-INFLATION IN A REFUSAL. `smtp_port()` falls back to the documented default and
    `email_ready()` deliberately does not consult the port, so a port fault alone would still have
    delivered on BOTH channels. The refusal stays — a setting stated wrongly is fixed at boot —
    but the reason has to be true."""
    config = write_shared(tmp_path, SMTP_PORT="0")
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError) as exc:
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert "SMTP_PORT" in str(exc.value)
    assert "go nowhere" not in str(exc.value)

    # ...while a fault that really does silence a channel still says so.
    config = write_shared(tmp_path, NTFY_URL_PROD="bare-topic")
    settings = load_alert_settings(config, "prod", "feed-poller")
    with pytest.raises(AlertEnvError, match="go nowhere"):
        validate_boot(settings, "prod", tmp_path / "app3", alerter=Alerter(settings))


def _stated_mappings(text: str) -> list[tuple[str, str]]:
    """Every `service -> NTFY_URL_KEY` mapping a document states, in either form it uses.

    The template writes them as an arrow table; the prose files write them as "`x` is `NTFY_URL_X`".
    Both are read, because a mapping is a mapping wherever an operator finds it.
    """
    import re as _re

    arrow = _re.findall(r'service "([^"]+)"\s*->\s*(NTFY_URL_[A-Z0-9_]+)', text)
    prose = _re.findall(r"`([^`\s]+)`\s+(?:is|derives)\s+`?(NTFY_URL_[A-Z0-9_]+)`?", text)
    return [*arrow, *prose]


def test_EVERY_transform_mapping_this_package_publishes_is_checked_against_the_code() -> None:
    """⭐⭐ THE DOCUMENTATION DEFECT THAT NOTHING COULD CATCH, TURNED INTO SOMETHING THAT CAN.

    The `NTFY_URL_<SERVICE>` rule is stated in four places, and a correction reached ONE of them:
    the others went on describing a PER-CHARACTER substitution while the code collapses a RUN and
    trims the ends. Every example published at the time fell in the set where the two agree, so it
    read plausibly — and an operator with a doubled or trailing separator would have written a key
    the loader never looks up and landed on the shared topic, with a prefix, silently. That is the
    exact failure the sentence itself warns about, produced by the sentence.

    ⚠️ SCOPE, STATED HONESTLY: prose cannot be tested and this does not try. It checks every
    MAPPING — the concrete `x -> KEY` claims an operator actually copies — in every file this
    package ships, which is the part that is machine-checkable. A first version read the template
    ALONE, and the whole finding was that fixing one file is not fixing the class.
    """
    import re as _re

    sources = {
        "alerting.env.template": TEMPLATE.read_text(encoding="utf-8"),
        "docs/alerting-setup.md": SETUP_DOC.read_text(encoding="utf-8"),
        "CHANGELOG.md": (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        "README.md": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "src/kw_common/alerting_env.py": MODULE_PATH.read_text(encoding="utf-8"),
    }
    checked = 0
    for name, text in sources.items():
        for service, key in _stated_mappings(text):
            checked += 1
            # ⚠️ A MAPPING FOR A RESERVED NAME IS A DIFFERENT CLAIM. The documents say `prod`
            # "derives NTFY_URL_PROD" while EXPLAINING WHY such a name is refused — it is the
            # reason for the rule, not a key anyone should write. Asserting equality there would
            # have failed on correct prose; asserting the REFUSAL turns the false positive into
            # coverage of the other half of the same rule.
            if service.strip().lower() in ENVIRONMENTS:
                with pytest.raises(AlertEnvError, match="environment"):
                    ntfy_key(service)
                continue
            assert ntfy_key(service) == key, (
                f"{name} tells a reader that {service!r} is {key}, but the loader looks up "
                f"{ntfy_key(service)} — so a service named that would land on the SHARED topic "
                f"and never be found")
    assert checked >= 6, (
        f"only {checked} stated mapping(s) were found across {len(sources)} files — the "
        f"extraction has stopped matching how these documents write them, which would make this "
        f"test pass over anything")

    # ⭐ AND THE TEMPLATE'S EXAMPLES MUST DISTINGUISH THE RIGHT RULE FROM THE WRONG ONE. Every
    # example published before this fell in the set where per-character and per-run substitution
    # AGREE, which is exactly why the wrong wording read plausible for two rounds. Compute what the
    # wrong rule would produce and require the template to carry a case where the two differ.
    def per_character(service: str) -> str:
        return "NTFY_URL_" + _re.sub(r"[^A-Za-z0-9]", "_", service).upper()

    template_examples = _stated_mappings(sources["alerting.env.template"])
    assert len(template_examples) >= 4, (
        f"the template shows {len(template_examples)} transform example(s); it needs enough to "
        f"cover a plain name, a hyphen, a RUN of separators and a trailing one")
    assert [s for s, _ in template_examples if ntfy_key(s) != per_character(s)], (
        "no template example separates the per-RUN rule from the per-CHARACTER one, so this test "
        "would have passed on the wording that was wrong. Add a name with a doubled or trailing "
        "separator.")


def test_every_document_that_describes_the_MARKER_describes_the_mechanism_the_code_USES(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ THE SAME SHAPE AS THE TRANSFORM TEST ABOVE, AIMED AT THE OTHER CLAIM (#15).

    The marker's mechanism is stated in four shipped files, and it just CHANGED — from an empty
    file compared by modification time to a recorded digest. A document that still describes the
    old one sends an operator to the wrong remedy: `touch` the config after a restore was the
    advice, and under the old mechanism it was the ONLY thing that worked. Correcting three of the
    four files and missing the fourth is this repository's recurring miss, so the question is asked
    of every file rather than of the one that was edited.

    ⚠️ Prose is not tested here, and this does not pretend to. It pins the one machine-checkable
    fact — that what the code WRITES is what the documents SAY it writes — by producing a real
    marker and requiring every document that discusses one to name that mechanism.
    """
    import re as _re

    settings, marker_dir, marker = _validated(tmp_path)
    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True
    written = marker.read_text(encoding="utf-8").strip()
    assert _re.fullmatch(r"sha256:[0-9a-f]{64}", written), (
        f"the marker's content is {written!r}, which is not the `sha256:<hex>` line every "
        f"document below tells a reader to expect")
    assert marker.name == f"{MARKER_NAME}-prod", (
        f"the marker is written as {marker.name!r}; the documents name "
        f"`{MARKER_NAME}-<env>`")

    sources = {
        "docs/alerting-setup.md": SETUP_DOC.read_text(encoding="utf-8"),
        "CHANGELOG.md": (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        "README.md": (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        "src/kw_common/alerting_env.py": MODULE_PATH.read_text(encoding="utf-8"),
    }
    describing = {name: text for name, text in sources.items()
                  if "marker" in text.lower()}
    assert len(describing) == len(sources), (
        f"only {sorted(describing)} still discuss the marker — this test would pass over the rest")
    for name, text in describing.items():
        assert "sha256" in text.lower(), (
            f"{name} describes the boot marker without naming the mechanism the code actually "
            f"uses. It was a timestamp comparison until 1.3.0, and a document still saying so "
            f"tells an operator to `touch` a file that no longer needs touching.")


# ============================================================ the package's own boundaries
def test_importing_alerting_env_does_not_drag_in_the_leak_guard() -> None:
    """Contract rule 5: modules are independently importable. `alerting_env` needs `alerting` —
    it returns an `AlertSettings` — and must need nothing else in the package."""
    script = (
        "import sys; import kw_common.alerting_env;"
        "loaded = sorted(m for m in sys.modules if m.startswith('kw_common'));"
        "print(loaded)"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         encoding="utf-8", errors="replace", check=True,
                         cwd=str(REPO_ROOT))
    assert "leakguard" not in out.stdout, out.stdout
    assert "alerting_env" in out.stdout


def test_every_exported_name_exists() -> None:
    missing = [n for n in alerting_env.__all__ if not hasattr(alerting_env, n)]
    assert missing == [], f"__all__ names things the module does not define: {missing}"


# ====================================================================== the setup contract
def test_the_template_exists_and_carries_every_key_the_manifest_requires() -> None:
    """⭐ THE TEMPLATE IS THE SETUP CONTRACT'S DELIVERABLE, so it is checked against the manifest
    rather than eyeballed. A key added to `required_keys` and forgotten in the template is a
    setup document that produces a file which refuses to boot."""
    assert TEMPLATE.is_file(), f"{TEMPLATE} is missing"
    text = TEMPLATE.read_text(encoding="utf-8")
    for key in (*required_keys("dev"), *required_keys("prod"), "SMTP_USER"):
        assert key in text, f"{key} is required or optional and the template does not mention it"


def test_the_template_parses_with_the_same_parser_the_service_uses() -> None:
    """A template an operator copies must be a file this library can read. Comment-only lines are
    fine; what is asserted is that every uncommented line parses to a key."""
    values = read_config(TEMPLATE)
    for key in required_keys("prod"):
        assert key in values, f"{key} is not an actual KEY=VALUE line in the template"


def test_the_template_carries_no_real_value(tmp_path: Path) -> None:
    """⛔ PLACEHOLDERS ONLY. This repository is PUBLIC and the template is the file most likely to
    be filled in with a real one and then committed by accident somewhere else. The leak guard
    already scans this repository; this asks the narrower question the guard cannot: does the
    SHIPPED template contain anything that looks like a populated setting rather than a
    placeholder?
    """
    values = read_config(TEMPLATE)
    for key, value in values.items():
        if not value:
            continue
        placeholder = ("example" in value          # the reserved documentation domain
                       or value.isdigit()          # a port number is not an operator secret
                       or value.startswith("CHANGE-ME"))
        assert placeholder, (
            f"{key}={value!r} in the template does not look like a placeholder")
    # And the template really was parsed, so the loop above had something to reject.
    assert len(values) >= len(required_keys("prod"))


def test_the_setup_document_exists_and_names_the_three_variables() -> None:
    assert SETUP_DOC.is_file(), f"{SETUP_DOC} is missing"
    text = SETUP_DOC.read_text(encoding="utf-8")
    for name in ENV_VARS:
        assert name in text
    assert CONFIG_RELPATH in text


def test_the_setup_document_pipes_nothing_into_a_shell() -> None:
    """⛔ `curl … | sh` IS THE ONE THING THE PACKAGE FORBIDS OUTRIGHT. A setup document that tells
    an operator to execute whatever a URL happens to return today is a supply-chain hole with a
    friendly face, and this repository is public."""
    text = SETUP_DOC.read_text(encoding="utf-8")
    for forbidden in ("| sh", "| bash", "|sh", "|bash", "iex ", "Invoke-Expression"):
        assert forbidden not in text, f"the setup document contains {forbidden!r}"


def test_the_setup_document_covers_all_three_operating_systems() -> None:
    text = SETUP_DOC.read_text(encoding="utf-8").lower()
    for os_name in ("windows", "macos", "linux"):
        assert os_name in text
    # The two documented download commands, one per platform family.
    assert "invoke-webrequest" in text
    assert "curl" in text


def test_the_setup_document_tells_the_reader_to_review_what_they_download() -> None:
    text = SETUP_DOC.read_text(encoding="utf-8").lower()
    assert "review" in text
    assert "own risk" in text


def test_the_setup_documents_download_url_names_the_version_this_package_actually_is() -> None:
    """⭐ SAME INCENTIVE DESIGN AS THE README'S INSTALL LINE, for the same reason.

    The document tells an operator to fetch the template at a TAG. Pinned to a literal, bumping
    `__version__` and forgetting this page would leave the suite green while the page went on
    handing every new adopter a superseded template — the test would punish the correct action and
    reward the omission. Derived from the package, it becomes the drift guard it claims to be.
    """
    import kw_common

    text = SETUP_DOC.read_text(encoding="utf-8")
    # The FETCH lines, matched on the scheme so a sentence merely NAMING the host — the one
    # explaining what `-L` is for — is not mistaken for a command that pins a tag.
    urls = [ln for ln in text.splitlines() if "https://raw.githubusercontent.com/" in ln]
    assert len(urls) >= 2, "the per-OS download commands are no longer where this test looks"
    stale = [ln for ln in urls if f"/v{kw_common.__version__}/" not in ln]
    assert stale == [], (
        f"these fetch a tag this package is not ({kw_common.__version__}):\n" + "\n".join(stale))


def test_the_setup_documents_python_example_uses_only_names_it_imports() -> None:
    """The same defect class `test_readme.py` exists for: an adoption example that has never been
    run, calling something it never imported. Checked, not executed — running it would create the
    directories its example paths name."""
    import builtins
    import importlib
    import re

    blocks = re.findall(r"```python\n(.*?)```", SETUP_DOC.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, f"expected one python block in the setup document, found {len(blocks)}"
    tree = ast.parse(blocks[0])

    available: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            available.update(a.asname or a.name for a in node.names)
            if node.module and node.module.startswith("kw_common"):
                mod = importlib.import_module(node.module)
                for alias in node.names:
                    assert alias.name in getattr(mod, "__all__", ()), (
                        f"the setup document imports {alias.name!r} from {node.module}, which "
                        f"does not export it")
        elif isinstance(node, ast.Import):
            available.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            available.add(node.id)

    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert sorted(used - available) == []
