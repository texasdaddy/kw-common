"""The generic-code contract, enforced as tests rather than as a promise — FOR EVERY MODULE.

A module in this library earns its place by satisfying rules a reviewer cannot reliably eyeball.
These are the ones a green functional suite says NOTHING about:

1. **It imports cleanly in isolation.** Copy the module alone into an empty directory and import
   it: no consumer module, and no other `kw_common` module beyond the one dependency edge the
   contract allows, may appear in `sys.modules`.
2. **Configuration is INJECTED, never assumed.** The module names no environment variable, no
   default path and no service name — the caller passes what it uses. This is checked against the
   AST rather than the text, so an explanatory comment cannot fail it and a real `os.environ`
   read cannot hide behind one. `alerting_env` is the deliberate exception, and its reads are
   pinned to exactly the three variables the standard names.
3. **Stdlib-first**, 5. **independently importable** and 7. **an explicit `__all__`** — each
   asked of every module below.

⭐⭐ PARAMETRISED OVER `src/kw_common/*.py` (#12). This file used to bind a single `MODULE_PATH`
— `alerting.py` — so every one of its checks measured that module and no other, while
`README.md` said the contract was enforced for all of them. `leakguard` arrived in v1.1.0 with
none of this cover, and module four would have arrived the same way. Now a module is held to the
contract the day it is added rather than the day somebody remembers.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "kw_common"
MODULES = sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")
MODULE_IDS = [p.stem for p in MODULES]

# ⭐ THE ONE INTRA-PACKAGE IMPORT EDGE THE CONTRACT ALLOWS, as data: the environment layer may
# import the core it builds settings for, and nothing else may import anything. A module absent
# from this map may import no sibling at all. Adding an edge is a deliberate edit HERE, with the
# reason beside it — not something a new `from .x import` gets for free.
ALLOWED_INTERNAL_IMPORTS: dict[str, set[str]] = {"alerting_env": {"alerting"}}

# The module that reads the process environment on purpose, and the ONLY names it may read.
ENVIRONMENT_READER = "alerting_env"
DECLARED_VARIABLES = {"SHARED_ROOT", "CONFIG_PATH", "DEPLOY_ENV"}

# ⛔ DELIBERATELY NO LIST OF CONSUMER MODULE NAMES HERE. An earlier version enumerated the
# adopting services by name, which published a private deployment's inventory into a PUBLIC
# repository — the exact class of disclosure the leak guard exists to stop, and one it
# structurally cannot catch, because a bare project name has no shape to match.
#
# The enumeration was never needed. Two positive properties replace it, stated below and scoped
# honestly: after importing a module alone, (a) nothing outside the standard library is in
# `sys.modules`, and (b) the sandbox directory supplied nothing but the module and its declared
# dependencies. Neither needs a list to maintain, and together they cover consumers that do not
# exist yet.
#
# ⚠️ (a) ALONE IS NOT A SUPERSET of the list it replaced — a consumer whose top-level name
# collides with a standard-library one would be invisible to it. That is what (b) is for: it asks
# where a module was loaded FROM, which is a question a name cannot answer.


def test_the_modules_are_where_the_test_thinks_they_are() -> None:
    """A vacuity guard: every check below reads these files. A glob that resolved somewhere else,
    or an empty one, would make every parametrised test below pass over nothing."""
    assert len(MODULES) >= 3, MODULE_IDS
    assert "alerting" in MODULE_IDS and "leakguard" in MODULE_IDS
    for module in MODULES:
        assert module.is_file()
    assert "def notify(" in (SRC / "alerting.py").read_text(encoding="utf-8")


def _expected_from_sandbox(module: Path) -> list[str]:
    """Every module the sandbox is allowed to have supplied after importing `module`."""
    deps = ALLOWED_INTERNAL_IMPORTS.get(module.stem, set())
    return sorted({"kw_common", f"kw_common.{module.stem}", *(f"kw_common.{d}" for d in deps)})


def _run_isolated(tmp_path: Path, module: Path,
                  script: str) -> subprocess.CompletedProcess[str]:
    """Run `script` in a directory holding ONLY `module` (plus its declared dependency edge, if
    any) inside a bare `kw_common` package, with the repository off the path.

    The package form rather than a bare copy, because `alerting_env` imports its sibling
    relatively and "alone" for it means "with exactly that one edge"; the same form is used for
    every module so the property measured is the same property.

    ⚠️ `encoding=`/`errors=` explicitly, never a bare `text=True`: that decodes the child's output
    with the LOCALE codec, which on a Windows workstation is cp1252 — so a harness capturing a
    child whose output carries a non-ASCII assertion message crashes only when a test FAILS. It
    works on the green control and dies on the first real result, which is exactly the path this
    harness exists to exercise.
    """
    sandbox = tmp_path / "sandbox"
    package = sandbox / "kw_common"
    package.mkdir(parents=True)
    # The real `__init__.py`, which the contract says re-exports nothing — proven by
    # `test_importing_the_package_does_not_import_any_submodule` below.
    shutil.copy(SRC / "__init__.py", package / "__init__.py")
    shutil.copy(module, package / module.name)
    for dep in ALLOWED_INTERNAL_IMPORTS.get(module.stem, ()):
        shutil.copy(SRC / f"{dep}.py", package / f"{dep}.py")
    # `-E` ignores every PYTHON* variable (PYTHONPATH, PYTHONHOME); `-s` drops the user site
    # directory; `-S` drops site-packages entirely. What is left on `sys.path` is the standard
    # library and the sandbox directory, and NOTHING else — which is what makes "it imported
    # alone" mean what it says rather than "it found everything it needed already installed".
    #
    # ⛔ NOT `-I`. That looks like the right flag and is the obvious first reach, but `-I` also
    # removes the CURRENT DIRECTORY from `sys.path`, so the child cannot see the copied package at
    # all and the run fails with `ModuleNotFoundError` — a false RED that reads exactly like the
    # property being violated.
    # `-B` (not PYTHONDONTWRITEBYTECODE, which `-E` would ignore) keeps a `__pycache__` out of
    # the sandbox, so a second run cannot import a stale copy.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    return subprocess.run(
        [sys.executable, "-E", "-s", "-S", "-B", "-c", script],
        cwd=sandbox,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        env=env,
    )


@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_the_module_imports_alone_with_no_consumer_in_sys_modules(tmp_path: Path,
                                                                   module: Path) -> None:
    """⭐ Copy it into an empty directory and import it. Nothing from any consuming service, and
    nothing from the rest of this library beyond the declared edge, may come along."""
    script = (
        "import sys, json\n"
        f"import kw_common.{module.stem}\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = _run_isolated(tmp_path, module, script)

    assert result.returncode == 0, f"{module.stem} did not import alone:\n{result.stderr}"
    loaded = set(json.loads(result.stdout))

    # Stated positively and without naming anyone: nothing outside the standard library came
    # along. It needs no list to maintain and it covers consumers that do not exist yet.
    #
    # ⚠️ SCOPED HONESTLY — this is NOT an unconditional superset of the name list it replaced.
    # A module whose TOP-LEVEL name collides with a standard-library one (`array`, `platform`,
    # `queue`, `select`, `stat`, `code` are all plausible names in a fleet) would be invisible
    # here. The origin test below is what closes the collision case, because it asks WHERE a
    # module came from rather than what it is called.
    foreign = sorted(m for m in loaded
                     if m.partition(".")[0] not in sys.stdlib_module_names
                     and m.partition(".")[0] not in ("kw_common", "__main__"))
    assert foreign == [], f"importing {module.stem} dragged in non-stdlib modules: {foreign}"
    kw = sorted(m for m in loaded if m.startswith("kw_common"))
    assert kw == _expected_from_sandbox(module), (
        f"{module.stem} pulled in {kw}; the contract allows exactly "
        f"{_expected_from_sandbox(module)}")


@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_nothing_but_the_module_and_its_declared_edge_come_from_the_sandbox(
        tmp_path: Path, module: Path) -> None:
    """The origin check, which a name-based one cannot make.

    Asks where every loaded module was loaded FROM, and requires the sandbox directory to have
    supplied exactly the package, the module, and its declared dependency edge. A consumer module
    sitting beside it is caught here whatever it is called — including when its name collides
    with a standard-library module, which is precisely the case the name-based filter above
    cannot see.
    """
    script = (
        "import sys, json, os\n"
        f"import kw_common.{module.stem}\n"
        "here = os.path.realpath(os.getcwd())\n"
        "from_sandbox = sorted(\n"
        "    name for name, mod in list(sys.modules.items())\n"
        "    if getattr(mod, '__file__', None)\n"
        "    and os.path.realpath(mod.__file__).startswith(here + os.sep)\n"
        ")\n"
        "print(json.dumps(from_sandbox))\n"
    )
    result = _run_isolated(tmp_path, module, script)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == _expected_from_sandbox(module), (
        f"the sandbox supplied something other than {module.stem} and its declared edge: "
        f"{result.stdout}")


@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_the_module_needs_no_third_party_package(tmp_path: Path, module: Path) -> None:
    """Stdlib-first is a contract: every third-party dependency here is a supply-chain surface
    across the WHOLE fleet at once. `-S` above puts site-packages out of reach, so a third-party
    import would already fail the run; this states the property directly as well, so the failure
    NAMES the offending package instead of arriving as a bare ImportError."""
    # ⛔ NOT a `sysconfig.get_paths()['stdlib']` prefix test. The C extension modules alerting
    # legitimately pulls in — `_ssl`, `_socket`, `_hashlib`, `_bz2`, `_lzma`, `select` — live in
    # `DLLs/` or `lib-dynload/`, OUTSIDE that directory, so a path-prefix check reports six
    # standard-library modules as third-party. `sys.stdlib_module_names` (3.10+) is the frozen
    # set of names the interpreter itself considers standard, which is the question being asked.
    script = (
        "import sys, json\n"
        f"import kw_common.{module.stem}\n"
        "outside = sorted(\n"
        "    name for name in list(sys.modules)\n"
        "    if name.partition('.')[0] not in sys.stdlib_module_names\n"
        "    and name.partition('.')[0] not in ('kw_common', '__main__')\n"
        ")\n"
        "print(json.dumps(outside))\n"
    )
    result = _run_isolated(tmp_path, module, script)

    assert result.returncode == 0, result.stderr
    outside = json.loads(result.stdout)
    assert outside == [], f"{module.stem} imported non-stdlib modules: {outside}"


def test_importing_the_package_does_not_import_any_submodule() -> None:
    """Contract rule 5, from the other side: `import kw_common` must stay free.

    A convenience re-export in `__init__.py` would make every consumer of every future module pay
    for alerting's `smtplib`/`ssl`/`urllib` import whether or not it alerts.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "import kw_common, sys;"
         "print([m for m in sys.modules if m.startswith('kw_common.')])"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"importing kw_common pulled in submodules: {result.stdout.strip()}")


