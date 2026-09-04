"""The pre-push hook: what it DOES, driven end to end against a real `git push`.

⭐ WHY THIS FILE EXISTS, AND WHAT IT DELIBERATELY CANNOT SAY (#16, #10).

`test_the_shipped_guard_names_no_private_repository` used to live in
`tests/test_leak_guard_extraction.py`. It asked ONE file — the guard's own source — whether it
named a private repository, and it carried the list of those names as a plain tuple. Both halves
were wrong:

  * every OTHER published file was unchecked. Four private service names reached the template, the
    setup document, the CHANGELOG and a module during 1.2.0 and were caught by a person reading,
    not by any check. `MANIFEST.in` puts `tests/`, `docs/` and the template in the sdist attached
    to every public Release, and the modules are in the wheel.
  * the LIST ITSELF SHIPPED. The inventory the check existed to keep out of published artifacts
    was published inside the check, and had been since 1.1.0. Widening that test's reach while
    the list stayed would have made the disclosure worse, not better; hashing or splitting it
    in-repo is the same disclosure with extra steps.

So the list moved OUT, into a guard that is committed to nothing, and the check moved from a
pytest assertion to `.githooks/pre-push`. That is a real trade and this file states it rather than
implying coverage it does not have:

  ⚠️ CI CANNOT VERIFY A LIST IT MUST NOT HAVE. Nothing here — and nothing in CI — can assert that
     the hook RAN on somebody's machine, or that the names are absent from this tree. What this
     file asserts is that the hook exists, is installed the way it says it is, and REFUSES the
     push when the guard it delegates to finds something. The other half is a local preflight and
     is honest about being one.

⭐ EVERY END-TO-END TEST BELOW DRIVES A REAL `git push` TO A REAL BARE REMOTE, and asserts what
HAPPENED — the remote ref moved, or it did not. A wiring assertion ("`--public-repo` appears in
the hook file") holds while the hook is unreachable, while the interpreter probe fails, and while
the exit code is discarded; #10 names that shape explicitly as the thing not to write.

The guard the hook calls is STUBBED here. It has to be: the real one is not in this repository,
which is the entire point of #16. The stub takes the same command line and records the arguments
it was handed, so the `--not --remotes` case — the brand-new branch that would otherwise scan zero
commits — is pinned by what the hook actually passed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".githooks" / "pre-push"

# The one string the stub guard treats as a leak. Deliberately not a real anything.
SENTINEL = "PLANTED-PRIVATE-NAME"

# A stand-in for the project-side guard. Same command line, same exit codes: 0 clean, 1 finding.
# It also APPENDS its argv to a log the tests read, which is how the range form is asserted.
STUB_GUARD = '''\
import subprocess, sys
from pathlib import Path

argv = sys.argv[1:]
Path(sys.argv[0]).with_suffix(".log").open("a", encoding="utf-8").write(" ".join(argv) + "\\n")

def value(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

repo = value("--repo")
rng = value("--range")
sentinel = "SENTINEL_PLACEHOLDER"

if rng is None:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=True)
    for rel in out.stdout.decode().split("\\0"):
        if not rel:
            continue
        p = Path(repo) / rel
        if p.is_file() and sentinel in p.read_text(encoding="utf-8", errors="replace"):
            print("STUB FINDING (tree): " + rel)
            sys.exit(1)
else:
    shas = subprocess.run(["git", "rev-list", *rng.split()], cwd=repo,
                          capture_output=True, check=True).stdout.decode().split()
    for sha in shas:
        diff = subprocess.run(["git", "show", sha], cwd=repo,
                              capture_output=True, check=True).stdout.decode(errors="replace")
        if sentinel in diff:
            print("STUB FINDING (range): " + sha)
            sys.exit(1)
print("stub clean")
sys.exit(0)
'''


# =================================================================================================
# helpers
# =================================================================================================
def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git with an identity injected PER CALL.

    ⚠️ NOT via a helper that some call sites skip, and not from a global config. A CI runner has no
    global git identity, so a `commit` that needs one silently no-ops there — and a test whose
    SETUP no-ops passes vacuously while looking like coverage.
    """
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False)


