"""Alarms and the email that carries them (D43). No test sends a real email."""

from __future__ import annotations

import httpx
import pytest

from app.services import mailer
from app.settings import Settings

# --- the mailer --------------------------------------------------------------


def _send(settings: Settings) -> bool:
    return mailer.send("subj", "text", "<p>html</p>", to=["a@x.test"], cc=["b@x.test"], settings=settings)


def test_the_suite_can_never_hold_a_real_brevo_key():
    # tests/__init__.py blanks it before .env is read; a real key there is fine.
    assert Settings().brevo_api_key == ""


def test_no_key_is_a_dry_run_that_never_touches_the_network(settings, monkeypatch, caplog):
    monkeypatch.setattr(mailer.httpx, "post", lambda *a, **k: pytest.fail("network used in dry run"))
    with caplog.at_level("WARNING"):
        assert _send(settings) is False
    assert "dry run" in caplog.text


@pytest.mark.parametrize("status, expected", [(201, True), (202, True), (400, False), (500, False)])
def test_only_a_2xx_counts_as_sent(settings, monkeypatch, status, expected):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(status, text="{}")

    monkeypatch.setattr(mailer.httpx, "post", fake_post)
    keyed = settings.model_copy(update={"brevo_api_key": "xkeysib-test-not-real"})
    assert _send(keyed) is expected
    assert seen["url"] == mailer.BREVO_URL
    assert seen["headers"]["api-key"] == "xkeysib-test-not-real"
    assert seen["json"]["to"] == [{"email": "a@x.test"}]
    assert seen["json"]["cc"] == [{"email": "b@x.test"}]
    assert seen["json"]["sender"]["email"] == keyed.alarm_from


@pytest.mark.parametrize("exc", [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), RuntimeError("bug")])
def test_a_network_error_is_false_and_never_raises(settings, monkeypatch, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(mailer.httpx, "post", boom)
    keyed = settings.model_copy(update={"brevo_api_key": "xkeysib-test-not-real"})
    assert _send(keyed) is False


def test_alarm_settings_parse(settings):
    assert settings.alarm_to_list == ["tanjir@sdsmanager.com"]
    assert settings.alarm_cc_list == ["abir@sdsmanager.com", "soham@sdsmanager.com", "shafiq@sdsmanager.com"]
    assert settings.alarm_workday_numbers == {0, 1, 2, 3, 4}
    assert settings.alarm_work_window == (10, 18)
