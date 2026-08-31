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

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"


def _quickstart() -> str:
    md = README.read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", md, re.S)
    assert len(blocks) == 1, (
        f"expected exactly one ```python block in README.md, found {len(blocks)} — this test "
        f"needs updating to say which one is the quick-start")
    return blocks[0]


def test_the_readme_has_a_quickstart_at_all() -> None:
    """A vacuity guard. Every check below reads this block; if the extraction silently returned
    nothing, they would all pass over an empty string."""
    src = _quickstart()
    assert "configure(" in src
    assert "notify(" in src


def test_the_quickstart_parses() -> None:
    compile(_quickstart(), str(README), "exec")


def test_every_name_the_quickstart_uses_is_one_it_imports() -> None:
    """⭐ THE DEFECT THAT GOT THROUGH, PINNED.

    Walks the snippet and requires every name it READS to be one it imported, one it defined, or
    a builtin. `warn_if_unconfigured` was none of the three.
    """
    tree = ast.parse(_quickstart())

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

    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    missing = sorted(used - available)
    assert missing == [], (
        f"the README quick-start uses {missing} without importing it — copy-pasting it raises "
        f"NameError")


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


@pytest.mark.parametrize("claim", [
    "pip install git+https://github.com/texasdaddy/kw-common@v1.0.0",
    "Never pin a branch",
])
def test_the_install_instructions_are_present(claim: str) -> None:
    """Criterion: the README carries the install line and the pin-an-exact-tag rule."""
    assert claim in README.read_text(encoding="utf-8")
