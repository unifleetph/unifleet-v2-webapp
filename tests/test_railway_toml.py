"""Tests for rw.txt — the Railway build/start config.

Railway reads this file to know how to build and start the service. The
tests below lock down the contracts called out in
PLAN-railway-provisioning.md, plus the two build-reproducibility rules
learned from production outages.
"""
import tomllib
from pathlib import Path


RAILWAY_TOML = Path(__file__).resolve().parent.parent / "rw.txt"
DOCKERFILE = RAILWAY_TOML.parent / "Dockerfile"


def _load():
    with RAILWAY_TOML.open("rb") as f:
        return tomllib.load(f)


def test_railway_toml_exists_and_parses():
    assert RAILWAY_TOML.is_file(), f"rw.txt must exist at {RAILWAY_TOML}"
    data = _load()
    assert data, "rw.txt must not be empty"


def _start_command(data):
    """Railway accepts deploy.startCommand or startCommand depending on schema version."""
    deploy = data.get("deploy", {})
    return (
        deploy.get("startCommand")
        or deploy.get("start_command")
        or data.get("startCommand")
        or ""
    )


def test_railway_toml_declares_gunicorn_start_with_dynamic_port():
    cmd = _start_command(_load())
    assert "gunicorn" in cmd, (
        f"rw.txt start command must include 'gunicorn' (got: {cmd!r})"
    )
    assert "0.0.0.0:$PORT" in cmd, (
        f"rw.txt start command must bind to 0.0.0.0:$PORT (got: {cmd!r})"
    )
    assert "0.0.0.0:5000" not in cmd, (
        f"rw.txt start command must NOT hardcode port 5000 (got: {cmd!r})"
    )


def test_railway_builds_from_the_dockerfile():
    """Nixpacks reuses the environment an earlier build left behind, so a
    `poetry install` there overlays the lock onto stale packages instead of
    replacing them. That shipped two outages: a failed install that kept the
    old dependency set (no `requests`), then a charset-normalizer downgrade
    that left 4.x's compiled `cd` extension next to 3.4.3's pure-Python
    modules ("module 'charset_normalizer.md' has no attribute 'CharInfo'").
    Both took email and /admin/recipients down through main.py's guarded
    import. A Dockerfile build starts from a clean image every time.
    """
    build = _load().get("build", {})

    assert build.get("builder", "").upper() == "DOCKERFILE", (
        "rw.txt must pin the Dockerfile builder; Nixpacks carries stale "
        f"site-packages between builds (got: {build.get('builder')!r})"
    )
    assert build.get("dockerfilePath") == "Dockerfile"
    assert DOCKERFILE.is_file(), f"the referenced Dockerfile must exist at {DOCKERFILE}"


def test_railway_toml_leaves_dependency_installation_to_the_dockerfile():
    """A build.command or [nix] block here is a leftover from the Nixpacks
    era. Railway ignores both under the Dockerfile builder, so keeping them
    only invites someone to edit the dead copy and expect a deploy to change.
    """
    data = _load()
    build = data.get("build", {})

    assert not (build.get("command") or build.get("buildCommand")), (
        "the Dockerfile installs dependencies; a build command here is dead config"
    )
    assert "nix" not in data and "nixPackages" not in data, (
        "system packages come from the Dockerfile's apt-get, not a [nix] block"
    )


def test_the_dockerfile_installs_production_dependencies_from_the_lock():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "poetry.lock" in dockerfile, "install from the lock, not a resolve at build time"
    assert "poetry install --only main" in dockerfile, (
        "install production dependencies explicitly"
    )
    assert "--no-dev" not in dockerfile, (
        "Poetry 2.x removed --no-dev; the install fails outright and the "
        "build silently keeps whatever dependencies were there before"
    )


def test_the_dockerfile_carries_the_system_libraries_nix_used_to_provide():
    """freetype was a [nix] package; Pillow needs it to render voucher text."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "freetype" in dockerfile


def test_railway_toml_declares_no_cron_table():
    """Railway's config-as-code has no cron table, and one config file
    describes one service.

    A `[[cron]]` block was added here for the nightly supplier sheet and
    Railway would have ignored it silently — the report would never have run
    and nothing would have reported an error. The schedule lives in the
    dashboard, like the `backup` service (review finding B4).
    """
    raw = RAILWAY_TOML.read_text(encoding="utf-8")
    data = _load()

    assert "cron" not in data, (
        "rw.txt declares a cron table; Railway ignores it silently, so the "
        "schedule must be configured as a Cron Schedule service instead"
    )
    assert "[[cron]]" not in raw
    assert "docs/runbook.md" in raw, (
        "leave a pointer to where the schedule actually lives"
    )
