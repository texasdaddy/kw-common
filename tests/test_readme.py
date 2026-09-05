"""The README's quick-start must actually work.

⭐ WHY THIS FILE EXISTS. The README's Python example is the ONLY adoption example in the
repository, and it shipped calling `warn_if_unconfigured(...)` without importing it — a
`NameError` on the third line, in the call that exists to stop alerts silently going nowhere. It
was written, reviewed and committed without once being run.

⚠️ IT IS CHECKED, NOT EXECUTED, AND THAT IS DELIBERATE. Running the snippet verbatim does real
work: it resolves a hostname, and it creates the directories its example paths name — measured,
running it once created a `my-service` tree on the machine. A test that has to be cleaned up
after is a test that will eventually not be. So this resolves every name the snippet uses against
what the snippet imports plus the builtins, which is precisely the defect class that got through,
with no side effects at all.
"""

from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def _snippets() -> list[str]:
    """EVERY ```python block in the README.

    ⚠️ THIS USED TO DEMAND EXACTLY ONE, and that was a bound on the README rather than a property
    of it: adding a second adoption example — which 1.2.0 did, for `alerting_env` — turned a
    passing suite red for a reason that had nothing to do with either example being wrong. Worse
    is the direction it pushed, because the cheapest way to make it green again is to delete a
    block or stop fencing it as Python, which is exactly how an example escapes the checks below.

    So every block is checked, and each carries its own index in the failure message.
    """
    md = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", md, re.S)
    assert blocks, "README.md has no ```python block at all"
    return blocks


def _quickstart() -> str:
    """The blocks as one unit, for the checks that do not care which block a name came from."""
    return "\n".join(_snippets())


def test_the_readme_has_a_quickstart_at_all() -> None:
    """A vacuity guard. Every check below reads these blocks; if the extraction silently returned
    nothing, they would all pass over an empty string."""
    src = _quickstart()
    assert "configure(" in src
    assert "notify(" in src
    # 1.2.0 added a SECOND adoption example, which is what this file used to forbid outright.
    assert len(_snippets()) >= 2


def test_every_snippet_parses() -> None:
    for n, src in enumerate(_snippets()):
        compile(src, f"{README} [python block {n}]", "exec")


