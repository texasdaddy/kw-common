"""Tests for `kw_common.alerting_env` — the fleet convention, and the two `alerting` changes it
needed.

These travel with the module for the same reason every other suite here does: a behaviour that is
not tested here becomes six untested copies the moment it ships. That is not rhetorical for this
package — the whole reason it exists is that five container templates declared five different
shapes of alerting variable, each of which was somebody's reasonable reading of the same prose.

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
import json
import logging
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
    MARKER_NAME,
    SHARED_ROOT_VAR,
    AlertEnvError,
    config_file_for,
    load_alert_settings,
    load_alert_settings_from_env,
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


# ====================================================================== acceptance 1: it is pure
def test_the_loader_runs_with_no_environment_set_at_all(tmp_path: Path) -> None:
    """⭐ THE ACCEPTANCE CRITERION, ASKED THE ONLY WAY THAT MEANS ANYTHING.

    The three variables are already deleted by the autouse fixture. If `load_alert_settings` read
    any of them — or fell back to a default path — this raises rather than returning settings.
    """
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "tape")
    assert settings.service == "tape"
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


# ============================================================ acceptance 2: it fails loudly
@pytest.mark.parametrize("missing", [SHARED_ROOT_VAR, DEPLOY_ENV_VAR])
def test_the_env_layer_refuses_a_missing_variable_rather_than_defaulting(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    write_shared(tmp_path)
    monkeypatch.setenv(SHARED_ROOT_VAR, str(tmp_path))
    monkeypatch.setenv(DEPLOY_ENV_VAR, "prod")
    monkeypatch.delenv(missing)

    with pytest.raises(AlertEnvError) as exc:
        load_alert_settings_from_env("tape")
    assert missing in str(exc.value)


@pytest.mark.parametrize("blank", ["", "   "])
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
        load_alert_settings_from_env("tape")


def test_the_env_layer_reads_shared_root_and_builds_the_documented_subpath(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_shared(tmp_path)
    monkeypatch.setenv(SHARED_ROOT_VAR, str(tmp_path))
    monkeypatch.setenv(DEPLOY_ENV_VAR, "dev")
    settings = load_alert_settings_from_env("tape")
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
    too, so a loader that selected the right topic while writing `[PROD][tape]` into every title
    would pass a URL-only assertion and ship two spellings of the same deployment to the operator.
    """
    write_shared(tmp_path)
    canonical = load_alert_settings(config_file_for(tmp_path), "prod", "tape")
    assert load_alert_settings(config_file_for(tmp_path), spelling, "tape") == canonical
    assert canonical.title_prefix == "[prod][tape] "


def test_the_manifest_is_case_insensitive_the_same_way() -> None:
    assert required_keys("PROD") == required_keys("prod")
    assert required_keys("prod")[-1] == "NTFY_URL_PROD"
    assert required_keys("dev")[-1] == "NTFY_URL_DEV"


