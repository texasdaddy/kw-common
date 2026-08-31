"""Tests for `kw_common.alerting`.

These travel WITH the module, deliberately. A behaviour that is not tested here becomes six
untested copies the moment it ships — which is the duplication this library exists to end.

Nothing here touches the network or a real SMTP server: the channels are replaced with spies that
RECORD what they were asked, and the assertions read the recording. A spy that answers whatever it
is asked, or a test that asserts only that a call happened, proves nothing.
"""

from __future__ import annotations

import json
import logging
import ssl
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from kw_common import alerting
from kw_common.alerting import (
    ERROR,
    MAX_SMTP_PORT,
    MIN_SMTP_PORT,
    OK,
    WARN,
    AlertConfig,
    Alerter,
    AlertSettings,
    smtp_port_fault,
)


# --------------------------------------------------------------------------- fixtures / helpers
@pytest.fixture
def settings(tmp_path: Path) -> AlertSettings:
    """A fully-configured service. Every path is inside `tmp_path`; nothing is assumed."""
    return AlertSettings(
        service="svc",
        config_file=str(tmp_path / "alerting.env"),
        ntfy_url="https://ntfy.example.com/svc",
        state_file=str(tmp_path / "state.json"),
        error_log=str(tmp_path / "logs" / "svc-errors.log"),
    )


@pytest.fixture
def alerter(settings: AlertSettings) -> Alerter:
    return Alerter(settings)


class Spy:
    """Records every (config, spec, title, message) it is handed. Never raises by itself.

    It RECORDS rather than asserts: an assertion raised in here would be swallowed by the
    `except Exception` that wraps each channel, so the test would pass whether or not the guard
    under test exists. The assertions live in the test, reading `self.calls`.
    """

    def __init__(self, fail: BaseException | None = None) -> None:
        self.calls: list[tuple[AlertConfig, alerting.SeveritySpec, str, str]] = []
        self.fail = fail

    def __call__(self, cfg: AlertConfig, spec: alerting.SeveritySpec, title: str,
                 message: str) -> None:
        self.calls.append((cfg, spec, title, message))
        if self.fail is not None:
            raise self.fail


@pytest.fixture(autouse=True)
def channels(monkeypatch: pytest.MonkeyPatch) -> dict[str, Spy]:
    """Replace both channels with spies, keeping the registry shape the code reads.

    ⭐ AUTOUSE. A test that merely forgot to ask for this would otherwise attempt a real send, and
    the failure is not a loud one: `_dispatch` catches it and reports `"failed"`, so a test
    asserting `"suppressed"` fails for a reason that has nothing to do with de-duplication. Nine
    tests here did exactly that before this was autouse. `conftest.py` blocks the network as well;
    this makes the intended object available to every test whether or not it names it.
    """
    spies = {"email": Spy(), "ntfy": Spy()}
    monkeypatch.setattr(alerting, "_CHANNELS",
                        tuple((name, spies[name]) for name in ("email", "ntfy")))
    return spies


def write_email_config(settings: AlertSettings, **overrides: str) -> None:
    values = {
        "EMAIL_TO": "ops@example.com",
        "EMAIL_FROM": "svc@example.com",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USER": "svc@example.com",
        "SMTP_PASSWORD": "not-a-real-password",
    }
    values.update(overrides)
    assert settings.config_file is not None
    Path(settings.config_file).write_text(
        "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")


# ================================================================= the config-file line boundary
# ⭐ THE TWO HALVES OF THE PARSER FIX. `_parse_env_file` opens the file with `newline=""` AND
# splits on `"\n"`. Either half alone leaves a live silent-truncation bug, so both directions are
# pinned below and neither test can be satisfied by the other's fix.

# Every character `str.splitlines()` treats as a line boundary but a KEY=VALUE file does not.
# `\n` is deliberately absent — it IS a real separator and has its own test below.
LINE_BOUNDARY_CHARS = [
    pytest.param("\r", id="CR"),
    pytest.param("\v", id="VT"),
    pytest.param("\f", id="FF"),
    pytest.param("\x1c", id="FS"),
    pytest.param("\x1d", id="GS"),
    pytest.param("\x1e", id="RS"),
    pytest.param("\x85", id="NEL"),
    pytest.param(" ", id="LINE-SEPARATOR"),
    pytest.param(" ", id="PARAGRAPH-SEPARATOR"),
]


@pytest.mark.parametrize("boundary", LINE_BOUNDARY_CHARS)
def test_a_unicode_line_boundary_never_truncates_a_setting(tmp_path: Path, boundary: str) -> None:
    """A character only `splitlines()` calls a line break must stay INSIDE the value.

    ⭐ WRITTEN WITH `write_bytes`, NOT `write_text`. The text writer applies newline translation,
    which rewrites a lone `\\r` before it ever reaches the file — silently turning the CR case
    (the one the `newline=""` half of the fix exists for) into a test of something else entirely.
    """
    path = tmp_path / "alerting.env"
    value = f"a@example.com{boundary}b@example.invalid"
    path.write_bytes(f"EMAIL_TO={value}\n".encode())

    parsed = alerting._parse_env_file(str(path))

    assert parsed["EMAIL_TO"] == value, (
        f"{boundary!r} was treated as a line boundary, so the value was truncated to "
        f"{parsed['EMAIL_TO']!r} and a recipient was silently dropped"
    )