# --------------------------------------------------------------- independently importable
def _sibling_imports(tree: ast.Module) -> set[str]:
    """Every `kw_common` sibling a module imports, whatever the spelling."""
    siblings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.module:                      # from .x import y
                siblings.add(node.module.partition(".")[0])
            elif node.module and node.module.startswith("kw_common."):
                siblings.add(node.module.split(".")[1])
            elif node.module == "kw_common":                    # from kw_common import x
                siblings.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("kw_common."):
                    siblings.add(alias.name.split(".")[1])
    return siblings


@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_intra_package_imports_follow_the_one_allowed_direction(module: Path) -> None:
    """Contract rule 5 pinned statically, so `leakguard` importing `alerting` (or the reverse) is
    a red test and not a review comment. `alerting_env -> alerting` is the one allowed edge, and
    it is asserted to EXIST as well as to be the only one — the environment layer is nothing
    without the core it builds settings for."""
    found = _sibling_imports(_module_ast(module))
    assert found == ALLOWED_INTERNAL_IMPORTS.get(module.stem, set()), (
        f"{module.stem} imports {sorted(found)}; the contract allows "
        f"{sorted(ALLOWED_INTERNAL_IMPORTS.get(module.stem, set()))}")


@pytest.mark.parametrize("source, expected", [
    pytest.param("from .alerting import notify\n", {"alerting"}, id="relative"),
    pytest.param("from kw_common.leakguard import main\n", {"leakguard"}, id="absolute-from"),
    pytest.param("import kw_common.alerting as a\n", {"alerting"}, id="absolute-import"),
    pytest.param("from kw_common import alerting_env\n", {"alerting_env"}, id="package-from"),
    pytest.param("import os\nfrom pathlib import Path\n", set(), id="stdlib-only"),
])
def test_the_sibling_import_walk_sees_every_spelling(source: str, expected: set[str]) -> None:
    """The NEGATIVE direction for the guard above: four spellings of a sibling import, and one
    that is not, through the REAL walk — a guard recognising one spelling is an arms race."""
    assert _sibling_imports(ast.parse(source)) == expected


