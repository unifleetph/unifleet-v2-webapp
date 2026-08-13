"""
tests/test_register_success_text.py — /register/success thank-you text
(T4, ARCH-brief-9).
"""

import main


def test_success_page_still_renders_account_code():
    main.app.config.update(TESTING=True)
    client = main.app.test_client()

    r = client.get("/register/success?account_code=TEST")

    assert r.status_code == 200
    assert b"TEST" in r.data


def test_success_page_shows_updated_thank_you_text():
    main.app.config.update(TESTING=True)
    client = main.app.test_client()

    r = client.get("/register/success?account_code=TEST")

    assert b"Thank you for registering with UniFleet." in r.data


def test_success_page_no_longer_shows_old_text():
    main.app.config.update(TESTING=True)
    client = main.app.test_client()

    r = client.get("/register/success?account_code=TEST")

    assert b"your fleet" not in r.data
