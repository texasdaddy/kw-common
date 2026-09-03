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
    assert injected_literals.path_exempt, "this repository declares no path_exempt entries"
    known = {label for label, _ in guard.PATTERNS}
    for label, _rx in injected_literals.path_exempt:
        assert label in known, f"{label!r} names no pattern; `load_config` should have refused it"
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
    assert "<commit message>" in _out(message), (
        f"the finding is not attributed to the MESSAGE surface, so it may be the diff or the "
        f"identity being reported instead:\n{_out(message)}")


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
    guard should not pay for `smtplib`, `ssl` and `urllib`. The engine is stdlib-only besides.
    """
    res = subprocess.run(
        [sys.executable, "-c",
         "import sys; import kw_common.leakguard; "
         "assert 'kw_common.alerting' not in sys.modules, sorted(sys.modules); "
         "assert 'smtplib' not in sys.modules, 'the guard pulled in smtplib'"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert res.returncode == 0, f"{res.stdout}{res.stderr}"