# ------------------------------------------------------------------ configuration is injected
def _module_ast(module: Path) -> ast.Module:
    return ast.parse(module.read_text(encoding="utf-8"), filename=str(module))


def _environment_reads(tree: ast.Module) -> list[str]:
    """Every place `tree` reads the process environment.

    ⭐ THE SHARED IMPLEMENTATION, and that sharing is the point. The positive test below and the
    negative one after it MUST run the same code: an earlier version re-implemented this walk
    inline in the negative test, so gutting the real one left the "would it catch anything?"
    test still passing. It validated a copy, not the guard.
    """
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _ENVIRONMENT_NAMES:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                offenders.append(f"os.{node.attr} at line {node.lineno}")
        elif isinstance(node, ast.Name) and node.id in _ENVIRONMENT_NAMES:
            offenders.append(f"{node.id} at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            # ⭐ THE ALIASED SPELLING. `from os import environ as _ambient` binds the mapping to a
            # name the two arms above never see, and every later `_ambient.get(...)` walked past
            # this guard — measured by the audit: the aliased form survived on every module the
            # walk was run over. An import is where the environment enters a module, so the
            # import is what is flagged, whatever it is called afterwards.
            for alias in node.names:
                if alias.name in _ENVIRONMENT_NAMES:
                    offenders.append(f"from os import {alias.name} at line {node.lineno}")
    return offenders


# `environb` too: it is the same mapping as bytes, and a walk that names one spelling of the
# thing it forbids is an arms race it loses.
_ENVIRONMENT_NAMES = ("environ", "environb", "getenv", "getenvb")


@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_the_module_never_reads_the_environment(module: Path) -> None:
    """⭐ AST, not grep — the docstrings explain that config is injected INSTEAD of read from the
    environment, so a text search would trip on the very sentence documenting the rule.

    An environment variable NAME is configuration the consumer would have to match exactly, which
    makes it a hardcoded contract by another spelling. The caller passes values; a module reads
    none of its own — except the environment layer, whose ONE read site is the seam every
    variable goes through, and whose names are pinned by the test after this one.
    """
    offenders = _environment_reads(_module_ast(module))
    if module.stem == ENVIRONMENT_READER:
        assert len(offenders) == 1, (
            f"{module.stem} reads the environment at {offenders}; the contract allows exactly "
            f"one seam (`_require_env`), so every read goes through the same refusal")
        return
    assert offenders == [], (
        f"{module.stem} reads the environment: {offenders}. Configuration is INJECTED; a "
        f"consumer passes what it uses.")


def test_the_environment_layer_reads_exactly_the_three_declared_variables(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The BEHAVIOURAL half of the exception: the names actually read are the three the standard
    declares, no more. A fourth variable — however it was spelled — is the divergence this package
    exists to remove, and an AST count of read SITES cannot see it."""
    from kw_common import alerting_env
    from kw_common.alerting import AlertSettings

    seen: set[str] = set()

    class Recording(dict):
        def get(self, key: str, default: object = None) -> object:  # type: ignore[override]
            seen.add(key)
            return super().get(key, default)

    # ⚠️ A REAL, COMPLETE shared root, so the loader runs to its END rather than refusing at the
    # first check — a fourth read placed AFTER an early refusal was invisible to this test
    # (the gate planted one and stayed green). The values are the suite's synthetic ones.
    config = alerting_env.config_file_for(tmp_path)
    config.parent.mkdir(parents=True)
    config.write_text(
        "EMAIL_TO=ops@example.com\nEMAIL_FROM=svc@example.com\nSMTP_HOST=smtp.example.com\n"
        "SMTP_PORT=587\nSMTP_PASSWORD=not-a-real-password\n"
        "NTFY_URL_DEV=https://ntfy.example.com/dev\nNTFY_URL_PROD=https://ntfy.example.com/prod\n",
        encoding="utf-8")
    monkeypatch.setattr(os, "environ", Recording(
        SHARED_ROOT=str(tmp_path), CONFIG_PATH=str(tmp_path / "cfg"), DEPLOY_ENV="dev"))
    settings = alerting_env.load_alert_settings_from_env("svc")   # must complete, no refusal
    assert settings.config_file == str(config)
    with contextlib.suppress(alerting_env.AlertEnvError):
        alerting_env.validate_boot_from_env(
            AlertSettings(service="svc", config_file=str(tmp_path / "alerting.env")))
    assert seen == DECLARED_VARIABLES, (
        f"the environment layer read {sorted(seen)}; the standard declares "
        f"{sorted(DECLARED_VARIABLES)} and nothing else")


@pytest.mark.parametrize("source", [
    pytest.param("import os\n\n\ndef f() -> str:\n    return os.environ.get('SECRET', '')\n",
                 id="os.environ.get"),
    pytest.param("import os\nX = os.environ['SECRET']\n", id="os.environ-subscript"),
    pytest.param("import os\n\n\ndef f():\n    return os.getenv('SECRET')\n", id="os.getenv"),
    pytest.param("from os import getenv\nX = getenv('SECRET')\n", id="bare-getenv"),
    pytest.param("from os import environ as _ambient\nX = _ambient.get('SECRET')\n",
                 id="aliased-environ"),
    pytest.param("import os\nX = os.environb.get(b'SECRET')\n", id="os.environb"),
])
def test_the_guard_above_would_actually_catch_an_environment_read(source: str) -> None:
    """⭐ THE NEGATIVE DIRECTION, through the REAL guard.

    "No `os.environ` node was found" is equally true of a module that has none and of a walk that
    looks in the wrong place. This calls `_environment_reads` — the same function the positive
    test calls — on modules that DO read the environment, so gutting the walk turns this test red
    as well. Four spellings, because a guard that only recognises one spelling of the thing it
    forbids is an arms race it loses.
    """
    assert _environment_reads(ast.parse(source)), (
        "the guard cannot see this environment read, so the positive test proves nothing")


# The modules whose EVERY string constant is checked for an absolute path. `leakguard` is not
# among them on purpose: its self-test corpora are synthetic path SHAPES by design (`/mnt/POOL/...`
# and the deny cases), exercised by `--selftest` and scanned for real values by the guard itself,
# so for it only the module-level assignments are checked.
_DEEP_PATH_SCAN = {"alerting", "alerting_env"}
_ABSOLUTE_PATH = re.compile(r"^(?:/[\w.-]|[A-Za-z]:[\\/])")


def _string_constants(tree: ast.Module, *, deep: bool) -> list[ast.Constant]:
    """Every string constant in `tree` — at any depth when `deep`, docstrings excluded, else
    only the value of a module-level assignment."""
    if not deep:
        return [node.value for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)
                  and isinstance(node.body[0].value.value, str)}
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_no_constant_holds_an_absolute_path(module: Path) -> None:
    """A default path is a promise this library cannot keep — see the module docstring. The
    predecessor defaulted its error log into a volume its adopters did not all have, and got
    `os.makedirs` SUCCEEDING inside the container's disposable layer: no error, no warning,
    records unreachable from the host.

    ⭐ AT ANY DEPTH for the alerting modules, docstrings excluded. The module-body-only walk this
    used to be let a dataclass FIELD DEFAULT of `/mnt/POOL/appdata/svc/errors.log` through
    (measured by the audit) — which is precisely the spelling a default path takes in this
    library, since every path setting is an `AlertSettings` field."""
    deep = module.stem in _DEEP_PATH_SCAN
    offenders = [f"line {node.lineno}: {node.value!r}"
                 for node in _string_constants(_module_ast(module), deep=deep)
                 if _ABSOLUTE_PATH.match(node.value)]
    assert offenders == [], (
        f"{module.stem}: absolute paths must be injected instead: {offenders}")


def test_the_path_walk_would_catch_a_field_default() -> None:
    """The negative direction for the deep walk, through the real helper."""
    source = ("from dataclasses import dataclass\n\n\n@dataclass\nclass S:\n"
              "    error_log: str = '/mnt/POOL/appdata/svc/errors.log'\n"
              "    note: str = 'a docstring-free string'\n")
    found = [n.value for n in _string_constants(ast.parse(source), deep=True)
             if _ABSOLUTE_PATH.match(n.value)]
    assert found == ["/mnt/POOL/appdata/svc/errors.log"]


# ------------------------------------------------------------- stdlib-first, and the matrix
PYPROJECT = SRC.parents[1] / "pyproject.toml"
CI_WORKFLOW = SRC.parents[1] / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = SRC.parents[1] / ".github" / "workflows" / "release.yml"


def test_the_package_declares_no_runtime_dependency() -> None:
    """Contract rule 3, at the one place it is actually decided. Every module test above proves
    a module IMPORTS nothing third-party; this proves nothing is INSTALLED alongside it either —
    `dependencies` is written out empty so that adding one is a visible diff on this line, and
    this is what makes the diff a red test rather than a review comment."""
    text = PYPROJECT.read_text(encoding="utf-8")
    assert re.search(r"^dependencies = \[\]\s*$", text, re.MULTILINE), (
        "pyproject.toml no longer declares `dependencies = []` — a runtime dependency is a "
        "supply-chain surface across the whole fleet and needs a stated justification")


def test_ci_measures_exactly_the_interpreters_the_package_claims() -> None:
    """Contract rule 4: 3.10 and 3.12, both required, in BOTH workflows — and the classifiers
    claim no version CI never runs. Read from the workflow text, because a matrix is only ever
    a piece of YAML; the claim it makes is what this pins."""
    for workflow in (CI_WORKFLOW, RELEASE_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        matrix = re.search(r'python: \[([^\]]*)\]', text)
        assert matrix, f"{workflow.name}: no python matrix found"
        versions = sorted(v.strip().strip('"') for v in matrix.group(1).split(","))
        assert versions == ["3.10", "3.12"], f"{workflow.name} runs {versions}"
    classifiers = re.findall(r'"Programming Language :: Python :: (3\.\d+)"',
                             PYPROJECT.read_text(encoding="utf-8"))
    assert sorted(classifiers) == ["3.10", "3.12"], (
        f"pyproject.toml claims {classifiers}; a classifier for a version CI never runs is a "
        f"claim nobody measured")


def test_the_package_exports_only_its_version() -> None:
    """`__init__.py` re-exports NOTHING — the whole isolation property rests on that — and its
    `__all__` says so. The audit measured that deleting the line left every test green."""
    import kw_common
    assert kw_common.__all__ == ["__version__"]
    assert isinstance(kw_common.__version__, str) and kw_common.__version__


# ------------------------------------------------------------------- the public API is explicit
@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_every_module_declares_an_explicit_public_api(module: Path) -> None:
    """Contract rule 7: `__all__` per module, every name in it real, none of them private, no
    duplicates. Anything not exported may change without a major bump; anything exported is a
    contract under semver — so the list has to exist and has to be true."""
    mod = importlib.import_module(f"kw_common.{module.stem}")
    exported = getattr(mod, "__all__", None)
    assert isinstance(exported, list) and exported, f"{module.stem} declares no `__all__`"
    assert all(isinstance(name, str) for name in exported)
    assert len(set(exported)) == len(exported), f"{module.stem}: duplicate names in __all__"
    private = [name for name in exported if name.startswith("_")]
    assert private == [], f"{module.stem} exports private names: {private}"
    missing = [name for name in exported if not hasattr(mod, name)]
    assert missing == [], f"{module.stem}: __all__ names things that do not exist: {missing}"


@pytest.mark.parametrize("module", MODULES, ids=MODULE_IDS)
def test_every_exported_callable_is_defined_at_module_level(module: Path) -> None:
    """Every exported FUNCTION OR CLASS is a real top-level definition in this file.

    Scoped honestly: this reads `__all__` and checks the names that resolve to a function or a
    class, which is what "a real top-level definition" can be asserted about via the AST. The
    exported CONSTANTS are covered by the test above (which resolves every one of them) and by
    ruff's `F822`, which fails an `__all__` entry that does not exist at all.
    """
    defined = {
        node.name
        for node in _module_ast(module).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    mod = importlib.import_module(f"kw_common.{module.stem}")
    expected = {name for name in mod.__all__
                if callable(getattr(mod, name, None)) and not isinstance(getattr(mod, name), str)}
    missing = sorted(expected - defined)
    assert missing == [], f"{module.stem}: exported but not defined at module level: {missing}"
    assert expected, f"{module.stem}: no exported callables found — the check proves nothing"
