"""What the EXTRACTION changed: the guard runs from an installed package, configured from outside.

WHY THIS FILE EXISTS, separately from the two suites beside it
    `test_leak_guard_range.py` and `test_leak_guard_engine_adds.py` are the ENGINE's tests. They
    travelled here from the repository that owned the engine and they assert what a scan does.
    This file asserts the properties that only exist because the engine is now a PACKAGE:

      * a repository injects its allowances from a file the installer never touches;
      * a configuration that would gut the guard is refused rather than obeyed;
      * the self-exemption follows the guard's own file, so an INSTALLED guard exempts nothing in
        the repository it scans — which is the blind spot the constant fallback used to leave;
      * the four engine-add surfaces still catch what they caught, in that new posture;
      * the module can be imported, invoked as a console script, and carries no repository name it
        should not publish.

⚠️ EVERY LEAKING LITERAL HERE IS ASSEMBLED AT RUNTIME, for the reason the sibling suites give and
    one more besides: this file is scanned by the guard in this repository's own CI, and an
    INSTALLED guard recognises nothing here as its own source, so there is no self-exemption to
    fall back on. Every value is synthetic — RFC1918 and CGNAT documentation shapes, `.invalid`
    hosts, `/mnt/<pool>` paths that name no real pool.

⚠️ THE POSTURE UNDER TEST IS "GUARD OUTSIDE THE REPOSITORY". Every subprocess below runs the
    module's own file against a scratch repository it is not inside, which is exactly what
    `site-packages` looks like to a scan. That is not a simulation of the installed case; it is
    the same relationship between the two paths, which is all the self-exemption looks at.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from kw_common import leakguard as guard

_SCRIPT = Path(guard.__file__).resolve()
_REPO_ROOT = Path(__file__).resolve().parents[1]

# ⚠️ ASSEMBLED, never spelled. Neither fragment matches a pattern alone.
_POOL = "/mnt/" + "user"
_LEAK_PATH = f"{_POOL}/appdata/svc/data"
_HOST = "nas-a." + "lan"
_ADDR = "192.168." + "77.77"
# ⚠️ A SECOND ADDRESS, and it has to be a different one. `_ADDR` is the address the engine's own
# `_MUST_FAIL` corpus uses, so allowing it genuinely DOES defeat three deny cases — measured, when
# the narrow-allowance test below was first written with it and was correctly refused. Any test
# about an allowance that should be ACCEPTED needs a value the corpus does not use.
_ADDR_NOT_IN_THE_CORPUS = "192.168." + "12.34"

# The path every repository in this fleet vendored the guard at, and the value the deleted
# `SELF_PATH` constant held. It is the decoy in the blind-spot tests for that reason.
_VENDORED_REL = "scripts/check_no_internal_info.py"

_PINNED = ("-c", "user.name=extraction test", "-c", "user.email=t@example.com",
           "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *_PINNED, *args], cwd=repo, capture_output=True, check=True,
                         timeout=300)
    return out.stdout.decode("utf-8", errors="replace")


def _repo(tmp_path: Path, name: str) -> Path:
    """A scratch repository with one ordinary clean file already tracked.

    ⚠️ THE CLEAN FILE IS NOT DECORATION. A tree whose only tracked file is the one under test can
    become a tree the scan reads NOTHING from, and the zero-scan floor refuses that rather than
    reporting clean — correctly, but it would mean these tests were measuring the floor.
    """
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("an ordinary clean file\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    return repo


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    """The guard, from OUTSIDE `repo` — the installed posture. Bounded: it may not hang."""
    return subprocess.run([sys.executable, str(_SCRIPT), "--repo", str(repo), *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300)


def _out(res: subprocess.CompletedProcess) -> str:
    return res.stdout + res.stderr


def _write_config(repo: Path, config: object) -> None:
    (repo / guard.CONFIG_FILENAME).write_text(json.dumps(config, indent=2), encoding="utf-8")
    _git(repo, "add", guard.CONFIG_FILENAME)


# =================================================================================================
# 1. A consumer's allow-list is honoured WITHOUT touching installed code
# =================================================================================================


def test_an_injected_literal_clears_a_leak_that_reds_without_it(tmp_path: Path) -> None:
    """⭐ THE HEADLINE PROPERTY, and both directions are the test.

    A repository supplies its allowances in a file the installer never writes to. Asserting only
    that the configured tree passes would be equally true of a guard that had stopped scanning;
    asserting only that the unconfigured tree reds says nothing about the configuration. The pair
    is what proves the file did the work.

    ⚠️ NOTHING IS EDITED POST-INSTALL, which is the acceptance criterion in one line. The guard
    runs from outside the repository throughout — the same file, the same bytes, both times.
    """
    repo = _repo(tmp_path, "injected")
    (repo / "notes.md").write_text(f"path: {_LEAK_PATH}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")

    before = _run(repo)
    assert before.returncode == 1, (
        f"the leak was not caught WITHOUT a config, so this test cannot show a config clearing "
        f"it:\n{_out(before)}")
    # ⚠️ THE POOL FRAGMENT, not the whole path: the guard reports the MATCH rather than the line
    # or the value the reader wrote, so asserting on the full path would be asserting on a message
    # format the guard has never used.
    #
    # ⛔ AND THE MATCH IS NOT SPELLED IN THIS COMMENT, which the guard itself caught when it was.
    # A comment explaining a deny shape is still a line of a scanned file — the same trap the
    # sibling suites warn about, walked into while documenting it.
    assert _POOL in _out(before), _out(before)

    _write_config(repo, {"allow_literals": [
        {"literal": _LEAK_PATH, "why": "test fixture: a path this repository is allowed to name"}]})

    after = _run(repo)
    assert after.returncode == 0, (
        f"the injected allow-literal was not honoured:\n{_out(after)}")


def test_the_config_is_read_from_the_SCANNED_repository_not_from_the_installed_package(
    tmp_path: Path,
) -> None:
    """Two repositories, one guard, two different answers — which is the whole point.

    ⛔ THE FAILURE THIS RULES OUT is a config cached on, or baked into, the module: the same
    installed guard must clear the repository that allows this literal and RED the one that does
    not, in the same session and with no reinstallation between them.
    """
    allowed = _repo(tmp_path, "allowed")
    (allowed / "notes.md").write_text(f"path: {_LEAK_PATH}\n", encoding="utf-8")
    _git(allowed, "add", "notes.md")
    _write_config(allowed, {"allow_literals": [
        {"literal": _LEAK_PATH, "why": "test fixture"}]})

    denied = _repo(tmp_path, "denied")
    (denied / "notes.md").write_text(f"path: {_LEAK_PATH}\n", encoding="utf-8")
    _git(denied, "add", "notes.md")

    assert _run(allowed).returncode == 0, "the allowing repository's config was not read"
    assert _run(denied).returncode == 1, (
        "the second repository was cleared by the FIRST repository's config — the allowances are "
        "leaking across scans, or are being cached in the installed package")


def test_an_explicit_config_path_is_honoured_and_a_missing_one_is_an_ERROR(
    tmp_path: Path,
) -> None:
    """`--config` names a file; a path that does not exist must not fall back to discovery.

    ⛔ THE FALLBACK IS THE BUG. A typo'd `--config` that quietly scanned under default rules would
    report a verdict the operator reads as authoritative and that measures something else.
    """
    repo = _repo(tmp_path, "explicit")
    (repo / "notes.md").write_text(f"path: {_LEAK_PATH}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")

    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"allow_literals": [
        {"literal": _LEAK_PATH, "why": "test fixture"}]}), encoding="utf-8")

    assert _run(repo, "--config", str(elsewhere)).returncode == 0, (
        "an explicitly named config outside the repository was not read")
    assert _run(repo, f"--config={elsewhere}").returncode == 0, (
        "the joined `--config=` form was not accepted, though `--repo=` is")

    missing = _run(repo, "--config", str(tmp_path / "nope.json"))
    assert missing.returncode == 2, (
        f"a --config path that does not exist must be a usage error, not a silent fall back to "
        f"the repository's own file or to the defaults:\n{_out(missing)}")
    # ⛔ AND IT MUST SAY SO. Exit 2 alone is satisfied by a DIFFERENT error path — delete the
    # `is_file()` guard and `load_config` raises "cannot be read (FileNotFoundError)", which is
    # also exit 2 and reads like a permissions problem. The operator has to be told which of the
    # two happened, and this assertion is what stops the specific message being lost.
    assert "no such file" in _out(missing), (
        f"the error does not say the named config is missing, so it is indistinguishable from an "
        f"unreadable or malformed one:\n{_out(missing)}")


def test_selftest_REFUSES_a_config_rather_than_ignoring_one(tmp_path: Path) -> None:
    """⚠️ A MUTATION SURVIVOR until this was written, which is why it is here.

    `--selftest` measures the SHIPPED patterns and reads no repository, so a config passed with it
    can only be ignored. Ignoring it silently is the failure: the operator has asked "are my rules
    safe?" and been told "the patterns still bite", which is a different question with the same
    exit code. What actually measures a repository's allowances is the refusal in `apply_config`,
    and that runs on a real scan.
    """
    repo = _repo(tmp_path, "selftest_config")
    _write_config(repo, {"allow_literals": [{"literal": "x", "why": "test fixture"}]})
    res = _run(repo, "--selftest", "--config", str(repo / guard.CONFIG_FILENAME))
    assert res.returncode == 2, (
        f"--selftest accepted a --config it cannot act on:\n{_out(res)}")
    assert "--selftest measures the shipped patterns" in _out(res), _out(res)
    assert _run(repo, "--selftest").returncode == 0, (
        "the plain self-test no longer passes, so the case above proves nothing about --config")


def test_no_config_file_means_NOTHING_is_allowed(tmp_path: Path) -> None:
    """The default direction, asserted rather than assumed.

    A repository with no config gets the strictest possible scan, so a config that is missing,
    misnamed, or never committed can only ever make the guard LOUDER. The opposite default would
    mean an installed library silently permitting values in a repository that never asked.
    """
    repo = _repo(tmp_path, "noconfig")
    (repo / "notes.md").write_text(f"path: {_LEAK_PATH}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    assert not (repo / guard.CONFIG_FILENAME).exists(), "vacuity guard: there must be no config"
    assert _run(repo).returncode == 1, "an unconfigured repository was cleared"
    assert guard.DEFAULT_CONFIG.allow_literals == () and guard.DEFAULT_CONFIG.path_exempt == (), (
        "the shipped default allows something; a library must assume nothing about its consumer")


# =================================================================================================
# 2. A configuration that would gut the guard is REFUSED
# =================================================================================================


def test_a_config_that_defeats_a_DENY_CASE_is_refused(tmp_path: Path) -> None:
    """⛔⛔ THE RISK THE INJECTION ITSELF CREATES, closed in the same change that creates it.

    An allow-literal suppresses any hit inside it, so one wide literal would turn a whole pattern
    off across a tree while CI stayed green and said "no internal info found" — the "guard switched
    off" outcome the module's docstring is written against, reduced to a one-line edit by moving
    the allowances into a file the consumer owns.
    """
    repo = _repo(tmp_path, "defeats")
    _write_config(repo, {"allow_literals": [
        {"literal": _POOL, "why": "test fixture: wide enough to swallow the pool-path deny case"}]})

    res = _run(repo)
    assert res.returncode == 2, (
        f"a configuration that stops a deny case being caught was accepted:\n{_out(res)}")
    assert "DEFEATS" in _out(res), _out(res)
    assert "unraid pool path" in _out(res), (
        f"the refusal does not say WHICH deny case it defeats, so nobody can narrow it:"
        f"\n{_out(res)}")


def test_a_NARROW_allowance_is_still_accepted(tmp_path: Path) -> None:
    """The other direction, and the one that stops the check above being a guard that says no.

    ⚠️ THIS IS ALSO THE HONEST STATEMENT OF WHAT THE REFUSAL DOES NOT COVER. The corpus holds one
    sample per pattern and a permitted span only suppresses a hit it fully CONTAINS, so a single
    real address can still be allowed one line at a time. That is not the hole the check closes:
    each such entry is explicit, justified, and visible in the repository's own file — which is
    more than the hand-edited engine tail it replaces ever was.
    """
    repo = _repo(tmp_path, "narrow")
    (repo / "notes.md").write_text(f"addr {_ADDR_NOT_IN_THE_CORPUS}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    # ⚠️ THE "BEFORE" DIRECTION, which this test lacked. Asserting only that the configured tree
    # passes is equally true of a pattern that had stopped matching the value altogether — it
    # would go on passing while measuring nothing.
    assert _run(repo).returncode == 1, (
        "vacuity guard: the address must be caught WITHOUT the config, or a clean verdict below "
        "says nothing about the allowance")
    _write_config(repo, {"allow_literals": [
        {"literal": _ADDR_NOT_IN_THE_CORPUS,
         "why": "test fixture: one address, not the whole pattern"}]})

    res = _run(repo)
    assert res.returncode == 0, (
        f"a single-value allowance was refused as though it gutted the pattern:\n{_out(res)}")


@pytest.mark.parametrize(
    "body,expected_in_message",
    [
        pytest.param("not json at all", "not valid JSON", id="not-json"),
        pytest.param('["a", "b"]', "top level must be an object", id="top-level-not-object"),
        pytest.param('{"allow_litrals": []}', "unknown key", id="typo-in-a-real-key"),
        pytest.param('{"allow_literals": {"literal": "x"}}', "must be a list", id="not-a-list"),
        pytest.param('{"allow_literals": ["x"]}', "must be an object", id="bare-string-entry"),
        pytest.param('{"allow_literals": [{"literal": "x"}]}', "'why'", id="no-justification"),
        pytest.param('{"allow_literals": [{"literal": "", "why": "w"}]}', "non-empty",
                     id="empty-literal"),
        pytest.param('{"path_exempt": [{"pattern": "nope", "path_regex": "^a$", "why": "w"}]}',
                     "names no pattern", id="unknown-pattern-label"),
        pytest.param('{"path_exempt": [{"pattern": "cgnat address", "path_regex": "^[", '
                     '"why": "w"}]}', "not a valid regex", id="uncompilable-regex"),
        pytest.param('{"path_exempt": [{"patern": "cgnat address", "path_regex": "^a$", '
                     '"why": "w"}]}', "unknown key", id="typo-in-an-entry-key"),
    ],
)
def test_a_configuration_this_scanner_cannot_understand_STOPS_the_scan(
    tmp_path: Path, body: str, expected_in_message: str,
) -> None:
    """⛔ EVERY REJECTION FAILS THE SCAN, and none of them degrades to "no config".

    The difference between "your rules were applied" and "your rules were unreadable, so none
    were" is invisible in a clean verdict, and a leak guard whose configuration can be silently
    misread is a leak guard whose verdict means nothing. Each case below is a mistake somebody
    makes: a typo in a key, a list where an object goes, a justification left off, a regex that
    does not compile.
    """
    repo = _repo(tmp_path, "malformed")
    (repo / guard.CONFIG_FILENAME).write_text(body, encoding="utf-8")
    _git(repo, "add", guard.CONFIG_FILENAME)

    res = _run(repo)
    assert res.returncode == 2, f"a malformed config did not stop the scan:\n{_out(res)}"
    assert expected_in_message in _out(res), (
        f"the error does not say what is wrong ({expected_in_message!r} not in it):\n{_out(res)}")


def test_a_key_prefixed_with_an_underscore_is_a_COMMENT(tmp_path: Path) -> None:
    """JSON has no comments and a config file has to explain itself somewhere.

    ⛔ AND IT IS THE ONLY IGNORED SPELLING. "Ignore anything unrecognised" would swallow
    `allow_litrals` — a typo that silently drops a repository's whole allow-list while the scan
    reports a confident pass. No key this scanner reads begins with `_`, so the two cases cannot
    be confused, which is what the case above and this one assert together.
    """
    repo = _repo(tmp_path, "comment")
    (repo / "notes.md").write_text(f"path: {_LEAK_PATH}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    _write_config(repo, {
        "_why": "a note to whoever reads this file next",
        "allow_literals": [{"literal": _LEAK_PATH, "why": "test fixture"}]})

    res = _run(repo)
    assert res.returncode == 0, (
        f"an `_`-prefixed comment key was treated as an error:\n{_out(res)}")


def test_this_repositorys_OWN_config_parses_and_is_honoured(injected_literals) -> None:
    """The worked example is exercised, not just shipped.

    ⭐ THE REAL FILE, because a fixture built inline would pass just as happily with
    `.leakguard.json` deleted or unparseable. This is what makes a broken config in THIS repository
    a red test here rather than a discovery in somebody else's CI.
    """
    assert injected_literals.allow_literals, "this repository declares no allow_literals"
    for literal in injected_literals.allow_literals:
        line = f"see https://{literal}/kw-common for the source"
        spans = guard._permitted_spans(line)
        start = line.index(literal)
        assert (start, start + len(literal)) in spans, (
            f"{literal!r} is configured but is not recognised as a permitted span")


# =================================================================================================
# 3. The self-exemption blind spot an INSTALLED guard used to have
# =================================================================================================


def test_a_repo_whose_only_leak_is_in_ITS_OWN_guard_file_still_REDS(tmp_path: Path) -> None:
    """⛔⛔ THE BLIND SPOT, reproduced against the posture that has it.

    The exemption used to resolve `__file__` relative to the scanned root and FALL BACK to a
    constant — `scripts/check_no_internal_info.py` — when that failed. It fails on every run of an
    INSTALLED guard, because `site-packages` is not inside the repository being scanned. So the
    fallback silently exempted whatever the scanned repository kept at that path, which is the
    exact path every repository in this fleet vendored its copy at: the first consumer to install
    the package and delete its old script would have handed a permanent, invisible amnesty to any
    file that later took that name.

    The file planted here is NOT a copy of the guard. It is an ordinary file at the old path with
    a single leak in it, which is what makes this a question about the exemption rather than about
    the guard's own synthetic corpus.
    """
    repo = _repo(tmp_path, "blindspot")
    decoy = repo / _VENDORED_REL
    decoy.parent.mkdir(parents=True)
    decoy.write_text(f"AGENT_URL=http://{_HOST}:9999/mcp\n", encoding="utf-8")
    _git(repo, "add", _VENDORED_REL)

    res = _run(repo)
    assert res.returncode == 1, (
        f"a leak at the old vendored guard path was exempted by an installed guard — the "
        f"self-exemption is landing on a PATH rather than on the guard's own file:\n{_out(res)}")
    assert _VENDORED_REL in _out(res), _out(res)


def test_the_same_leak_at_that_path_reds_the_RANGE_scan_too(tmp_path: Path) -> None:
    """Both scans, because they resolve the exemption separately and have disagreed before.

    A path wrongly judged self drops out of the range scan's `pending` list, and an empty
    `pending` returns early DISCARDING the unscannable list — so a mismatch there is not a
    cosmetic difference between two verdicts, it is a blob reported clean on the push path.
    """
    repo = _repo(tmp_path, "blindspot_range")
    _git(repo, "commit", "-q", "-m", "seed")
    base = _git(repo, "rev-parse", "HEAD").strip()
    decoy = repo / _VENDORED_REL
    decoy.parent.mkdir(parents=True)
    decoy.write_text(f"addr {_ADDR}\n", encoding="utf-8")
    _git(repo, "add", _VENDORED_REL)
    _git(repo, "commit", "-q", "-m", "add a file at the old vendored path")

    res = _run(repo, "--range", f"{base}..HEAD")
    assert res.returncode == 1, (
        f"the range scan exempted a leak at the old vendored guard path:\n{_out(res)}")
    # ⛔ AND IT MUST BE THAT FILE. Exit 1 also means "something unreadable, or a range git could
    # not resolve", so the exit code alone does not pin the surface — the tree half of this pair
    # asserts the path for exactly that reason, and this half did not.
    assert _VENDORED_REL in _out(res), (
        f"the range scan RED for some reason other than the planted leak:\n{_out(res)}")


def test_a_guard_OUTSIDE_the_scanned_repo_resolves_NO_self_path() -> None:
    """The unit form of the two above: the resolver itself must answer `None`, not a guess.

    ⚠️ PINNED DIRECTLY because the scans can only show the CONSEQUENCE. A resolver that started
    returning a default again would be caught here in milliseconds and in the tests above only if
    somebody happened to plant a file at whatever the new default was.
    """
    outside = Path(__file__).resolve().parent / "nowhere-that-contains-the-guard"
    assert guard._self_rel_path(outside) is None, (
        "the guard resolved a self-path for a root it is not inside; a fallback has come back")
    assert guard._self_rel_path(None) is None, (
        "the guard resolved a self-path with no root at all — there is nothing to be relative to")
    assert not guard._is_self(_VENDORED_REL, outside), (
        "the old vendored path is exempt again")
    assert not hasattr(guard, "SELF_PATH"), (
        "the SELF_PATH constant is back. It is the fallback that created the blind spot; the "
        "exemption must be derived from __file__ or not granted at all")


def test_a_VENDORED_guard_still_exempts_its_own_source(tmp_path: Path) -> None:
    """The direction that must NOT break: a repository that keeps a copy still skips it.

    Removing the fallback narrows the exemption; it must not remove it. A guard run from inside
    the repository it scans has to recognise its own file, or every one of its synthetic deny
    cases becomes a finding and the repository can never be clean.
    """
    repo = _repo(tmp_path, "vendored")
    vendored = repo / _VENDORED_REL
    vendored.parent.mkdir(parents=True)
    vendored.write_bytes(_SCRIPT.read_bytes())
    _git(repo, "add", _VENDORED_REL)

    res = subprocess.run([sys.executable, str(vendored), "--repo", str(repo)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=300)
    assert res.returncode == 0, (
        f"a vendored guard reported its OWN synthetic deny cases as findings:"
        f"\n{res.stdout}{res.stderr}")


# =================================================================================================
# 4. The four engine-add surfaces, in the new posture
# =================================================================================================


def test_the_four_engine_add_surfaces_still_catch_from_an_INSTALLED_guard(
    tmp_path: Path,
) -> None:
    """⭐ ONE PLANT PER SURFACE, all four in one repository, all four from outside it.

    The sibling suite proves each surface against the engine. This proves the same four survive
    the thing this release changed — the guard being installed rather than vendored, and reading
    a configuration from the repository. A surface that quietly stopped being scanned in that
    posture would leave every one of those tests green.

    The four, and what each one exists for:
      * a leaking PATH — published, in every clone, and read by nothing until it was added;
      * a leaking commit-message BODY — rendered on every commit page, removable only by a rewrite;
      * a leak only in the INDEX — staged, then tidied in the worktree, so the tree scan sees
        nothing and the commit records it anyway;
      * a leak under a SKIP-SUFFIX name — renaming a text file must not defeat a guard.
    """
    # ---- surface 1: the PATH, under a skip suffix, which is surfaces 1 and 4 in one plant is
    # NOT what is done here — they are planted separately so a single fix cannot cover both.
    repo = _repo(tmp_path, "surfaces")
    _git(repo, "commit", "-q", "-m", "seed")
    base = _git(repo, "rev-parse", "HEAD").strip()

    path_leak = repo / f"{_ADDR}.conf"
    path_leak.write_text("nothing to see here\n", encoding="utf-8")
    _git(repo, "add", str(path_leak.name))
    tree = _run(repo)
    assert tree.returncode == 1 and "<path>" in _out(tree), (
        f"surface 1 (a leaking PATH) is not caught:\n{_out(tree)}")
    path_leak.unlink()
    _git(repo, "rm", "-q", "--cached", path_leak.name)

    # ---- surface 4: the same question under a name the suffix list would skip.
    asset = repo / f"icons/{_HOST}.png"
    asset.parent.mkdir()
    asset.write_text("this is ASCII text, whatever the name says\n", encoding="utf-8")
    _git(repo, "add", "icons")
    assert guard._binary_suffix(f"icons/{_HOST}.png"), "vacuity guard: .png must be a skip suffix"
    suffixed = _run(repo)
    assert suffixed.returncode == 1, (
        f"surface 4 (a leak under a skip-suffix NAME) is not caught:\n{_out(suffixed)}")
    _git(repo, "rm", "-q", "-r", "--cached", "icons")

    # ---- surface 3: the INDEX. Staged with the leak, then tidied in the worktree, so only the
    # index carries it — which is exactly what the commit would record.
    staged_file = repo / "cfg.txt"
    staged_file.write_text(f"AGENT_URL=http://{_HOST}:9999/mcp\n", encoding="utf-8")
    _git(repo, "add", "cfg.txt")
    staged_file.write_text("AGENT_URL=http://svc.your-domain.example:9999/mcp\n",
                           encoding="utf-8")
    assert _HOST in _git(repo, "cat-file", "blob", ":cfg.txt"), "vacuity guard: the index must leak"
    assert _run(repo).returncode == 0, (
        "precondition: the WORKTREE is clean, so a red below would not be about the index")
    staged = _run(repo, "--staged")
    assert staged.returncode == 1, (
        f"surface 3 (a leak only in the INDEX) is not caught:\n{_out(staged)}")

    # ---- surface 2: the commit MESSAGE body.
    _git(repo, "commit", "-q", "-m", "add a config")
    _git(repo, "commit", "-q", "--allow-empty", "-m",
         f"tidy up\n\nran it against {_ADDR} before merging\n")
    message = _run(repo, "--range", f"{base}..HEAD")
    assert message.returncode == 1, (
        f"surface 2 (a leaking commit-message BODY) is not caught:\n{_out(message)}")
    # ⛔ THE FINDING LINE, NOT THE OUTPUT. A range report always prints a remediation footer
    # containing the literal "`<commit message>` finding is the message, not the diff", so
    # `"<commit message>" in output` is satisfied by BOILERPLATE — it is true of a range whose
    # only finding is a diff line and whose messages are spotless. Verified: a verification agent
    # constructed exactly that and the assertion held. Findings are the indented lines.
    finding_lines = [ln for ln in _out(message).splitlines() if ln.startswith("  ")]
    assert any("<commit message>" in ln for ln in finding_lines), (
        f"no FINDING is attributed to the message surface — the diff or the identity may be what "
        f"was reported:\n{_out(message)}")



# -------------------------------------------------------------------------------------------
# The gate found every one of the following, in code this package invented. Each is pinned in
# BOTH directions, because a refusal that only ever says no is not a check.
# -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path_regex",
    [".", ".+", ".*", "(tests/fixtures/)?", "^", "[a-z./]*", "docs|src|tests|README"],
    ids=["any-char", "one-or-more", "zero-or-more", "optional-group", "empty-anchor",
         "char-class-star", "alternation-over-everything"],
)
def test_a_WIDE_path_exempt_is_refused(tmp_path: Path, path_regex: str) -> None:
    """⛔⛔ A COMPLETE OFF-SWITCH, found by three independent verification agents at once.

    `_deny_cases_defeated` used to measure the deny corpus with NO `rel_path`, and `scan_text`
    consults `_exempt` only `if rel_path` — so the "this configuration DEFEATS the guard's own
    deny cases" refusal was structurally blind to `path_exempt`. A repository could write one
    entry with a regex matching every path, silence a pattern across its whole tree on both the
    content and the path surfaces, and have the config ACCEPTED. Measured before the fix: a tree
    with an RFC1918 address in a tracked file scanned `no internal info found`, exit 0, in all
    three scan modes.

    ⚠️ THE CHECK IS NOT A REGEX-SHAPE RULE. "It must be anchored", "it must not match the empty
    string" — `.+` is neither anchored nor empty-matching and is still total, and this file's own
    history is a list of matchers that lost that arms race. What is measured is the PROPERTY: a
    path exemption may not stop a deny case being caught on a path an ordinary repository has.
    """
    repo = _repo(tmp_path, f"wide_{abs(hash(path_regex))}")
    (repo / "leak.txt").write_text(f"DATABASE_URL=postgres://u:p@{_ADDR}:5432/db\n",
                                   encoding="utf-8")
    _git(repo, "add", "leak.txt")
    assert _run(repo).returncode == 1, "vacuity guard: the leak must be caught without the config"

    _write_config(repo, {"path_exempt": [
        {"pattern": "private IPv4 (RFC1918)", "path_regex": path_regex,
         "why": "test fixture: wide enough to be an off-switch"}]})
    res = _run(repo)
    assert res.returncode == 2, (
        f"a path_exempt regex that matches every path was accepted — the guard can be switched "
        f"off from a config file:\n{_out(res)}")
    assert "DEFEATS" in _out(res), _out(res)


def test_a_NARROW_path_exempt_is_still_accepted_and_honoured(tmp_path: Path) -> None:
    """The direction that must NOT break: a scoped exemption is the mechanism's whole purpose.

    A repository that keeps its own synthetic deny cases — a fixtures directory, a vendored
    corpus — has to be able to say so. `^tests/fixtures/` matches none of the probe paths, so it
    is accepted, and the file under it really is excused.
    """
    repo = _repo(tmp_path, "narrowpath")
    fixtures = repo / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "hosts.txt").write_text(f"{_HOST}\n", encoding="utf-8")
    _git(repo, "add", "tests")
    assert _run(repo).returncode == 1, "vacuity guard: the fixture must red without the exemption"

    _write_config(repo, {"path_exempt": [
        {"pattern": "private lan domain", "path_regex": "^tests/fixtures/",
         "why": "synthetic hosts, needed to prove the pattern still bites"}]})
    res = _run(repo)
    assert res.returncode == 0, (
        f"a properly scoped path exemption was refused; the mechanism is now unusable:"
        f"\n{_out(res)}")

    # ...and it is scoped. The same host OUTSIDE the exempted path must still red.
    (repo / "prod.conf").write_text(f"AGENT=http://{_HOST}/\n", encoding="utf-8")
    _git(repo, "add", "prod.conf")
    assert _run(repo).returncode == 1, (
        "the exemption reached beyond its path regex — a scoped carve-out that is not scoped")


def test_an_allow_literal_may_not_defeat_the_PATH_or_MESSAGE_deny_cases(tmp_path: Path) -> None:
    """The refusal measures three surfaces, because `ALLOW_LITERALS` reaches three.

    ⛔ IT USED TO MEASURE ONLY FILE CONTENT. `_permitted_spans` is applied inside `scan_text`,
    which the PATH and MESSAGE scans call too — so a literal could be accepted while defeating
    three of the engine's own path and message deny cases at once. Measured before the fix: with
    that config applied, the shipped `--selftest` failed with two PATH cases and one MESSAGE case
    uncaught, and a real range scan cleared a leaking commit message and a leaking filename.
    """
    repo = _repo(tmp_path, "pathmsg")
    _write_config(repo, {"allow_literals": [
        {"literal": "host-a.lan.", "why": "test fixture: swallows the path and message cases"},
        {"literal": "host-a.lan.example", "why": "test fixture"}]})
    res = _run(repo)
    assert res.returncode == 2, (
        f"a literal that defeats the PATH and MESSAGE deny cases was accepted:\n{_out(res)}")
    assert "PATH" in _out(res) or "MESSAGE" in _out(res), (
        f"the refusal does not name the surface it protects, so nobody can act on it:"
        f"\n{_out(res)}")


def test_an_UNTRACKED_config_is_refused(tmp_path: Path) -> None:
    """⛔ THE JUSTIFICATION FOR INJECTION IS THAT THE ALLOWANCES BECOME REVIEWABLE.

    A `.leakguard.json` that is gitignored, or simply never added, is not reviewable: it governs
    every local scan — including the pre-commit and pre-push paths a developer meets most — while
    `git status` is empty and nothing in the repository shows why the scan is green. Measured
    before the fix: an ignored config allowing a `.lan` host turned the local scan green with
    nothing tracked to explain it.

    ⚠️ `--config` IS DELIBERATELY EXEMPT — it is written out in the invocation, which is the
    visibility that matters, and a config held outside the scanned repository is legitimate. That
    half is pinned by `test_an_explicit_config_path_is_honoured_and_a_missing_one_is_an_ERROR`.
    """
    repo = _repo(tmp_path, "untracked")
    (repo / "leak.txt").write_text(f"AGENT=http://{_HOST}/\n", encoding="utf-8")
    _git(repo, "add", "leak.txt")
    _git(repo, "commit", "-q", "-m", "seed")
    (repo / guard.CONFIG_FILENAME).write_text(
        json.dumps({"allow_literals": [{"literal": _HOST, "why": "test fixture"}]}),
        encoding="utf-8")
    (repo / ".gitignore").write_text(f"{guard.CONFIG_FILENAME}\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")

    res = _run(repo)
    assert res.returncode == 2, (
        f"an untracked config governed the scan; the allowances are invisible to review:"
        f"\n{_out(res)}")
    assert "NOT TRACKED" in _out(res), _out(res)

    # The other direction: the very same file, tracked, is honoured.
    _git(repo, "add", "-f", guard.CONFIG_FILENAME)
    assert _run(repo).returncode == 0, "a tracked config was not honoured"


def test_a_BOM_on_a_valid_config_does_not_red_the_build(tmp_path: Path) -> None:
    """⚠️ A GUARD THAT REDDENS CORRECT WORK GETS SWITCHED OFF, so this is a real defect class.

    Windows PowerShell 5.1 `Out-File` and Notepad's "Save As UTF-8" both write a BOM. Reading the
    config as `utf-8` rather than `utf-8-sig` turned a semantically perfect file into exit 2 on
    the workstation this fleet is operated from.
    """
    repo = _repo(tmp_path, "bom")
    (repo / "leak.txt").write_text(f"AGENT=http://{_HOST}/\n", encoding="utf-8")
    _git(repo, "add", "leak.txt")
    (repo / guard.CONFIG_FILENAME).write_text(
        json.dumps({"allow_literals": [{"literal": _HOST, "why": "test fixture"}]}),
        encoding="utf-8-sig")
    _git(repo, "add", guard.CONFIG_FILENAME)
    assert (repo / guard.CONFIG_FILENAME).read_bytes().startswith(b"\xef\xbb\xbf"), (
        "vacuity guard: the fixture is not actually BOM-prefixed")

    res = _run(repo)
    assert res.returncode == 0, (
        f"a BOM-prefixed but otherwise valid config reddened the scan:\n{_out(res)}")


def test_NO_scan_banner_claims_the_repository_is_public(tmp_path: Path) -> None:
    """⭐ THE CLASS, not the instance — which is how this one was missed the first time.

    The sentence was removed from the tree and range banners and left on `--staged`. It is true
    of at most two repositories in this fleet and is printed to every consumer of the guard, and
    it is precisely the sentence a reader uses to decide a finding does not apply to them.
    """
    repo = _repo(tmp_path, "banners")
    _git(repo, "commit", "-q", "-m", "seed")
    (repo / "s.py").write_text(f"ADDR = '{_ADDR}'\n", encoding="utf-8")
    _git(repo, "add", "s.py")

    staged = _run(repo, "--staged")
    _git(repo, "commit", "-q", "-m", "commit the leak")
    tree = _run(repo)
    rng = _run(repo, "--range", "HEAD~1..HEAD")

    for name, res in (("--staged", staged), ("tree", tree), ("--range", rng)):
        assert res.returncode == 1, f"vacuity guard: the {name} scan did not find the leak"
        assert "this repo is public" not in _out(res), (
            f"the {name} banner still claims the scanned repository is public:\n{_out(res)}")


@pytest.mark.timeout(300)
def test_a_REAL_leak_in_the_engines_OWN_source_reds_even_in_the_repo_that_owns_it(
    tmp_path: Path,
) -> None:
    """⛔⛔ THE ACCEPTANCE CRITERION, asked of the one repository it is hardest for.

    This repository's tracked engine source is full of synthetic deny cases by design, and an
    INSTALLED guard is not inside it, so the path-based self-exemption cannot fire. The first
    answer was a `path_exempt` entry per pattern in this repository's own config — and since the
    engine has exactly as many patterns as that list had entries, it was a whole-file skip written
    in data: a REAL leak appended to the engine's source PASSED. That is the `SELF_PATH` blind
    spot again, moved from code into configuration.

    The guard recognises its own source by its BYTES instead. Nothing else can claim that, and no
    configuration can widen it — change one character and the file is scanned like any other.

    ⚠️ THE CLEAN DIRECTION IS ASSERTED FIRST. Without it, a guard that had stopped recognising
    itself entirely would pass the interesting half of this test for the wrong reason.
    """
    own = _SCRIPT.read_bytes()
    repo = _repo(tmp_path, "ownsource")
    vendored = repo / "src" / "kw_common" / "leakguard.py"
    vendored.parent.mkdir(parents=True)
    vendored.write_bytes(own)
    _git(repo, "add", "src")

    assert _run(repo).returncode == 0, (
        "the guard did not recognise a byte-identical copy of its own source, so every synthetic "
        "deny case in it became a finding")

    vendored.write_bytes(own + f"\n# PLANTED: {_HOST} {_ADDR} {_LEAK_PATH}\n".encode())
    _git(repo, "add", "src")
    res = _run(repo)
    assert res.returncode == 1, (
        f"a REAL leak appended to the engine's own source was exempted — the identity test has "
        f"become a whole-file skip:\n{_out(res)}")
    assert "src/kw_common/leakguard.py" in _out(res), _out(res)


def test_the_identity_test_cannot_be_claimed_by_any_OTHER_file(tmp_path: Path) -> None:
    """The other half: only a byte-identical copy is recognised, and that copy leaks nothing.

    A file that merely LOOKS like the guard — same name, same path, a plausible prefix of its
    bytes — is scanned. The only file that is skipped is one whose content is the guard's own,
    which by construction contains no real value.
    """
    own = _SCRIPT.read_bytes()
    repo = _repo(tmp_path, "impostor")
    (repo / "scripts").mkdir()
    # The guard's real first 4 KB, then a leak. Same name, same path, a genuine prefix.
    (repo / _VENDORED_REL).write_bytes(own[:4096] + f"\nAGENT={_HOST}\n".encode())
    _git(repo, "add", "scripts")
    res = _run(repo)
    assert res.returncode == 1, (
        f"a file carrying the guard's first 4 KB was treated as the guard:\n{_out(res)}")


def test_line_endings_do_not_stop_the_guard_recognising_its_own_source(tmp_path: Path) -> None:
    """⚠️ A CRLF CHECKOUT IS NOT A DIFFERENT FILE.

    `core.autocrlf=true` gives a Windows worktree the same source with CRLF line endings while an
    installed copy has LF. Without normalisation the guard would fail to recognise itself on
    exactly one platform — the "works on the machine that wrote it" class this file has been
    bitten by before.
    """
    own = _SCRIPT.read_bytes()
    repo = _repo(tmp_path, "crlf")
    crlf = own.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    assert crlf != own, "vacuity guard: the fixture is not actually CRLF"
    (repo / "vendored.py").write_bytes(crlf)
    _git(repo, "add", "vendored.py")
    assert _run(repo).returncode == 0, (
        "a CRLF copy of the guard's own source was not recognised as the guard")


# -------------------------------------------------------------------------------------------
# ⭐ ISOLATING CASES. The refusal check has three loops, and a wide `path_exempt` is caught by
# TWO of them at once — so a mutation that deletes either one survives, and neither is really
# pinned. Each test below constructs the case where ONLY its loop can refuse: the rule is that
# to isolate a predicate you build the scenario no other arm can exclude.
# -------------------------------------------------------------------------------------------


def test_the_PROBE_PATH_loop_alone_refuses_a_wide_exemption(tmp_path: Path) -> None:
    """`unraid pool path` has NO entry in `_MUST_FAIL_PATHS`, so the path-corpus loop is silent.

    A wide `path_exempt` on it can therefore only be refused by running the CONTENT corpus at
    ordinary probe paths — which is precisely the loop whose absence was the off-switch.
    """
    assert "unraid pool path" not in {label for label, _ in guard._MUST_FAIL_PATHS}, (
        "vacuity guard: this label now HAS a path deny case, so the path loop could refuse the "
        "config below and this test no longer isolates the probe-path loop")
    repo = _repo(tmp_path, "probeonly")
    (repo / "notes.md").write_text(f"path: {_LEAK_PATH}\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    _write_config(repo, {"path_exempt": [
        {"pattern": "unraid pool path", "path_regex": ".",
         "why": "test fixture: total, and invisible to every loop but the probe-path one"}]})

    res = _run(repo)
    assert res.returncode == 2 and "DEFEATS" in _out(res), (
        f"a total path exemption on a pattern with no path deny case was accepted — the deny "
        f"corpus is not being measured at ordinary paths:\n{_out(res)}")


def test_the_PATH_CORPUS_loop_alone_refuses_a_literal(tmp_path: Path) -> None:
    """A literal that swallows the `cgnat address` PATH deny case and nothing else.

    It does not appear in any content or message sample, so the probe-path and message loops
    cannot refuse it. Only running `_MUST_FAIL_PATHS` through `scan_path` can.
    """
    # ⚠️ Assembled: this file is scanned, and the value is a deny shape by construction.
    literal = "100.127." + "255.254.conf"
    repo = _repo(tmp_path, "pathcorpus")
    _write_config(repo, {"allow_literals": [
        {"literal": literal, "why": "test fixture: swallows exactly one PATH deny case"}]})

    res = _run(repo)
    assert res.returncode == 2 and "PATH" in _out(res), (
        f"a literal that defeats a PATH deny case and nothing else was accepted — the path "
        f"corpus is not being measured:\n{_out(res)}")


def test_the_MESSAGE_CORPUS_loop_alone_refuses_a_literal(tmp_path: Path) -> None:
    """The third loop, isolated the same way: a literal present only in a MESSAGE deny sample."""
    literal = "see /" + "/mnt/" + "user/appdata/svc"
    repo = _repo(tmp_path, "msgcorpus")
    _write_config(repo, {"allow_literals": [
        {"literal": literal, "why": "test fixture: swallows exactly one MESSAGE deny case"}]})

    res = _run(repo)
    assert res.returncode == 2 and "MESSAGE" in _out(res), (
        f"a literal that defeats a MESSAGE deny case and nothing else was accepted — the message "
        f"corpus is not being measured:\n{_out(res)}")


def test_the_ADJACENT_deny_corpus_is_measured_too(tmp_path: Path) -> None:
    """`_MUST_FAIL_ADJACENT` is thirteen of the thirty-two cases and was droppable unnoticed.

    Those cases ARE the amnesty class this engine was rewritten to close — a permitted token
    written flush against a leak — so a config allowed to defeat one is a config allowed to
    reintroduce the bug the allow-span design exists to prevent.
    """
    want_label, sample = guard._MUST_FAIL_ADJACENT[0]
    repo = _repo(tmp_path, "adjacent")
    _write_config(repo, {"allow_literals": [
        {"literal": sample,
         "why": "test fixture: the whole adjacent sample, so the hit falls inside it"}]})

    res = _run(repo)
    assert res.returncode == 2 and want_label in _out(res), (
        f"a literal that defeats an ADJACENT deny case was accepted; those thirteen cases are "
        f"the amnesty bypass this engine was rewritten to close:\n{_out(res)}")


def test_a_REFUSED_config_leaves_the_previous_allowances_in_place() -> None:
    """`apply_config` rolls back on refusal, and nothing measured that.

    Every other test refuses a config in a SUBPROCESS that then exits, so a failure to roll back
    is invisible to them. In a long-lived process — a consumer importing this module, or a scan of
    several repositories — a half-applied config would silently govern the next question asked.
    """
    good = guard.GuardConfig(allow_literals=("kept-by-the-rollback",))
    guard.apply_config(good)
    assert good.allow_literals == guard.ALLOW_LITERALS, "vacuity guard: the good config took"

    defeating = guard.GuardConfig(allow_literals=("/mnt/" + "user",))
    try:
        guard.apply_config(defeating)
    except guard.ConfigError:
        pass
    else:
        guard.apply_config(guard.DEFAULT_CONFIG)
        pytest.fail("the defeating config was accepted, so this test cannot measure the rollback")

    assert good.allow_literals == guard.ALLOW_LITERALS, (
        "a REFUSED config left its allowances installed — the rollback in `apply_config` is gone, "
        "and the next scan in this process runs under rules that were rejected")
    assert good.path_exempt == guard.PATH_EXEMPT
    guard.apply_config(guard.DEFAULT_CONFIG)


@pytest.mark.parametrize(
    "argv,expected",
    [
        pytest.param(["--config"], "needs a value", id="no-value"),
        pytest.param(["--config", "--selftest"], "not another flag", id="value-is-a-flag"),
        pytest.param(["--config="], "needs a path", id="empty-joined-form"),
    ],
)
def test_the_config_flag_is_parsed_STRICTLY(tmp_path: Path, argv: list[str],
                                            expected: str) -> None:
    """Every `--config` spelling that cannot mean anything is a usage error, not a silent default.

    ⛔ THE SILENT DEFAULT IS THE BUG THIS FILE'S PARSER EXISTS TO PREVENT: an argument the scanner
    swallows leaves the caller believing their allowances were applied when the scan ran under
    different rules entirely. `--repo` has all three of these checks; `--config` needed them too,
    and none of them was exercised.
    """
    repo = _repo(tmp_path, f"argv_{abs(hash(tuple(argv)))}")
    res = _run(repo, *argv)
    assert res.returncode == 2, f"{argv} was accepted:\n{_out(res)}"
    assert expected in _out(res), f"{argv}: expected {expected!r} in:\n{_out(res)}"


def test_a_config_that_is_UNREADABLE_or_not_UTF8_stops_the_scan(tmp_path: Path) -> None:
    """The two `load_config` failure branches, neither of which any test reached.

    Both must exit 2 rather than degrading to "no config" — the same rule as a parse error, for
    the same reason: a clean verdict looks identical either way.
    """
    # Not UTF-8: a lone continuation byte cannot start a sequence.
    repo = _repo(tmp_path, "notutf8")
    (repo / guard.CONFIG_FILENAME).write_bytes(b'{"allow_literals": [\x80]}')
    _git(repo, "add", guard.CONFIG_FILENAME)
    res = _run(repo)
    assert res.returncode == 2, f"a non-UTF-8 config did not stop the scan:\n{_out(res)}"
    assert "UTF-8" in _out(res), _out(res)

    # Unreadable: a DIRECTORY at the config's path. `is_file()` is false, so discovery passes over
    # it — but `--config` names it explicitly and must refuse rather than proceed.
    other = _repo(tmp_path, "isadir")
    (other / guard.CONFIG_FILENAME).mkdir()
    res = _run(other, "--config", str(other / guard.CONFIG_FILENAME))
    assert res.returncode == 2, f"a directory named as --config did not stop the scan:\n{_out(res)}"


def test_a_justification_of_only_WHITESPACE_is_refused(tmp_path: Path) -> None:
    """"`why` is required" has to mean a reason, not a space bar.

    An entry whose justification is blank satisfies the letter of the format and none of its
    purpose, and `not value.strip()` — the half that makes it mean something — was unmeasured.
    """
    repo = _repo(tmp_path, "blankwhy")
    (repo / guard.CONFIG_FILENAME).write_text(
        json.dumps({"allow_literals": [{"literal": "x", "why": "   \t  "}]}), encoding="utf-8")
    _git(repo, "add", guard.CONFIG_FILENAME)
    res = _run(repo)
    assert res.returncode == 2, f"a whitespace-only justification was accepted:\n{_out(res)}"
    assert "non-empty" in _out(res), _out(res)

# =================================================================================================
# 5. The package surface itself
# =================================================================================================


def test_the_shipped_guard_names_no_private_repository() -> None:
    """⛔ THE MODULE IS INSIDE THE WHEEL, so its comments are published to everyone who installs.

    While the guard was vendored, `MANIFEST.in` kept it out of the sdist for exactly this reason —
    its docstring enumerated the fleet's private repositories by name. Exclusion is no longer
    available as the fix: the engine ships in the package now. The names were removed instead, and
    this is what keeps them out. Provenance is cited as `consumer#NN`, which finds the discussion
    from inside the fleet and discloses no inventory outside it.

    ⚠️ THE PUBLIC NAMES ARE DELIBERATELY NOT LISTED HERE. `kw-common` and `unraid-templates` are
    public repositories and naming them costs nothing; a test that banned every repository name
    would fail on the docstring sentence that exists to say which ones are public.
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    private = ("tape", "keystone", "cef-tracker", "reauth-bot", "gambit", "the-desk")
    named = [name for name in private
             if any(name in line.lower() for line in src.splitlines())]
    assert not named, (
        f"the packaged guard names private repositories {named} — this file is published in the "
        f"wheel to anyone who installs the library")