def test_the_manifest_requires_the_email_block_and_leaves_smtp_user_optional() -> None:
    """⭐ THE MANIFEST IS OWNED HERE, so this is the test that stops six apps disagreeing about
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
    config.write_text(config.read_text(encoding="utf-8") +
                      "NTFY_URL_TAPE=https://ntfy.example.com/tape\n", encoding="utf-8")
    settings = load_alert_settings(config, "prod", "tape")
    assert settings.ntfy_url == "https://ntfy.example.com/tape"   # the service itself is fine
    with pytest.raises(AlertEnvError, match="NTFY_URL_PROD"):
        validate_boot(settings, "prod", tmp_path / "appconfig")


# ============================================ acceptance 4: the topic decides the prefix
def test_a_dedicated_topic_wins_and_carries_no_prefix(tmp_path: Path) -> None:
    write_shared(tmp_path, NTFY_URL_REAUTH_BOT="https://ntfy.example.com/reauth")
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "reauth-bot")
    assert settings.ntfy_url == "https://ntfy.example.com/reauth"
    assert settings.title_prefix == ""


def test_the_live_example_keeps_its_behaviour_in_both_environments(tmp_path: Path) -> None:
    """⚠️ reauth-bot IS the live example the standard names, and this package must not change what
    it does. Its own topic, in either environment, with no prefix — and the environment topic
    sitting right there in the same file, unused by it."""
    write_shared(tmp_path, NTFY_URL_REAUTH_BOT="https://ntfy.example.com/reauth")
    for env in ("dev", "prod"):
        settings = load_alert_settings(config_file_for(tmp_path), env, "reauth-bot")
        assert settings.ntfy_url == "https://ntfy.example.com/reauth"
        assert settings.title_prefix == ""


def test_a_service_on_the_shared_topic_carries_the_env_and_service_prefix(tmp_path: Path) -> None:
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "dev", "cef-tracker")
    assert settings.ntfy_url == GOOD_CONFIG["NTFY_URL_DEV"]
    assert settings.title_prefix == "[dev][cef-tracker] "


def test_a_blank_override_means_the_shared_topic_and_therefore_the_prefix(tmp_path: Path) -> None:
    """A cleared key means "stop using my own topic", not "use no topic at all"."""
    write_shared(tmp_path, NTFY_URL_TAPE="   ")
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "tape")
    assert settings.ntfy_url == GOOD_CONFIG["NTFY_URL_PROD"]
    assert settings.title_prefix == "[prod][tape] "


def test_a_missing_shared_topic_still_gets_the_prefix(tmp_path: Path) -> None:
    """⭐ THE PREFIX FOLLOWS WHICH KEY WON, NOT WHETHER A URL WAS FOUND.

    Deciding it on the URL's presence would silently drop the prefix for exactly the deployment
    that is already broken — so when the file is later fixed, the first alerts arrive unlabelled
    from a service the operator has to guess at.
    """
    settings = load_alert_settings(drop(tmp_path, "NTFY_URL_PROD"), "prod", "tape")
    assert settings.ntfy_url == ""
    assert settings.title_prefix == "[prod][tape] "


def test_the_prefix_reaches_BOTH_outbound_titles(tmp_path: Path,
                                                 channels: dict[str, Spy]) -> None:
    """⭐ THE HALF A RETURN-VALUE ASSERTION CANNOT SEE. `title_prefix` being correct on the
    settings object says nothing about whether anything applies it."""
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "tape")
    Alerter(settings).notify(ERROR, "Backup failed", "disk full")
    for name, spy in channels.items():
        assert spy.calls, f"the {name} channel was not asked to send anything"
        assert spy.calls[0][2] == "[prod][tape] Backup failed"


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
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "tape")
    state = tmp_path / "state.json"
    errors = tmp_path / "logs" / "errors.log"
    from dataclasses import replace
    settings = replace(settings, state_file=str(state), error_log=str(errors))

    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        Alerter(settings).notify(ERROR, "Backup failed", "disk full", escalating=True)

    assert list(json.loads(state.read_text(encoding="utf-8"))) == ["Backup failed"]
    record = json.loads(errors.read_text(encoding="utf-8").splitlines()[0])
    assert record["title"] == "Backup failed"
    assert "[prod][tape]" not in caplog.text
    # ...and the outbound copy really did carry it, so this is not passing because nothing works.
    assert channels["ntfy"].calls[0][2] == "[prod][tape] Backup failed"


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

    for stand_in in (NoPrefix(), PrefixRaises()):
        channels["ntfy"].calls.clear()
        results = Alerter(stand_in).notify(ERROR, "still delivered", "...")  # type: ignore[arg-type]
        assert results["ntfy"] == "sent", f"{type(stand_in).__name__} cost the notification"
        assert channels["ntfy"].calls[0][2] == "still delivered"


def test_a_non_string_prefix_is_refused_by_the_constructor() -> None:
    """`None` is the obvious thing to pass for "no prefix" and would render as the text `None` in
    front of every alert title — visible, wrong, and only in production."""
    with pytest.raises(ValueError, match="title_prefix"):
        AlertSettings(service="svc", title_prefix=None)  # type: ignore[arg-type]


# ================================================================ the service→key transform
@pytest.mark.parametrize(("service", "key"), [
    ("tape", "NTFY_URL_TAPE"),
    ("reauth-bot", "NTFY_URL_REAUTH_BOT"),
    ("cef-tracker", "NTFY_URL_CEF_TRACKER"),
    ("the.desk", "NTFY_URL_THE_DESK"),
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
    settings = load_alert_settings(config, "prod", "tape")
    _send_one(settings)
    assert smtp_login == [("svc@example.com", GOOD_CONFIG["SMTP_PASSWORD"])]


def test_smtp_user_present_and_different_is_honoured(
        tmp_path: Path, smtp_login: list[tuple[str, str]]) -> None:
    """⚠️ THE DIRECTION A DEFAULT BREAKS. `SMTP_USER` and `EMAIL_FROM` are the same value in this
    fleet today and are different things — the auth identity and the header. They diverge on a
    verified alias and on a relay whose user is literally `apikey`, and a default that overwrote
    the configured value would fail authentication for exactly those deployments."""
    config = write_shared(tmp_path, SMTP_USER="apikey")
    settings = load_alert_settings(config, "prod", "tape")
    _send_one(settings)
    assert smtp_login == [("apikey", GOOD_CONFIG["SMTP_PASSWORD"])]


def test_a_blank_smtp_user_also_falls_back(tmp_path: Path,
                                           smtp_login: list[tuple[str, str]]) -> None:
    """Blank is how a cleared container Variable arrives, so it must mean the same as absent."""
    config = write_shared(tmp_path, SMTP_USER="")
    settings = load_alert_settings(config, "prod", "tape")
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
    with pytest.raises(AlertEnvError):
        read_config(path)


# ======================================================== acceptance 6 & 7: boot validation
def _validated(tmp_path: Path, service: str = "tape",
               env: str = "prod") -> tuple[AlertSettings, Path, Path]:
    write_shared(tmp_path)
    settings = load_alert_settings(config_file_for(tmp_path), env, service)
    marker_dir = tmp_path / "appconfig"
    return settings, marker_dir, marker_dir / MARKER_NAME


def test_a_good_config_writes_the_marker_and_alerts_exactly_once(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    settings, marker_dir, marker = _validated(tmp_path)
    alerter = Alerter(settings)

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    assert marker.is_file()
    assert marker.read_bytes() == b""
    assert len(channels["ntfy"].calls) == 1
    assert channels["ntfy"].calls[0][1] is alerting.SEVERITIES[OK]


def test_a_second_boot_with_an_unchanged_config_alerts_nobody(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    settings, marker_dir, _marker = _validated(tmp_path)
    alerter = Alerter(settings)
    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    channels["ntfy"].calls.clear()
    channels["email"].calls.clear()

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is False
    assert silence(channels) == []


def test_touching_the_config_makes_the_next_boot_validate_again(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    settings, marker_dir, marker = _validated(tmp_path)
    alerter = Alerter(settings)
    validate_boot(settings, "prod", marker_dir, alerter=alerter)
    channels["ntfy"].calls.clear()

    config = Path(settings.config_file)
    # A real edit, and its timestamp moved forward far enough that a coarse filesystem clock
    # cannot make this test depend on how fast the machine is.
    config.write_text(config.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    import os as _os
    marker_mtime = marker.stat().st_mtime
    _os.utime(config, (marker_mtime + 10, marker_mtime + 10))

    assert validate_boot(settings, "prod", marker_dir, alerter=alerter) is True
    assert len(channels["ntfy"].calls) == 1


def test_a_marker_exactly_as_old_as_the_config_validates_again(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ STRICTLY NEWER. Filesystem timestamp granularity is a whole second on some filesystems,
    so "the config was edited in the same tick the marker was written" is a real ordering — and
    the safe reading of it is to re-check. Skipping is an optimisation; validating is the point.
    """
    settings, marker_dir, marker = _validated(tmp_path)
    validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings))
    import os as _os
    stamp = marker.stat().st_mtime
    _os.utime(Path(settings.config_file), (stamp, stamp))
    channels["ntfy"].calls.clear()

    assert validate_boot(settings, "prod", marker_dir, alerter=Alerter(settings)) is True