def test_a_real_newline_still_separates_settings_including_crlf(tmp_path: Path) -> None:
    """The NEGATIVE direction: `\\n` and `\\r\\n` must still end a line.

    Without this, a parser that never splits at all satisfies the test above. With it, the only
    implementation that passes both is the one that splits on `\\n` and strips the `\\r` a CRLF
    file leaves behind.
    """
    path = tmp_path / "alerting.env"
    path.write_bytes(b"EMAIL_TO=a@example.com\nEMAIL_FROM=b@example.com\r\n"
                     b"SMTP_HOST=smtp.example.com\n")

    parsed = alerting._parse_env_file(str(path))

    assert parsed["EMAIL_TO"] == "a@example.com"
    assert parsed["EMAIL_FROM"] == "b@example.com", "the CR of a CRLF line ending survived"
    assert parsed["SMTP_HOST"] == "smtp.example.com"


def test_a_cr_only_file_is_the_documented_dual(tmp_path: Path) -> None:
    """Classic-Mac line endings parse as ONE line. Known, accepted, and pinned so it stays a
    DECISION rather than becoming a surprise — translating a lone CR *is* the bug being fixed."""
    path = tmp_path / "alerting.env"
    path.write_bytes(b"EMAIL_TO=a@example.com\rEMAIL_FROM=b@example.com\r")

    parsed = alerting._parse_env_file(str(path))

    assert list(parsed) == ["EMAIL_TO"]
    assert parsed["EMAIL_TO"] == "a@example.com\rEMAIL_FROM=b@example.com"


def test_the_parser_tolerates_the_shapes_an_operator_hand_writes(tmp_path: Path) -> None:
    path = tmp_path / "alerting.env"
    path.write_text(
        "# a comment\n"
        "\n"
        "export EMAIL_TO='ops@example.com'\n"
        '  SMTP_HOST = "smtp.example.com"  \n'
        "not a setting line\n"
        "SMTP_PORT=587\n",
        encoding="utf-8")

    parsed = alerting._parse_env_file(str(path))

    assert parsed == {"EMAIL_TO": "ops@example.com", "SMTP_HOST": "smtp.example.com",
                      "SMTP_PORT": "587"}


def test_a_missing_config_file_is_not_an_error(tmp_path: Path) -> None:
    assert alerting._parse_env_file(str(tmp_path / "nope.env")) == {}


