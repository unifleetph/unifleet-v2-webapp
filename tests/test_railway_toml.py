"""Tests for railway.toml — the F1.1 Railway build/start/nix config.

Railway reads this file to know how to build and start the service
and which nix packages to install. The four tests below lock the
config down to the contracts called out in PLAN-railway-provisioning.md.
"""
import tomllib
from pathlib import Path


RAILWAY_TOML = Path(__file__).resolve().parent.parent / "rw.txt"


def _load():
    with RAILWAY_TOML.open("rb") as f:
        return tomllib.load(f)


def test_railway_toml_exists_and_parses():
    assert RAILWAY_TOML.is_file(), f"railway.toml must exist at {RAILWAY_TOML}"
    data = _load()
    assert data, "railway.toml must not be empty"


def _build_command(data):
    """Railway accepts build.command or buildCommand depending on schema version."""
    build = data.get("build", {})
    return build.get("command") or build.get("buildCommand") or ""


def _start_command(data):
    """Railway accepts deploy.startCommand or startCommand depending on schema version."""
    deploy = data.get("deploy", {})
    return (
        deploy.get("startCommand")
        or deploy.get("start_command")
        or data.get("startCommand")
        or ""
    )


def _nix_packages(data):
    return data.get("nixPackages") or data.get("nix", {}).get("packages") or []


def test_railway_toml_declares_poetry_build():
    cmd = _build_command(_load())
    assert "poetry install" in cmd, (
        f"railway.toml build.command must include 'poetry install' (got: {cmd!r})"
    )


def test_railway_toml_declares_gunicorn_start_with_dynamic_port():
    cmd = _start_command(_load())
    assert "gunicorn" in cmd, (
        f"railway.toml start command must include 'gunicorn' (got: {cmd!r})"
    )
    assert "0.0.0.0:$PORT" in cmd, (
        f"railway.toml start command must bind to 0.0.0.0:$PORT (got: {cmd!r})"
    )
    assert "0.0.0.0:5000" not in cmd, (
        f"railway.toml start command must NOT hardcode port 5000 (got: {cmd!r})"
    )


def test_railway_toml_declares_required_nix_packages():
    packages = _nix_packages(_load())
    assert "freetype" in packages, (
        f"railway.toml must declare 'freetype' nix package (got: {packages!r})"
    )
    assert "glibcLocales" in packages, (
        f"railway.toml must declare 'glibcLocales' nix package (got: {packages!r})"
    )


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


def test_railway_build_command_uses_a_flag_poetry_still_supports():
    """`--no-dev` was removed in Poetry 2.x — it errors outright.

    rw.txt carried it, so on Railway the build step failed and the image kept
    whatever dependencies an earlier successful build had left behind. That
    was invisible until a new dependency was added (requests, for the mailer),
    at which point importing it failed at runtime, main.py's guarded import
    disabled all email, and the only visible symptom was "Recipient management
    is unavailable" on one admin page.
    """
    cmd = _load()["build"]["command"]

    assert "--no-dev" not in cmd, (
        "Poetry 2.x removed --no-dev; this build step fails and leaves stale "
        "dependencies in the image"
    )
    assert "--only main" in cmd, (
        "install production dependencies explicitly, matching the Dockerfile"
    )


def test_the_build_command_matches_the_dockerfile():
    """Both paths must install the same dependency set. They diverged: the
    Dockerfile used --only main while rw.txt used the removed --no-dev, so a
    local build had requests and the deployed image did not."""
    dockerfile = (RAILWAY_TOML.parent / "Dockerfile").read_text(encoding="utf-8")
    cmd = _load()["build"]["command"]

    assert "poetry install --only main" in dockerfile
    assert "poetry install --only main" in cmd