def test_the_marker_is_written_into_the_apps_own_directory_and_never_the_shared_root(
        tmp_path: Path) -> None:
    """⚠️ The shared root is mounted READ-ONLY. A marker written there fails on every boot in
    production while passing every test that used one writable directory for both."""
    shared = tmp_path / "shared"
    app = tmp_path / "app"
    write_shared(shared)
    settings = load_alert_settings(config_file_for(shared), "prod", "tape")
    validate_boot(settings, "prod", app, shared_root=shared, alerter=Alerter(settings))

    assert (app / MARKER_NAME).is_file()
    assert list(shared.rglob(MARKER_NAME)) == []


@pytest.mark.parametrize("missing_key", ["EMAIL_TO", "SMTP_PASSWORD", "SMTP_PORT",
                                         "NTFY_URL_PROD"])
def test_a_missing_required_key_refuses_to_boot_and_alerts_nobody(
        tmp_path: Path, channels: dict[str, Spy], caplog: pytest.LogCaptureFixture,
        missing_key: str) -> None:
    """⛔ THE REFUSAL MUST NOT REPORT ITSELF THROUGH THE CHANNEL IT IS REFUSING OVER."""
    config = drop(tmp_path, missing_key)
    settings = load_alert_settings(config, "prod", "tape")
    with pytest.raises(AlertEnvError) as exc:
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))

    assert missing_key in str(exc.value)
    assert silence(channels) == []
    assert not (tmp_path / "app" / MARKER_NAME).exists()