def test_the_console_entry_point_is_declared_and_points_at_the_no_argument_callable() -> None:
    """⚠️ `cli`, NOT `main`. An entry point aimed at `main` installs fine, imports fine, and dies
    the first time anybody runs it, because a console script is called with no arguments — in a
    consumer's repository, after the tag. `main(argv)` keeps its argument so it stays testable.
    """
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'kw-leak-guard = "kw_common.leakguard:cli"' in pyproject, (
        "the console script is not declared, or no longer points at `cli`")
    import inspect
    assert not inspect.signature(guard.cli).parameters, (
        "`cli` takes an argument; a console script is called with none")
    assert "argv" in inspect.signature(guard.main).parameters, (
        "`main` no longer takes its argv, so argument parsing can only be tested through sys.argv")


def test_every_name_in___all___exists() -> None:
    """`__all__` is the semver contract now that this module ships in a package.

    A name listed but absent is an `ImportError` for a consumer doing `from … import *`, and a
    promise nobody can keep. Checked here rather than trusted, because nothing else reads the list.
    """
    missing = [name for name in guard.__all__ if not hasattr(guard, name)]
    assert not missing, f"__all__ promises names the module does not have: {missing}"


def test_the_guard_module_imports_without_dragging_in_anything_else() -> None:
    """The isolation contract, asked of the new module.

    `kw_common.leakguard` must not pull in `kw_common.alerting` — a consumer that only wants the
    guard should not pay for `smtplib`, `ssl` and `urllib`.

    ⛔ AND IT MUST BE STDLIB-ONLY, which naming two forbidden modules does not establish. This
    test used to assert exactly that: no `kw_common.alerting`, no `smtplib`. It would have passed
    unchanged if the engine had grown a third-party import — the contract this repository is built
    on, unmeasured for its newest module. `README.md` cites `tests/test_isolation.py` as the
    enforcement, and that file is written against `alerting.py` alone.

    ⚠️ NOT A `sysconfig.get_paths()['stdlib']` PREFIX TEST, for the reason `test_isolation.py`
    gives at length: C extension modules live in `lib-dynload` or are built in, and a prefix test
    calls them foreign. What identifies a third-party import is a `site-packages` origin.

    ⭐ MEASURED AS A DELTA, not as an absolute. The first version of this asserted that NO
    third-party module was in `sys.modules` — and failed on `_virtualenv`, a shim the interpreter
    loads from a `.pth` file before any user code runs. What the contract is about is what the
    GUARD imports, so the baseline is taken first and subtracted, which also makes the test
    independent of whatever the surrounding environment happens to preload.
    """
    probe = (
        "import sys\n"
        "def third_party():\n"
        "    return {n for n, m in list(sys.modules.items())\n"
        "            if getattr(m, '__file__', None)\n"
        "            and ('site-packages' in m.__file__ or 'dist-packages' in m.__file__)}\n"
        "before = third_party()\n"
        "import kw_common.leakguard\n"
        "assert 'kw_common.alerting' not in sys.modules, sorted(sys.modules)\n"
        "assert 'smtplib' not in sys.modules, 'the guard pulled in smtplib'\n"
        "added = sorted(n for n in third_party() - before if not n.startswith('kw_common'))\n"
        "assert not added, 'importing the guard added third-party package(s): %s' % added\n"
    )
    res = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
    assert res.returncode == 0, f"{res.stdout}{res.stderr}"