def test_an_undecodable_config_file_disables_email_without_raising(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "alerting.env"
    path.write_bytes(b"EMAIL_TO=\xff\xfe\x00not-utf-8\n")

    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerting._parse_env_file(str(path)) == {}
    assert "unreadable" in caplog.text


# ============================================================================== recipient parsing
@pytest.mark.parametrize(("raw", "expected"), [
    ("ops@example.com", "ops@example.com"),
    ("a@example.com;b@example.com", "a@example.com, b@example.com"),
    ("a@example.com , ; b@example.com", "a@example.com, b@example.com"),
    ("a@example.com,,b@example.com", "a@example.com, b@example.com"),
    (";", ""),
    ("  ", ""),
    ('"Doe, Jane" <j@example.com>', '"Doe, Jane" <j@example.com>'),
])
def test_recipients_normalise_to_what_smtplib_can_parse(raw: str, expected: str) -> None:
    assert alerting._recipients(raw) == expected


# ================================================================================ the SMTP port
@pytest.mark.parametrize("raw", ["587", "25", str(MIN_SMTP_PORT), str(MAX_SMTP_PORT), "  465  "])
def test_a_usable_port_has_no_fault(raw: str) -> None:
    assert smtp_port_fault(raw) == ""


@pytest.mark.parametrize("raw", ["", "   "])
def test_a_blank_port_is_not_a_fault(raw: str) -> None:
    """That is how a container template passes an unset optional setting."""
    assert smtp_port_fault(raw) == ""


@pytest.mark.parametrize(("raw", "why"), [
    ("not-a-port", "is not a number"),
    ("0", "outside the usable range"),
    ("-1", "outside the usable range"),
    ("65536", "outside the usable range"),
    ("1" + "0" * 400, "outside the usable range"),
])
def test_a_rejected_port_says_why_without_echoing_the_value(raw: str, why: str) -> None:
    fault = smtp_port_fault(raw)
    assert why in fault
    assert f"its {len(raw.strip())}-character value" in fault
    assert raw not in fault, "the rejected value was echoed — it may be a pasted password"


def test_a_400_digit_port_does_not_raise_inside_the_guard() -> None:
    """`int()` accepts it happily; converting it to a float raises `OverflowError` INSIDE the
    guard meant to stop a bad setting breaking anything. The comparison must stay exact."""
    assert "outside the usable range" in smtp_port_fault("1" + "0" * 400)


def test_smtp_port_falls_back_and_complains(caplog: pytest.LogCaptureFixture) -> None:
    cfg = AlertConfig(email={"SMTP_PORT": "65536"})
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert cfg.smtp_port() == alerting.DEFAULT_SMTP_PORT
    assert "SMTP_PORT" in caplog.text
    assert "65536" not in caplog.text


def test_smtp_port_falls_back_silently_for_a_blank_value(
        caplog: pytest.LogCaptureFixture) -> None:
    cfg = AlertConfig(email={"SMTP_PORT": "   "})
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert cfg.smtp_port() == alerting.DEFAULT_SMTP_PORT
    assert caplog.text == ""


def test_setting_faults_reports_the_same_sentence_the_send_path_logs() -> None:
    """The point of the pure classifier: a boot report and the send-time log line cannot drift
    into describing the same fault two different ways."""
    cfg = AlertConfig(email={"SMTP_PORT": "70000"})
    assert cfg.setting_faults() == [smtp_port_fault("70000")]


def test_setting_faults_is_empty_when_nothing_was_rejected() -> None:
    assert AlertConfig(email={"SMTP_PORT": "587"}).setting_faults() == []
    assert AlertConfig(email={"SMTP_PORT": ""}).setting_faults() == []


# ============================================================================ channel readiness
def test_email_ready_needs_every_required_setting(settings: AlertSettings) -> None:
    write_email_config(settings)
    assert AlertConfig.load(settings).email_ready() is True


def test_email_is_not_ready_when_nothing_is_configured(
        settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    """The `any(...)` shortcut answers "nothing configured" and stays QUIET — that is not a
    misconfiguration to shout about on every send, it is a channel the boot warning reports."""
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert AlertConfig.load(settings).email_ready() is False
    assert caplog.text == ""


def test_a_separator_only_recipient_list_is_a_reported_misconfiguration(
        settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    """⭐ THE LOAD-BEARING ORDERING. `EMAIL_TO=;` is non-empty, so the raw `any()` shortcut passes
    and the check proceeds to normalisation, where EMAIL_TO resolves to nothing and is REPORTED.

    Counting the RESOLVED values in the shortcut instead would make all five look absent and
    return False with NO log line at all — reclassifying a real misconfiguration as "nothing
    configured", which is this module's one unforgivable failure: going quiet.
    """
    write_email_config(settings, EMAIL_TO=";")
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert AlertConfig.load(settings).email_ready() is False
    assert "EMAIL_TO" in caplog.text
    assert "missing or unusable" in caplog.text


def test_email_readiness_never_names_the_password(
        settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    write_email_config(settings, SMTP_PASSWORD="", EMAIL_TO="ops@example.com")
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        AlertConfig.load(settings).email_ready()
    assert "SMTP_PASSWORD" in caplog.text  # the NAME
    assert "not-a-real-password" not in caplog.text  # never the value


@pytest.mark.parametrize("url", [
    "https://ntfy.example.com/topic",
    "http://ntfy.example.com/topic",
])
def test_a_full_topic_url_is_ready(url: str) -> None:
    assert AlertConfig(ntfy_url=url).ntfy_ready() is True


@pytest.mark.parametrize("url", [
    "",
    "a-bare-topic",
    "ftp://ntfy.example.com/topic",
    "https:///topic",
    "https://ntfy.example.com/to pic",
    "https://ntfy.example.com/\x00topic",
    "https://[::1/topic",
])
def test_an_unusable_ntfy_url_is_not_ready(url: str) -> None:
    """Including the two that RAISE rather than return False in a naive implementation: a control
    character (http.client `InvalidURL`, whose message quotes the topic) and an unclosed IPv6
    bracket (`urlsplit` raises `ValueError`)."""
    assert AlertConfig(ntfy_url=url).ntfy_ready() is False


def test_a_readiness_check_that_raises_disables_only_its_own_channel(
        caplog: pytest.LogCaptureFixture) -> None:
    class Exploding(AlertConfig):
        def ntfy_ready(self) -> bool:
            raise RuntimeError("boom")

    cfg = Exploding(ntfy_url="https://ntfy.example.com/t",
                    email=dict.fromkeys(alerting.EMAIL_KEYS, "x"))
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert cfg.is_ready("ntfy") is False
        assert cfg.is_ready("email") is True
    assert "RuntimeError" in caplog.text


def test_is_ready_answers_false_for_a_channel_that_does_not_exist() -> None:
    """`is_ready` derives the check from the channel NAME, so an unknown one must be refused
    rather than raising the AttributeError its own docstring promises it never will."""
    assert AlertConfig().is_ready("carrier-pigeon") is False


# ================================================================================ notify() basics
def test_notify_sends_to_every_ready_channel(
        alerter: Alerter, settings: AlertSettings, channels: dict[str, Spy]) -> None:
    write_email_config(settings)

    results = alerter.notify(ERROR, "svc: db unreachable", "connection refused")

    assert results == {"email": "sent", "ntfy": "sent"}
    for spy in channels.values():
        assert len(spy.calls) == 1
        _, spec, title, message = spy.calls[0]
        assert spec is alerting.SEVERITIES[ERROR]
        assert (title, message) == ("svc: db unreachable", "connection refused")


def test_an_unconfigured_channel_is_skipped_not_failed(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    """No email config file was written, so email is unconfigured; ntfy is fine."""
    results = alerter.notify(WARN, "svc: slow", "p95 up")

    assert results == {"email": "skipped", "ntfy": "sent"}
    assert channels["email"].calls == []


def test_one_dead_channel_never_silences_the_other(
        alerter: Alerter, settings: AlertSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    write_email_config(settings)
    email = Spy(fail=OSError("smtp down"))
    ntfy = Spy()
    monkeypatch.setattr(alerting, "_CHANNELS", (("email", email), ("ntfy", ntfy)))

    results = alerter.notify(ERROR, "svc: broken", "...")

    assert results == {"email": "failed", "ntfy": "sent"}
    assert len(ntfy.calls) == 1, "the failing channel took the healthy one down with it"


def test_the_severity_drives_the_priority_and_the_prefix(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    for severity, priority, prefix in [(OK, "low", "[OK]"), (WARN, "high", "[WARN]"),
                                       (ERROR, "urgent", "[ERROR]")]:
        alerter.notify(severity, f"svc: {severity}", "...")
        _, spec, _, _ = channels["ntfy"].calls[-1]
        assert (spec.ntfy_priority, spec.prefix) == (priority, prefix)


def test_an_unknown_severity_is_loud_and_still_delivered(
        alerter: Alerter, channels: dict[str, Spy], caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        results = alerter.notify("CATASTROPHE", "svc: ?", "...")

    assert results["ntfy"] == "sent"
    assert "unknown alert severity" in caplog.text
    _, spec, _, _ = channels["ntfy"].calls[-1]
    assert spec is alerting.SEVERITIES[ERROR], "an unknown severity must be treated as ERROR"


def test_an_unhashable_severity_does_not_reach_the_caller(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    """`dict.get` propagates whatever the key's own `__hash__` raises. `notify()` never raises."""
    results = alerter.notify(["not", "hashable"], "svc: ?", "...")  # type: ignore[arg-type]
    assert results["ntfy"] == "sent"


def test_the_log_line_goes_out_even_when_every_channel_is_dead(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """Logging is not paging. The process log stays the complete record."""
    monkeypatch.setattr(alerting, "_CHANNELS",
                        (("email", Spy(fail=OSError("x"))), ("ntfy", Spy(fail=OSError("y")))))
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        alerter.notify(ERROR, "svc: everything is on fire", "details here")
    assert "svc: everything is on fire" in caplog.text
    assert "details here" in caplog.text


def test_a_dispatch_that_fails_before_any_channel_is_reported_as_failed(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(self: Alerter) -> AlertConfig:
        raise RuntimeError("unreadable mount")

    monkeypatch.setattr(Alerter, "config", explode)
    assert alerter.notify(ERROR, "svc: x", "...") == {"email": "failed", "ntfy": "failed"}


def test_a_tls_verification_failure_is_reported_louder_than_an_outage(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """⭐ `urlopen` re-raises the handshake's `OSError` as `URLError`, which is NOT an
    `SSLError` — so a plain `except ssl.SSLCertVerificationError` is dead code on this path."""
    wrapped = urllib.error.URLError(ssl.SSLCertVerificationError("self-signed"))
    monkeypatch.setattr(alerting, "_CHANNELS", (("ntfy", Spy(fail=wrapped)),))

    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        alerter.notify(ERROR, "svc: x", "...")
    assert "TLS VERIFICATION" in caplog.text


def test_a_self_referencing_cause_chain_does_not_spin_forever() -> None:
    exc = RuntimeError("loop")
    exc.__cause__ = exc
    assert alerting._is_cert_failure(exc) is False


# ============================================================ edge-trigger / escalate / clears
def test_a_repeat_condition_is_suppressed_until_it_clears(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    first = alerter.notify(ERROR, "svc: backup failed", "attempt 1")
    second = alerter.notify(ERROR, "svc: backup failed", "attempt 2")

    assert first["ntfy"] == "sent"
    assert second == {"email": "suppressed", "ntfy": "suppressed"}
    assert len(channels["ntfy"].calls) == 1


def test_an_ok_is_never_suppressed(alerter: Alerter, channels: dict[str, Spy]) -> None:
    for _ in range(3):
        assert alerter.notify(OK, "svc: heartbeat", "alive")["ntfy"] == "sent"
    assert len(channels["ntfy"].calls) == 3


def test_an_ok_clears_only_its_own_title_and_what_it_names(
        alerter: Alerter, channels: dict[str, Spy]) -> None:
    """⭐ The failure this prevents: an earlier version cleared EVERY condition on any OK, so a
    routine hourly confirmation re-armed every edge-triggered condition the service had."""
    alerter.notify(ERROR, "svc: backup failed", "...")
    alerter.notify(ERROR, "svc: census stalled", "...")

    alerter.notify(OK, "svc: backup ok", "...", clears="svc: backup failed")

    # the backup condition was re-armed, so its next occurrence alerts again
    assert alerter.notify(ERROR, "svc: backup failed", "again")["ntfy"] == "sent"
    # the census condition was NOT touched, so it is still suppressed
    assert alerter.notify(ERROR, "svc: census stalled", "still")["ntfy"] == "suppressed"


def test_clears_accepts_an_iterable_of_titles(alerter: Alerter) -> None:
    alerter.notify(ERROR, "svc: a", "...")
    alerter.notify(ERROR, "svc: b", "...")
    alerter.notify(OK, "svc: recovered", "...", clears=["svc: a", "svc: b"])
    assert alerter.notify(ERROR, "svc: a", "...")["ntfy"] == "sent"
    assert alerter.notify(ERROR, "svc: b", "...")["ntfy"] == "sent"


def test_clears_on_a_non_ok_is_refused_and_says_so(
        alerter: Alerter, caplog: pytest.LogCaptureFixture) -> None:
    """An alert that resolved its own condition would reset the escalation backoff on every
    retry, so a permanent failure would page at the base gap forever and never escalate."""
    alerter.notify(ERROR, "svc: db down", "attempt 1")
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        alerter.notify(ERROR, "svc: db down", "attempt 2", clears="svc: db down")
    assert "is IGNORED" in caplog.text
    # still suppressed — the clear was not honoured
    assert alerter.notify(ERROR, "svc: db down", "attempt 3")["ntfy"] == "suppressed"


def test_a_nonsense_clears_argument_loses_the_clear_but_not_the_alert(
        alerter: Alerter, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        results = alerter.notify(OK, "svc: ok", "...", clears=object())
    assert results["ntfy"] == "sent"
    assert "neither a title nor an iterable" in caplog.text


def test_an_escalating_condition_backs_off_then_re_pages(
        alerter: Alerter, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(alerting, "_now", lambda: clock["t"])

    assert alerter.notify(ERROR, "svc: consent lapsing", "1", escalating=True)["ntfy"] == "sent"
    clock["t"] += 59 * 60  # 59 minutes — inside the 1h base gap
    assert alerter.notify(ERROR, "svc: consent lapsing", "2", escalating=True)[
        "ntfy"] == "suppressed"
    clock["t"] += 2 * 60  # now past 1h
    assert alerter.notify(ERROR, "svc: consent lapsing", "3", escalating=True)["ntfy"] == "sent"
    clock["t"] += 61 * 60  # 61 min later: the gap has doubled to 2h, so still quiet
    assert alerter.notify(ERROR, "svc: consent lapsing", "4", escalating=True)[
        "ntfy"] == "suppressed"

    assert len(channels["ntfy"].calls) == 2


@pytest.mark.parametrize(("count", "hours"), [
    (1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0), (6, 24.0), (99, 24.0),
    (0, 1.0), (-5, 1.0),
])
def test_the_backoff_curve_doubles_and_caps(count: int, hours: float) -> None:
    assert alerting._escalate_gap_hours(count) == hours


def test_a_backwards_clock_does_not_mute_an_escalating_condition(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch) -> None:
    """`0 <= waited_h < gap_h` on purpose: a negative wait means the clock jumped BACKWARDS, and
    treating that as "not long enough" would suppress the alert for as long as the skew lasts."""
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(alerting, "_now", lambda: clock["t"])
    alerter.notify(ERROR, "svc: x", "1", escalating=True)
    clock["t"] -= 10_000  # the clock went backwards
    assert alerter.notify(ERROR, "svc: x", "2", escalating=True)["ntfy"] == "sent"


def test_a_delivery_that_reached_nobody_is_not_recorded_as_reported(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch) -> None:
    """⭐ Otherwise a delivery OUTAGE becomes a permanent silence: the condition reads as
    already-reported, so its next occurrence is de-duplicated against a send that never landed."""
    monkeypatch.setattr(alerting, "_CHANNELS", (("ntfy", Spy(fail=OSError("down"))),))
    assert alerter.notify(ERROR, "svc: backup failed", "1") == {"ntfy": "failed"}

    delivered = Spy()
    monkeypatch.setattr(alerting, "_CHANNELS", (("ntfy", delivered),))
    assert alerter.notify(ERROR, "svc: backup failed", "2") == {"ntfy": "sent"}
    assert len(delivered.calls) == 1


def test_de_duplication_fails_open(
        alerter: Alerter, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """Every error in the escalation bookkeeping SENDS the alert. Over-alerting is a nuisance; a
    de-duplicator that silently swallows a lapsed consent is the failure this exists to prevent."""
    def explode(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("state is on fire")

    monkeypatch.setattr(Alerter, "_should_send", explode)
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "de-duplication failed" in caplog.text


def test_corrupt_state_is_treated_as_nothing_firing(
        alerter: Alerter, settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    assert settings.state_file is not None
    Path(settings.state_file).write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "unreadable" in caplog.text


def test_a_state_file_holding_a_json_list_is_ignored(
        alerter: Alerter, settings: AlertSettings) -> None:
    assert settings.state_file is not None
    Path(settings.state_file).write_text("[1, 2, 3]", encoding="utf-8")
    assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"


def test_the_state_file_is_pruned_to_the_configured_cap(tmp_path: Path) -> None:
    settings = AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                             state_file=str(tmp_path / "state.json"),
                             max_tracked_conditions=3)
    alerter = Alerter(settings)
    state = {f"cond-{i}": {"first": float(i), "last": float(i), "count": 1} for i in range(10)}

    alerter._write_state(state)

    kept = json.loads(Path(str(settings.state_file)).read_text(encoding="utf-8"))
    assert set(kept) == {"cond-7", "cond-8", "cond-9"}, "the OLDEST must be the ones dropped"


def test_state_file_none_delivers_everything_and_writes_nothing(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    """The upstream-de-duplicates-its-own-conditions opt-out.

    ⭐ The CWD assertion is the non-obvious half. The predecessor spelled this opt-out as a magic
    string in a path-valued setting, which resolved to a RELATIVE file of that name in the working
    directory — created, written and left behind on the container's disposable layer by the first
    alert. Running from an empty CWD and requiring it to stay empty is what pins that it cannot
    come back.
    """
    monkeypatch.chdir(tmp_path)
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    state_file=None))
    for _ in range(3):
        assert alerter.notify(ERROR, "svc: repeated", "...")["ntfy"] == "sent"
    assert len(channels["ntfy"].calls) == 3
    assert list(tmp_path.iterdir()) == [], "nothing may be persisted with the opt-out set"


def test_a_read_only_state_directory_re_alerts_rather_than_raising(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    state_file=str(tmp_path / "state.json")))

    def refuse(*args: object, **kwargs: object) -> object:
        raise PermissionError("read-only")

    monkeypatch.setattr(alerting.os, "replace", refuse)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "could not save alert state" in caplog.text


# ============================================================================ the error-log sink
def test_warn_and_error_are_recorded_and_ok_is_not(
        alerter: Alerter, settings: AlertSettings, channels: dict[str, Spy]) -> None:
    alerter.notify(OK, "svc: heartbeat", "alive")
    alerter.notify(WARN, "svc: slow", "p95 up")
    alerter.notify(ERROR, "svc: down", "refused")

    records = alerter.read_errors(limit=0)
    assert [r["severity"] for r in records] == [WARN, ERROR]
    assert [r["title"] for r in records] == ["svc: slow", "svc: down"]
    assert all(r["service"] == "svc" for r in records)
    assert settings.error_log is not None


def test_a_suppressed_alert_is_still_recorded(alerter: Alerter) -> None:
    """De-duplication protects the operator's phone; hiding an occurrence from the file used to
    troubleshoot it would be the opposite of what this sink is for."""
    alerter.notify(ERROR, "svc: backup failed", "attempt 1")
    assert alerter.notify(ERROR, "svc: backup failed", "attempt 2")["ntfy"] == "suppressed"

    records = alerter.read_errors(limit=0)
    assert [r["message"] for r in records] == ["attempt 1", "attempt 2"]


def test_an_unknown_severity_is_recorded_as_error(alerter: Alerter) -> None:
    """It is treated as ERROR everywhere else; this must not be the one place a caller bug makes
    something vanish from the file a reader filters on WARN/ERROR."""
    alerter.notify("CATASTROPHE", "svc: ?", "...")
    assert [r["severity"] for r in alerter.read_errors(limit=0)] == [ERROR]


def test_a_record_is_one_line_even_for_a_traceback(alerter: Alerter,
                                                   settings: AlertSettings) -> None:
    alerter.notify(ERROR, "svc: crash", "Traceback:\nline one\nline two")
    assert settings.error_log is not None
    body = Path(settings.error_log).read_text(encoding="utf-8")
    assert body.count("\n") == 1
    assert json.loads(body)["message"] == "Traceback:\nline one\nline two"


def test_an_enormous_message_is_bounded(tmp_path: Path) -> None:
    """The roll checks the size BEFORE appending, so without a per-field cap one unbounded
    message writes one unbounded record and the size cap is not a cap at all."""
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"),
                             max_record_field=100)
    Alerter(settings).notify(ERROR, "svc: big", "x" * 10_000)
    record = json.loads(Path(str(settings.error_log)).read_text(encoding="utf-8"))
    assert record["message"] == "x" * 100 + "…[truncated]"


def test_an_object_whose_repr_raises_is_recorded_as_unrepresentable(tmp_path: Path) -> None:
    """`_field` calls `repr()` inside its own guard: an object whose `__repr__` raises is exactly
    the kind of thing that turns up in an exception handler, and this runs on the path that must
    never raise.

    `__str__` is left working on purpose, so this isolates the `repr` guard. The harsher case —
    a `__str__` that raises too, which reaches the `%`-formatting inside `logging` — is pinned
    separately below, in a subprocess, because it cannot be measured under pytest's log handler.
    """
    class ReprRaises:
        def __str__(self) -> str:
            return "printable"

        def __repr__(self) -> str:
            raise RuntimeError("no")

    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"))
    Alerter(settings).notify(ERROR, "svc: x", ReprRaises())  # type: ignore[arg-type]
    record = json.loads(Path(str(settings.error_log)).read_text(encoding="utf-8"))
    assert record["message"] == "<unrepresentable>"


def test_notify_does_not_raise_even_when_str_itself_raises(tmp_path: Path) -> None:
    """⭐ THE "NEVER RAISES" INVARIANT, MEASURED WHERE IT ACTUALLY LIVES.

    A message object whose `__str__` raises reaches the `%`-formatting inside `logging`, which is
    OUTSIDE this module's guards on purpose — the log line goes out first, before everything.
    The stdlib swallows it (`Handler.handleError` prints the traceback to stderr and returns), so
    `notify()` still returns normally and the caller's loop survives.

    ⚠️ THIS CANNOT BE MEASURED WITH `caplog`. Pytest's log handler overrides `handleError` and
    RE-RAISES formatting errors, so an in-process version of this test reports a failure that the
    shipped code does not have. Running it in a subprocess under default logging is what makes
    the assertion about the module rather than about the harness.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import logging, sys\n"
        "from kw_common.alerting import Alerter, AlertSettings, ERROR\n"
        "logging.basicConfig(level=logging.ERROR)\n"
        "class Hostile:\n"
        "    def __str__(self): raise RuntimeError('no str')\n"
        "    def __repr__(self): raise RuntimeError('no repr')\n"
        "results = Alerter(AlertSettings(service='svc')).notify(ERROR, 'svc: x', Hostile())\n"
        "assert results == {'email': 'skipped', 'ntfy': 'skipped'}, results\n"
        "sys.stdout.write('SURVIVED')\n",
        encoding="utf-8")

    result = subprocess.run([sys.executable, probe.name], cwd=tmp_path, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=60, check=False)

    assert result.stdout.strip() == "SURVIVED", (
        f"notify() let a caller's broken __str__ escape:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}")


def test_an_unwritable_error_log_does_not_cost_the_notification(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    settings = AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                             error_log=str(tmp_path / "errors.log"))

    def refuse(*args: object, **kwargs: object) -> int:
        raise PermissionError("full disk")

    monkeypatch.setattr(alerting.os, "open", refuse)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert Alerter(settings).notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert "could not append to the error log" in caplog.text


def test_error_log_none_writes_nothing_and_reads_empty(
        tmp_path: Path, channels: dict[str, Spy], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    error_log=None))
    assert alerter.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert alerter.read_errors(limit=0) == []
    assert list(tmp_path.iterdir()) == []


def test_the_log_rolls_and_the_reader_spans_the_rotation(tmp_path: Path) -> None:
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"),
                             error_log_max_bytes=400)
    alerter = Alerter(settings)
    for i in range(20):
        alerter.notify(ERROR, f"svc: cond-{i}", "x" * 50)

    assert Path(f"{settings.error_log}.1").exists(), "the log never rolled"
    titles = [r["title"] for r in alerter.read_errors(limit=0)]
    assert titles == sorted(titles, key=lambda t: int(t.rsplit("-", 1)[1])), "not oldest-first"
    assert "svc: cond-19" in titles


def test_the_reader_and_the_roller_agree_about_generation_count(tmp_path: Path) -> None:
    """A reader that looks for fewer generations than the writer keeps silently loses the oldest
    records — a data-loss bug no test of either side alone would catch."""
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"),
                             error_log_max_bytes=300, error_log_backups=3)
    alerter = Alerter(settings)
    for i in range(40):
        alerter.notify(ERROR, f"svc: cond-{i}", "y" * 60)

    on_disk = sum(1 for i in (1, 2, 3) if Path(f"{settings.error_log}.{i}").exists())
    assert on_disk == 3
    read_back = len(alerter.read_errors(limit=0))
    from_all_files = sum(
        len([line for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()])
        for p in [f"{settings.error_log}.{i}" for i in (1, 2, 3)] + [str(settings.error_log)]
    )
    assert read_back == from_all_files


def test_the_reader_returns_the_newest_limit_records(tmp_path: Path) -> None:
    settings = AlertSettings(service="svc", error_log=str(tmp_path / "errors.log"))
    alerter = Alerter(settings)
    for i in range(10):
        alerter.notify(ERROR, f"svc: cond-{i}", "...")
    assert [r["title"] for r in alerter.read_errors(limit=3)] == [
        "svc: cond-7", "svc: cond-8", "svc: cond-9"]


def test_the_reader_skips_a_truncated_final_line(tmp_path: Path) -> None:
    path = tmp_path / "errors.log"
    path.write_text('{"ts": "2026-08-30T10:00:00Z", "title": "good"}\n{"ts": "trunc',
                    encoding="utf-8")
    assert [r["title"] for r in alerting.read_jsonl_tail(str(path), limit=0)] == ["good"]


def test_an_absurd_limit_is_clamped_rather_than_raising(tmp_path: Path) -> None:
    """`deque(maxlen=)` narrows to a C ssize_t and raises `OverflowError` above 2**63."""
    path = tmp_path / "errors.log"
    path.write_text('{"ts": "2026-08-30T10:00:00Z", "title": "one"}\n', encoding="utf-8")
    assert len(alerting.read_jsonl_tail(str(path), limit=99999999999999999999)) == 1


# ---- the `since` window ------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "2026-08-30T10:00:00Z",
    "2026-08-30T10:00:00z",
    "2026-08-30T10:00:00+00:00",
])
def test_parse_since_normalises_the_z_suffix(text: str) -> None:
    """⭐ `fromisoformat` only learned `Z` in 3.11, and 3.10 is supported — AND `Z` is exactly
    what this module's own sink writes, so an unnormalised parser fails on its own records."""
    stamp = alerting.parse_since(text)
    assert stamp is not None
    assert stamp.isoformat() == "2026-08-30T10:00:00+00:00"


def test_parse_since_is_none_for_no_bound() -> None:
    assert alerting.parse_since("") is None
    assert alerting.parse_since("   ") is None


def test_parse_since_raises_on_garbage() -> None:
    with pytest.raises(ValueError):
        alerting.parse_since("last tuesday")


def test_parse_since_raises_overflow_rather_than_500ing() -> None:
    """An endpoint must catch BOTH ValueError and OverflowError and answer 422."""
    with pytest.raises(OverflowError):
        alerting.parse_since("9999-12-31T23:59:59-05:00")


def test_the_since_window_compares_instants_not_strings(tmp_path: Path) -> None:
    """⭐ A string compare is wrong for two spellings that both look right: a non-UTC offset
    sorts below every `+00:00` record, and `Z` (0x5A) sorts above the `.` of a fraction."""
    path = tmp_path / "errors.log"
    path.write_text(
        '{"ts": "2026-08-30T13:00:00Z", "title": "before"}\n'
        '{"ts": "2026-08-30T15:00:00Z", "title": "after"}\n', encoding="utf-8")

    # 09:00 US-Central-ish == 14:00Z. A string compare would sort this BELOW both records and
    # return them both.
    kept = alerting.read_jsonl_tail(str(path), limit=0, since_iso="2026-08-30T09:00:00-05:00")
    assert [r["title"] for r in kept] == ["after"]


def test_a_record_with_an_unparseable_timestamp_is_kept(tmp_path: Path) -> None:
    """Dropping it would hide a corrupt writer behind an empty result."""
    path = tmp_path / "errors.log"
    path.write_text('{"ts": "not-a-time", "title": "anomaly"}\n', encoding="utf-8")
    kept = alerting.read_jsonl_tail(str(path), limit=0, since_iso="2026-08-30T00:00:00Z")
    assert [r["title"] for r in kept] == ["anomaly"]


# =============================================================================== settings & boot
def test_settings_refuse_a_blank_service_name() -> None:
    """At CONSTRUCTION, not at notify() — a startup crash the operator sees beats records that
    say "unknown" forever."""
    for bad in ["", "   ", None]:
        with pytest.raises(ValueError, match="service"):
            AlertSettings(service=bad)  # type: ignore[arg-type]


def test_settings_strip_the_service_name() -> None:
    assert AlertSettings(service="  svc  ").service == "svc"


@pytest.mark.parametrize("name", ["error_log_max_bytes", "error_log_backups", "max_record_field",
                                  "max_tracked_conditions"])
@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "10"])
def test_settings_refuse_a_non_positive_sizing_knob(name: str, bad: object) -> None:
    with pytest.raises(ValueError, match=name):
        AlertSettings(service="svc", **{name: bad})  # type: ignore[arg-type]


def test_warn_if_unconfigured_names_the_ready_channels(
        alerter: Alerter, settings: AlertSettings, caplog: pytest.LogCaptureFixture) -> None:
    write_email_config(settings)
    with caplog.at_level(logging.INFO, logger="kw_common.alerting"):
        assert alerter.warn_if_unconfigured() == ["email", "ntfy"]
    assert "email + ntfy ready for svc" in caplog.text


def test_warn_if_unconfigured_shouts_when_nothing_is_usable(
        caplog: pytest.LogCaptureFixture) -> None:
    alerter = Alerter(AlertSettings(service="svc"))
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert alerter.warn_if_unconfigured() == []
    assert "ALERTING UNCONFIGURED for svc" in caplog.text
    assert "go NOWHERE" in caplog.text


def test_warn_if_unconfigured_reports_the_dedup_opt_out(
        caplog: pytest.LogCaptureFixture) -> None:
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t"))
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        alerter.warn_if_unconfigured()
    assert "disables de-duplication" in caplog.text


def test_warn_if_unconfigured_reports_an_unmounted_error_log_volume(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    missing = tmp_path / "not-mounted" / "logs" / "svc-errors.log"
    alerter = Alerter(AlertSettings(service="svc", ntfy_url="https://ntfy.example.com/t",
                                    error_log=str(missing)))
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        alerter.warn_if_unconfigured()
    assert "does not exist" in caplog.text
    assert "the volume is not mounted" in caplog.text


def test_warn_if_unconfigured_cannot_raise(monkeypatch: pytest.MonkeyPatch,
                                           caplog: pytest.LogCaptureFixture) -> None:
    def explode(self: Alerter) -> AlertConfig:
        raise RuntimeError("boom")

    monkeypatch.setattr(Alerter, "config", explode)
    with caplog.at_level(logging.WARNING, logger="kw_common.alerting"):
        assert Alerter(AlertSettings(service="svc")).warn_if_unconfigured() == []
    assert "ALERTING CONFIG UNREADABLE" in caplog.text


def test_state_file_problem_reports_a_missing_directory(tmp_path: Path) -> None:
    alerter = Alerter(AlertSettings(service="svc",
                                    state_file=str(tmp_path / "nope" / "state.json")))
    assert "does not exist" in alerter.state_file_problem()


def test_a_healthy_state_file_has_no_problem(tmp_path: Path) -> None:
    alerter = Alerter(AlertSettings(service="svc", state_file=str(tmp_path / "state.json")))
    assert alerter.state_file_problem() == ""


# =============================================================== the process-default convenience
# (`_reset_default` in conftest.py clears the installed default around every test.)
def test_configure_installs_the_default_and_returns_it(settings: AlertSettings,
                                                       channels: dict[str, Spy]) -> None:
    installed = alerting.configure(settings)
    assert alerting.current_alerter() is installed
    assert alerting.notify(ERROR, "svc: x", "...")["ntfy"] == "sent"
    assert channels["ntfy"].calls[0][2] == "svc: x"


def test_module_notify_without_configure_still_logs_and_never_raises(
        caplog: pytest.LogCaptureFixture) -> None:
    """A caller inside an `except` handler must not be punished for a startup mistake — but the
    reason its alert went nowhere has to be stated, loudly."""
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        results = alerting.notify(ERROR, "svc: db down", "refused")
    assert results == {"email": "skipped", "ntfy": "skipped"}
    assert "is NOT configured" in caplog.text
    assert "svc: db down" in caplog.text, "the log line must go out regardless"
    assert "refused" in caplog.text


def test_module_warn_if_unconfigured_without_configure_names_the_service(
        caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="kw_common.alerting"):
        assert alerting.warn_if_unconfigured("svc") == []
    assert "svc" in caplog.text


def test_configure_replaces_a_previous_default(settings: AlertSettings) -> None:
    first = alerting.configure(settings)
    second = alerting.configure(AlertSettings(service="other"))
    assert alerting.current_alerter() is second
    assert second is not first


# ====================================================================== the public API surface
def test_everything_in_dunder_all_exists() -> None:
    """`__all__` is the semver contract. A name in it that does not resolve is a broken import
    for every consumer that follows the documentation."""
    missing = [name for name in alerting.__all__ if not hasattr(alerting, name)]
    assert missing == []


def test_the_extracted_surface_is_exported() -> None:
    """The symbols this library was created to stop six repos from copying."""
    for name in ["notify", "warn_if_unconfigured", "configure", "AlertConfig", "AlertSettings",
                 "Alerter", "smtp_port_fault", "read_jsonl_tail", "parse_since",
                 "MIN_SMTP_PORT", "MAX_SMTP_PORT", "OK", "WARN", "ERROR"]:
        assert name in alerting.__all__, f"{name} must be part of the public contract"


def test_the_port_bounds_are_a_tcp_port() -> None:
    assert (MIN_SMTP_PORT, MAX_SMTP_PORT) == (1, 65535)