def test_a_blank_required_key_is_refused_the_same_way_as_an_absent_one(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    config = write_shared(tmp_path, EMAIL_TO="   ")
    settings = load_alert_settings(config, "prod", "tape")
    with pytest.raises(AlertEnvError, match="EMAIL_TO"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert silence(channels) == []


def test_the_refusal_names_the_keys_and_never_their_values(tmp_path: Path) -> None:
    """One of these keys is the SMTP password, and this message is the line an operator pastes
    into a bug report."""
    secret = "s3cret-app-password"  # noqa: S105 — a fixture value, and the point of the test
    config = write_shared(tmp_path, SMTP_PASSWORD=secret, EMAIL_TO="")
    settings = load_alert_settings(config, "prod", "tape")
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
    settings = AlertSettings(service="tape", config_file=str(config))
    with pytest.raises(AlertEnvError, match="does not exist"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert silence(channels) == []


def test_a_mis_encoded_config_refuses_to_boot_and_alerts_nobody(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    config = config_file_for(tmp_path)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes("EMAIL_TO=ops@example.com\n".encode("utf-16"))
    settings = AlertSettings(service="tape", config_file=str(config))
    with pytest.raises(AlertEnvError, match="UTF-8"):
        validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))
    assert silence(channels) == []


def test_a_shared_root_that_is_not_mounted_is_reported_as_a_mount_problem(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⭐ A MISSING MOUNT AND A MISSING FILE SEND AN OPERATOR TO DIFFERENT PLACES, so they are
    different messages. One is a template volume that was never added; the other is a file that
    was never created."""
    settings = AlertSettings(service="tape",
                             config_file=str(config_file_for(tmp_path / "absent")))
    with pytest.raises(AlertEnvError, match="MOUNT"):
        validate_boot(settings, "prod", tmp_path / "app",
                      shared_root=tmp_path / "absent", alerter=Alerter(settings))
    assert silence(channels) == []


def test_a_mounted_root_with_no_configs_directory_is_reported_as_a_structure_problem(
        tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    settings = AlertSettings(service="tape", config_file=str(config_file_for(shared)))
    with pytest.raises(AlertEnvError, match="structure"):
        validate_boot(settings, "prod", tmp_path / "app", shared_root=shared)


def test_settings_with_no_config_file_are_refused_rather_than_silently_passing(
        tmp_path: Path) -> None:
    settings = AlertSettings(service="tape")
    with pytest.raises(AlertEnvError, match="config_file"):
        validate_boot(settings, "prod", tmp_path / "app")


def test_validation_without_an_installed_alerter_still_validates_and_says_so(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Not a failure: an app may validate before it configures. Loud enough that "no alert
    arrived" is explicable."""
    settings, marker_dir, marker = _validated(tmp_path)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting_env"):
        assert validate_boot(settings, "prod", marker_dir) is True
    assert "no Alerter" in caplog.text
    assert marker.is_file()


def test_an_unwritable_marker_directory_costs_a_warning_and_not_the_boot(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """⭐ THE CONFIGURATION IS VALID AND HAS BEEN ANNOUNCED. Refusing to start over a marker would
    turn a missing read-write volume into an outage of the service itself — the failure direction
    is a duplicate alert per boot, which is noise."""
    settings, marker_dir, _ = _validated(tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "write_bytes", refuse)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting_env"):
        assert validate_boot(settings, "prod", marker_dir,
                             alerter=Alerter(settings)) is True
    assert CONFIG_PATH_VAR in caplog.text
    assert len(channels["ntfy"].calls) == 1


def test_the_validation_alert_names_no_value_from_the_config_file(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    """⛔ THIS MESSAGE GOES TO EMAIL AND TO A PUSH TOPIC, and the file it describes holds an SMTP
    password. It says which KEYS were used, never what they hold."""
    secret = "s3cret-app-password"  # noqa: S105 — a fixture value, and the point of the test
    write_shared(tmp_path, SMTP_PASSWORD=secret)
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "tape")
    validate_boot(settings, "prod", tmp_path / "app", alerter=Alerter(settings))

    _, _, title, message = channels["ntfy"].calls[0]
    body = f"{title} {message}"
    for value in GOOD_CONFIG.values():
        assert value not in body
    assert secret not in body
    assert "tape" in body and "prod" in body


def test_the_validation_alert_says_which_kind_of_topic_the_service_is_on(
        tmp_path: Path, channels: dict[str, Spy]) -> None:
    write_shared(tmp_path, NTFY_URL_TAPE="https://ntfy.example.com/tape")
    settings = load_alert_settings(config_file_for(tmp_path), "prod", "tape")
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

    settings = load_alert_settings_from_env("tape")
    assert validate_boot_from_env(settings, alerter=Alerter(settings)) is True
    assert (app / MARKER_NAME).is_file()
    assert len(channels["ntfy"].calls) == 1


@pytest.mark.parametrize("missing", [SHARED_ROOT_VAR, CONFIG_PATH_VAR, DEPLOY_ENV_VAR])
def test_validate_boot_from_env_refuses_a_missing_variable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channels: dict[str, Spy],
        missing: str) -> None:
    shared = tmp_path / "shared"
    write_shared(shared)
    settings = load_alert_settings(config_file_for(shared), "prod", "tape")
    for name, value in ((SHARED_ROOT_VAR, str(shared)),
                        (CONFIG_PATH_VAR, str(tmp_path / "app")),
                        (DEPLOY_ENV_VAR, "prod")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)

    with pytest.raises(AlertEnvError) as exc:
        validate_boot_from_env(settings)
    assert missing in str(exc.value)
    assert silence(channels) == []


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
    urls = [ln for ln in text.splitlines() if "raw.githubusercontent.com" in ln]
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