def _in_a_git_repo() -> bool:
    res = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=REPO_ROOT, capture_output=True,
                         timeout=60, check=False)
    return res.returncode == 0


def _scratch(tmp_path: Path, *, configure_guard: bool = True) -> tuple[Path, Path, Path]:
    """A work repo with the REAL hook installed, a bare remote, and the stub guard.

    Returns (work, remote, guard-log). The hook is COPIED from this repository, not rewritten, so
    what the tests drive is the file that ships.
    """
    remote = tmp_path / "remote.git"
    remote.mkdir()
    assert _git(remote, "init", "--bare").returncode == 0

    work = tmp_path / "work"
    work.mkdir()
    assert _git(work, "init").returncode == 0

    guard = tmp_path / "stubguard.py"
    guard.write_text(STUB_GUARD.replace("SENTINEL_PLACEHOLDER", SENTINEL), encoding="utf-8")

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copyfile(HOOK, hooks / "pre-push")
    os.chmod(hooks / "pre-push", 0o755)  # noqa: S103 - a hook git will not run otherwise

    assert _git(work, "config", "core.hooksPath", str(hooks)).returncode == 0
    if configure_guard:
        # ⚠️ The hook runs `python3`/`python`/`py -3`, not this interpreter. The stub is plain
        # stdlib so any of them runs it; what matters is that the PATH has one, which the probe
        # inside the hook establishes and `test_the_hook_refuses_when_no_interpreter...` pins.
        assert _git(work, "config", "kw.privateGuard", str(guard)).returncode == 0
    assert _git(work, "remote", "add", "origin", str(remote)).returncode == 0

    (work / "README.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "README.md").returncode == 0
    assert _git(work, "commit", "-m", "first").returncode == 0
    return work, remote, guard.with_suffix(".log")


def _remote_head(remote: Path, ref: str = "refs/heads/main") -> str | None:
    res = _git(remote, "rev-parse", "--verify", "-q", ref)
    return res.stdout.strip() or None


needs_sh = pytest.mark.skipif(shutil.which("sh") is None,
                              reason="no POSIX sh on PATH, so git cannot run the hook")
needs_repo = pytest.mark.skipif(not _in_a_git_repo(),
                                reason="not a git repository (an sdist or an archive extraction)")


# =================================================================================================
# 1. the file itself
# =================================================================================================
def test_the_hook_is_shipped_at_the_path_the_install_command_names() -> None:
    assert HOOK.is_file(), (
        f"{HOOK} is missing. `core.hooksPath .githooks` finds nothing, and a hooks path that "
        f"resolves to no hook is indistinguishable from a passing check.")


@needs_repo
def test_the_hook_is_committed_EXECUTABLE() -> None:
    """⭐ THE BIT LIVES IN THE INDEX, NOT ON DISK.

    `core.filemode` is false on Windows, so `chmod +x` changes nothing that gets committed, and
    git-for-windows runs a non-executable hook anyway — so the workstation that authors the hook
    never notices. Linux silently SKIPS it. A hook that is silently skipped is the failure mode
    this whole file exists to remove, so the mode is asserted where it is actually stored.
    """
    res = subprocess.run(["git", "ls-files", "-s", ".githooks/pre-push"], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=60, check=True)
    assert res.stdout.startswith("100755 "), (
        f"the hook is committed as {res.stdout.split()[0] if res.stdout else '(untracked)'}, not "
        f"100755. git SKIPS a non-executable hook on Linux without a word. Fix with "
        f"`git update-index --chmod=+x .githooks/pre-push`.")


def test_the_hook_is_pure_ASCII_with_LF_endings_and_a_sh_shebang() -> None:
    """A hook is read by whatever `sh` git found, on three operating systems.

    Non-ASCII in a script that some shell decodes as the locale codepage is the mojibake class
    that has killed `.ps1` files here; CRLF makes a `#!/bin/sh` shebang fail with "bad
    interpreter" on Linux, which reads as "the hook is broken" rather than "the line endings are".
    `.gitattributes` normalises the checkout, and this is what says so out loud.
    """
    raw = HOOK.read_bytes()
    assert raw.startswith(b"#!/bin/sh\n"), "the hook does not start with a POSIX sh shebang"
    assert b"\r\n" not in raw, "the hook has CRLF line endings; `#!/bin/sh\\r` is not an interpreter"
    assert all(byte < 128 for byte in raw), "the hook is not pure ASCII"


@needs_sh
def test_the_hook_parses_as_posix_sh() -> None:
    res = subprocess.run(["sh", "-n", str(HOOK)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60, check=False)
    assert res.returncode == 0, f"the hook is not valid POSIX sh:\n{res.stdout}{res.stderr}"


def test_the_hook_probes_the_interpreter_by_RUNNING_it_not_by_command_v() -> None:
    """⛔ `command -v python3` FINDS THE MICROSOFT STORE STUB on Windows.

    The alias resolves, so the probe passes; the stub then refuses to run and every push aborts
    with "Python was not found" and no mention of the guard. `python3 -c ""` asks the question
    that matters — does this thing execute — and both hooks in the sibling repository this pattern
    came from had the wrong one.
    """
    text = HOOK.read_text(encoding="utf-8")
    assert 'python3 -c ""' in text, "the hook no longer probes the interpreter by running it"
    # ⚠️ COMMENTS STRIPPED FIRST. The hook EXPLAINS why `command -v` is wrong, so a scan of the
    # whole file finds the forbidden string in the sentence saying not to use it — the same
    # self-match that trips a leak guard reading its own denylist. Assert on the CODE.
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "command -v python" not in code, (
        "the hook probes with `command -v`, which resolves the Windows Store stub")


def test_the_hook_states_the_config_key_and_the_install_command() -> None:
    """The refusal has to be actionable. A hook that says only "refused" is a wall."""
    text = HOOK.read_text(encoding="utf-8")
    assert "core.hooksPath .githooks" in text, "the hook does not carry its own install command"
    assert text.count("kw.privateGuard") >= 2, (
        "the hook no longer names the config key in both the install line and the refusal")


# =================================================================================================
# 2. what it DOES — a real push, every time
# =================================================================================================
@needs_sh
@pytest.mark.timeout(300)
def test_a_CLEAN_push_is_NOT_refused(tmp_path: Path) -> None:
    """⭐ THE FALSE-RED DIRECTION, FIRST. A guard that reddens a correct tree gets switched off,
    and then it protects nothing at all. Every refusal asserted below is worthless without this."""
    work, remote, log = _scratch(tmp_path)
    res = _git(work, "push", "origin", "main")
    assert res.returncode == 0, f"a clean push was refused:\n{res.stdout}\n{res.stderr}"
    assert _remote_head(remote) is not None, "the push reported success but the remote has no main"
    assert log.exists() and log.read_text(encoding="utf-8").strip(), (
        "the guard was never invoked, so this push proves nothing about the hook")


@needs_sh
@pytest.mark.timeout(300)
def test_a_COMMIT_carrying_a_finding_is_REFUSED_and_the_remote_does_not_move(
        tmp_path: Path) -> None:
    """The whole point: the refusal is measured by the REMOTE, not by the hook's own chatter."""
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    before = _remote_head(remote)

    (work / "notes.md").write_text(f"deploy to {SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "notes.md").returncode == 0
    assert _git(work, "commit", "-m", "add notes").returncode == 0

    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"the push was NOT refused:\n{res.stdout}\n{res.stderr}"
    assert "REFUSED" in res.stderr, f"the refusal did not say why:\n{res.stderr}"
    assert _remote_head(remote) == before, "the leaking commit reached the remote anyway"


@needs_sh
@pytest.mark.timeout(300)
def test_a_WORKTREE_finding_is_REFUSED_even_though_no_commit_carries_it(tmp_path: Path) -> None:
    """⭐ THE TREE SCAN IS NOT REDUNDANT WITH THE RANGE SCAN, and this is the difference.

    The wheel and the sdist are built from the WORKING TREE. A value sitting in a tracked file
    that no commit has published yet is still what `python -m build` would package, so the tree
    scan is the half that answers #16 and the range scan is the half that answers "does this push
    publish it". Losing either one loses a whole question.
    """
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    before = _remote_head(remote)

    # Committed clean, then edited. Nothing in any commit carries the sentinel.
    (work / "extra.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "extra.md").returncode == 0
    assert _git(work, "commit", "-m", "extra").returncode == 0
    (work / "extra.md").write_text(f"{SENTINEL}\n", encoding="utf-8")

    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"a worktree finding did not refuse the push:\n{res.stderr}"
    assert "TREE" in res.stderr, f"the refusal did not name the tree scan:\n{res.stderr}"
    assert _remote_head(remote) == before


@needs_sh
@pytest.mark.timeout(300)
def test_an_UNCONFIGURED_guard_REFUSES_rather_than_passing_quietly(tmp_path: Path) -> None:
    """⛔ FAIL CLOSED. `kw.privateGuard` unset must not mean "skip the private-name check".

    A guard that is silently not running is worse than one that was never installed, because its
    absence looks exactly like a pass — #10's own words. The refusal names the key, because a
    refusal nobody can act on gets worked around with `--no-verify`.
    """
    work, remote, _log = _scratch(tmp_path, configure_guard=False)
    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"an unconfigured guard let the push through:\n{res.stderr}"
    assert "kw.privateGuard" in res.stderr, f"the refusal did not name the key:\n{res.stderr}"
    assert _remote_head(remote) is None, "the push landed despite the refusal"


@needs_sh
@pytest.mark.timeout(300)
def test_a_guard_path_that_is_not_a_file_REFUSES(tmp_path: Path) -> None:
    """Configured-but-wrong is the same hazard as unconfigured, one typo away."""
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "config", "kw.privateGuard",
                str(tmp_path / "no-such-guard.py")).returncode == 0
    res = _git(work, "push", "origin", "main")
    assert res.returncode != 0, f"a missing guard let the push through:\n{res.stderr}"
    assert "not a file" in res.stderr, res.stderr
    assert _remote_head(remote) is None


@needs_sh
@pytest.mark.timeout(300)
def test_a_BRAND_NEW_branch_is_scanned_with_not_remotes_rather_than_against_nothing(
        tmp_path: Path) -> None:
    """⭐ THE CASE THAT WOULD OTHERWISE SCAN ZERO COMMITS AND REPORT CLEAN.

    A branch that has never been pushed has no remote counterpart, so git hands the hook an
    all-zero `remote_sha`. Diffing against that scans nothing — and a first push is the most
    likely moment for a leak, not the least. `<sha> --not --remotes` is what makes it scan the
    new history instead.

    Asserted TWICE, from both sides: the form the hook passed (from the stub's argv log) AND the
    outcome (a leak on that new branch is refused). The argv alone would pass if the guard
    ignored the argument; the outcome alone would pass if some other scan caught it.
    """
    work, remote, log = _scratch(tmp_path)
    assert _git(work, "checkout", "-b", "feature/new").returncode == 0
    (work / "clean.md").write_text("clean\n", encoding="utf-8")
    assert _git(work, "add", "clean.md").returncode == 0
    assert _git(work, "commit", "-m", "clean work").returncode == 0

    res = _git(work, "push", "origin", "feature/new")
    assert res.returncode == 0, f"a clean first push was refused:\n{res.stdout}\n{res.stderr}"
    calls = log.read_text(encoding="utf-8")
    assert "--not --remotes" in calls, (
        f"the hook did not use `--not --remotes` for a branch with no upstream, so the range "
        f"scan saw nothing:\n{calls}")

    # And the same branch, now leaking, must be refused.
    (work / "leak.md").write_text(f"{SENTINEL}\n", encoding="utf-8")
    assert _git(work, "add", "leak.md").returncode == 0
    assert _git(work, "commit", "-m", "leak").returncode == 0
    before = _remote_head(remote, "refs/heads/feature/new")
    res = _git(work, "push", "origin", "feature/new")
    assert res.returncode != 0, f"a leak on a new branch was not refused:\n{res.stderr}"
    assert _remote_head(remote, "refs/heads/feature/new") == before


@needs_sh
@pytest.mark.timeout(300)
def test_deleting_a_remote_branch_is_not_scanned_as_if_it_added_something(tmp_path: Path) -> None:
    """A deletion publishes nothing, and git sends an all-zero LOCAL sha for it.

    Without the zero check the hook would build the range `<remote>..0000000` and the guard would
    exit non-zero on a range git cannot resolve — refusing a push that adds nothing at all. That
    is the false-red direction again, in the one case that looks like an edge and is not.
    """
    work, remote, _log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    assert _git(work, "checkout", "-b", "doomed").returncode == 0
    assert _git(work, "push", "origin", "doomed").returncode == 0
    assert _remote_head(remote, "refs/heads/doomed") is not None

    res = _git(work, "push", "origin", "--delete", "doomed")
    assert res.returncode == 0, f"deleting a branch was refused:\n{res.stdout}\n{res.stderr}"
    assert _remote_head(remote, "refs/heads/doomed") is None


@needs_sh
@pytest.mark.timeout(300)
def test_the_hook_passes_public_repo_on_BOTH_scans(tmp_path: Path) -> None:
    """The flag is what turns the private-name class on. Without it on BOTH invocations the hook
    runs a scan that cannot see the thing it exists to catch, and reports clean."""
    work, _remote, log = _scratch(tmp_path)
    assert _git(work, "push", "origin", "main").returncode == 0
    calls = [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(calls) >= 2, f"expected a tree scan and a range scan, got:\n{calls}"
    assert all("--public-repo" in line for line in calls), (
        f"a scan ran without --public-repo, so the private-name class was off:\n{calls}")
    assert any("--range" in line for line in calls), f"no range scan ran:\n{calls}"
    assert any("--range" not in line for line in calls), f"no tree scan ran:\n{calls}"


# =================================================================================================
# 3. the shipped set is a SUBSET of what the guard can see
# =================================================================================================
@needs_repo
def test_everything_the_package_ships_is_a_TRACKED_file() -> None:
    """⭐ #16(a) ASKED IN-REPO, WITHOUT THE LIST.

    The guard the hook runs scans `git ls-files`. That covers every published file only while
    every published file is TRACKED — an untracked file that setuptools picks up anyway (anything
    under `src/`, since `packages.find` walks the directory rather than the index) would be inside
    the wheel and invisible to the scan. This asks that question of the package directory and of
    every path `MANIFEST.in` names, which together are what the wheel and the sdist carry.

    ⚠️ It does NOT build the artifacts. Building needs the network for build dependencies and
    takes seconds; CI builds both and runs the shipped suite against them. The property this
    pins is the one that would make the whole hook mechanism blind, and it is cheap.
    """
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True,
                            timeout=120, check=True)
    tracked = {p for p in listed.stdout.decode("utf-8").split("\0") if p}
    assert tracked, "git ls-files returned nothing — this test would pass vacuously"

    untracked: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*")):
        # BUILD OUTPUT IS NOT SHIPPED SOURCE. `.egg-info/` is metadata setuptools regenerates on
        # every build and `.gitignore` excludes; `__pycache__` likewise. Neither is in the wheel,
        # so demanding they be tracked would redden a correct tree — which is how a check gets
        # switched off. Everything else under `src/` IS in the wheel, because `packages.find`
        # walks the directory rather than the index.
        if not path.is_file() or path.suffix == ".pyc":
            continue
        if any(part == "__pycache__" or part.endswith(".egg-info") for part in path.parts):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel not in tracked:
            untracked.append(rel)

    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    named = 0
    for line in manifest.splitlines():
        parts = line.split()
        if not parts or parts[0] != "include":
            continue
        for rel in parts[1:]:
            named += 1
            if rel not in tracked:
                untracked.append(rel)
    assert named >= 4, f"MANIFEST.in no longer names files with `include` ({named} found)"
    assert untracked == [], (
        f"these files ship but are not tracked, so the guard's tree scan never reads them: "
        f"{untracked}")
