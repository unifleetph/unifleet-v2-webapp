"""
tests/test_generate_voucher.py — BASE_URL fallback resolution for voucher
QR codes (T2, ARCH-brief-9).

BASE_URL is read once at module import time, so tests that vary the env
var must reload the module afterward.
"""

import importlib
import os

import generate_voucher


def _reload_with_env(monkeypatch, base_url=None):
    if base_url is None:
        monkeypatch.delenv("BASE_URL", raising=False)
    else:
        monkeypatch.setenv("BASE_URL", base_url)
    importlib.reload(generate_voucher)
    return generate_voucher


def test_base_url_fallback_is_staging_url(monkeypatch):
    mod = _reload_with_env(monkeypatch, base_url=None)

    assert mod.BASE_URL == "https://unifleet-v2-webapp-staging.up.railway.app"


def test_qr_content_uses_resolved_base_url(monkeypatch, tmp_path):
    mod = _reload_with_env(monkeypatch, base_url=None)
    monkeypatch.setattr(mod, "QR_OUTPUT_DIR", str(tmp_path) + "/")

    captured = {}
    real_make = mod.qrcode.make

    def spy_make(content, *args, **kwargs):
        captured["content"] = content
        return real_make(content, *args, **kwargs)

    monkeypatch.setattr(mod.qrcode, "make", spy_make)

    mod.generate_qr_image({"voucher_id": "UF-TEST-1", "vehicle_plate": "ABC-123"}, 0)

    assert captured["content"] == "https://unifleet-v2-webapp-staging.up.railway.app/redeem/UF-TEST-1"


def test_base_url_env_var_still_takes_precedence(monkeypatch):
    mod = _reload_with_env(monkeypatch, base_url="https://custom.example.com")

    assert mod.BASE_URL == "https://custom.example.com"
