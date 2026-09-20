"""Every third-party module the app imports must be in pyproject's main group.

`rapidfuzz` was imported at main.py's top level and declared nowhere. It only
ever existed because a Nixpacks build environment carried it forward between
deploys. The moment the image was built reproducibly from poetry.lock, gunicorn
died on `ModuleNotFoundError: No module named 'rapidfuzz'` — a dependency the
project had been running on for months without ever recording.

`pillow` was the same shape of hole, one step less lucky: generate_voucher.py
imports PIL directly, but it was only ever present as reportlab's transitive
dependency, so any reportlab release that dropped it would have taken voucher
rendering down.

This test reads the imports rather than the environment, so it fails in CI on
the commit that adds an undeclared import, not on the deploy that stops
carrying it.
"""
import ast
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Directories that never ship in the web image or never run under it.
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", ".worktrees", ".cache",
    "archive",   # retired scripts, kept for reference, never imported
    "tests",     # dev group
}


def _normalize(name):
    """PEP 503 name normalization — Pillow, pillow and PILLOW are one package."""
    return name.lower().replace("_", "-").replace(".", "-")


def _declared_main_dependencies():
    with (REPO / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    deps = pyproject["tool"]["poetry"]["dependencies"]
    return {_normalize(name) for name in deps if name != "python"}


def _first_party_names():
    names = {p.stem for p in REPO.glob("*.py")}
    for child in REPO.iterdir():
        if child.is_dir() and child.name not in SKIP_DIRS:
            names.add(child.name)
    # Modules under scripts/ and db/ are imported by bare name in places
    # because pytest's pythonpath and the container's workdir both put the
    # repo root on sys.path.
    for package_dir in ("scripts", "db"):
        names |= {p.stem for p in (REPO / package_dir).glob("*.py")}
    return names


def _guarded_import_nodes(tree):
    """Imports inside `try: ... except ImportError:` are optional by design.

    scripts/backup_postgres.py imports boto3 that way — S3 upload is
    best-effort, and the backup service runs from Dockerfile.backup, not this
    image. An import the code already handles the absence of is not a missing
    dependency.
    """
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        handled = any(
            isinstance(h.type, ast.Name) and h.type.id in {"ImportError", "ModuleNotFoundError"}
            or isinstance(h.type, ast.Tuple)
            and any(
                isinstance(e, ast.Name) and e.id in {"ImportError", "ModuleNotFoundError"}
                for e in h.type.elts
            )
            or h.type is None
            for h in node.handlers
        )
        if not handled:
            continue
        for child in node.body:
            for sub in ast.walk(child):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    guarded.add(sub)
    return guarded


def _third_party_imports():
    """{top-level module name: {files importing it}} for unguarded imports."""
    stdlib = set(sys.stdlib_module_names)
    first_party = _first_party_names()
    imports = {}

    for path in REPO.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.relative_to(REPO).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            # dummy.py is tracked Python 2 (`print "..."`). A file the
            # interpreter cannot parse cannot be imported, so it cannot
            # contribute a runtime dependency — and failing this test on it
            # would say nothing about dependencies.
            continue
        guarded = _guarded_import_nodes(tree)

        for node in ast.walk(tree):
            if node in guarded:
                continue
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:      # relative import — first party by definition
                    continue
                modules = [(node.module or "").split(".")[0]]
            else:
                continue

            for module in modules:
                if module and module not in stdlib and module not in first_party:
                    imports.setdefault(module, set()).add(
                        str(path.relative_to(REPO))
                    )
    return imports


def test_every_imported_third_party_module_is_a_declared_dependency():
    declared = _declared_main_dependencies()
    import_name_to_dists = packages_distributions()

    missing = []
    for module, files in sorted(_third_party_imports().items()):
        # A module's distribution is usually its import name, but not always
        # (PIL ships in pillow). Accept either spelling.
        candidates = {_normalize(module)}
        candidates |= {_normalize(d) for d in import_name_to_dists.get(module, [])}
        if not candidates & declared:
            missing.append(f"  {module} (imported by {', '.join(sorted(files))})")

    assert not missing, (
        "these modules are imported but not declared in pyproject's main "
        "dependency group, so they are absent from any image built "
        "reproducibly from poetry.lock:\n" + "\n".join(missing)
    )


def test_rapidfuzz_and_pillow_are_declared():
    """Named explicitly — these are the two that were actually missing, and a
    regression here is a production outage, not a lint failure."""
    declared = _declared_main_dependencies()
    assert "rapidfuzz" in declared, "main.py imports rapidfuzz at module level"
    assert "pillow" in declared, "generate_voucher.py imports PIL directly"