def test_every_name_a_snippet_uses_is_one_it_imports() -> None:
    """⭐ THE DEFECT THAT GOT THROUGH, PINNED.

    Walks each snippet and requires every name it READS to be one it imported, one it defined, or
    a builtin. `warn_if_unconfigured` was none of the three.

    ⚠️ PER BLOCK, NOT OVER THE BLOCKS CONCATENATED. A reader copies ONE block, so a name that
    block 2 uses and only block 1 imports is exactly the `NameError` this test exists to catch —
    and joining them first is the tidy-looking change that makes it invisible.
    """
    for n, src in enumerate(_snippets()):
        tree = ast.parse(src)

        available: set[str] = set(dir(builtins))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                available.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.Import):
                available.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                available.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                available.add(node.id)

        used = {x.id for x in ast.walk(tree)
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
        missing = sorted(used - available)
        assert missing == [], (
            f"README python block {n} uses {missing} without importing it — copy-pasting it "
            f"raises NameError")


def test_everything_the_quickstart_imports_from_the_library_really_exists() -> None:
    """The other direction: an import line naming something the module does not export fails at
    import time, before any of the example runs."""
    import importlib

    tree = ast.parse(_quickstart())
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("kw_common"):
            continue
        mod = importlib.import_module(node.module)
        for alias in node.names:
            assert hasattr(mod, alias.name), (
                f"README imports {alias.name!r} from {node.module}, which does not export it")
            assert alias.name in getattr(mod, "__all__", ()), (
                f"README imports {alias.name!r} from {node.module}, but it is not in __all__ — "
                f"the example depends on a name that carries no semver guarantee")
            checked += 1
    assert checked >= 5, "no kw_common imports were checked — this test proves nothing"


def test_the_pin_an_exact_tag_rule_is_stated() -> None:
    assert "Never pin a branch" in README.read_text(encoding="utf-8")


def test_no_shipped_file_pins_a_version_this_package_is_not() -> None:
    """⭐ THE DRIFT CLASS, NOT JUST THE README INSTANCE.

    The README install line was made to derive from `kw_common.__version__`; the package's own
    `__init__.py` docstring carries the same `@vX.Y.Z` install example and was left as a literal,
    so it would drift silently on the next bump. Fixing the reported instance and leaving its
    sibling is the recurring miss in this repository, so this asks the question of EVERY tracked
    file that shows an install line rather than of one of them.
    """
    import kw_common

    expected = f"@v{kw_common.__version__}"
    # ⚠️ ASSEMBLED FROM FRAGMENTS, and this file is skipped below. A scanner that searches for a
    # literal will find that literal in its own source — the same self-match that trips a leak
    # guard's own test file. Both halves are needed: the fragments keep the marker out of any
    # OTHER matcher, and the skip keeps this file out of its own results.
    marker = "github.com/texasdaddy/" + "kw-common@"
    # ⭐ AND THE RAW-ASSET FORM. The setup document fetches the template by tag from
    # `raw.githubusercontent.com/<owner>/<repo>/vX.Y.Z/…` — no `@` — and those two pins were
    # outside this scan (the gate found them drifting silently at the next bump).
    raw_marker = "raw.githubusercontent.com/texasdaddy/" + "kw-common/"
    raw_expected = f"{raw_marker}v{kw_common.__version__}/"
    root = Path(__file__).resolve().parents[1]
    stale: list[str] = []
    checked = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".md", ".py", ".toml", ".in", ".yml"):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue  # the scanner is not one of the scanned
        if any(part in {".git", ".venv", "dist", "build", ".mypy_cache", ".ruff_cache",
                        ".pytest_cache"} for part in path.parts):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if raw_marker in line:
                after = line.split(raw_marker, 1)[1]
                if after.startswith("v") and after[1:2].isdigit():
                    checked += 1
                    if raw_expected not in line:
                        stale.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
            if marker not in line:
                continue
            # Only a CONCRETE pin — `@v` followed by a digit. A documented placeholder like
            # `@vX.Y.Z` is not a version anyone would copy, and demanding it track the release
            # would be the same backwards incentive this test exists to remove.
            after = line.split(marker, 1)[1]
            if not after.startswith("v") or not after[1:2].isdigit():
                continue
            checked += 1
            if expected not in line:
                stale.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    assert checked >= 4, (
        f"only {checked} install line(s) found — this test is no longer looking where the "
        f"install examples live")
    assert stale == [], (
        f"these pin a version this package is not ({kw_common.__version__}):\n" + "\n".join(stale))


def test_the_install_line_names_the_version_this_package_actually_is() -> None:
    """⭐ DERIVED FROM `kw_common.__version__`, NOT PINNED TO A LITERAL.

    An earlier version hardcoded `v1.0.0` here, which got the incentive exactly backwards:
    bumping `__version__` and forgetting the README left the suite GREEN — so the README went on
    telling every new consumer to pin a superseded tag — while UPDATING the README to match a
    bump turned the suite RED. The test punished the correct action and rewarded the omission.

    Read from the package, it becomes the version-drift guard the README says exists.
    """
    import kw_common

    expected = f"@v{kw_common.__version__}"
    text = README.read_text(encoding="utf-8")
    install_lines = [ln for ln in text.splitlines()
                     if "github.com/texasdaddy/kw-common@" in ln]
    assert install_lines, "the README no longer shows an install line to check"
    stale = [ln for ln in install_lines if expected not in ln]
    assert stale == [], (
        f"the README pins a version that is not this package's ({kw_common.__version__}): "
        f"{stale}")
