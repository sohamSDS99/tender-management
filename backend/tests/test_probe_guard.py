"""The URL guard on the probe.

Letting a dashboard user make the *server* fetch a URL they type is SSRF. The
dashboard is unauthenticated by design (D23), and that reasoning held because
those writes were expensive-not-confidential — a server-side fetcher pointed at
an arbitrary address is a different class of thing entirely. It can reach the
internal network, cloud metadata endpoints and localhost.

So these are not style checks. Each one is an address a probe must refuse.
"""

from __future__ import annotations

import pytest

from app.services.probe import UnsafeUrl, assert_safe_url


class TestScheme:
    def test_https_is_allowed(self, public_dns):
        assert_safe_url("https://example.gov/api/notices")

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.gov/api",
            "file:///etc/passwd",
            "ftp://example.gov/x",
            "gopher://example.gov/x",
            "//example.gov/x",
        ],
    )
    def test_everything_else_is_refused(self, url):
        with pytest.raises(UnsafeUrl):
            assert_safe_url(url)


class TestAddress:
    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost/api",
            "https://127.0.0.1/api",
            "https://127.0.0.1:8001/api/tenders",
            "https://0.0.0.0/api",
            "https://10.0.0.1/api",
            "https://192.168.1.1/api",
            "https://172.16.0.5/api",
            # The cloud metadata endpoint: the reason this guard exists at all.
            "https://169.254.169.254/latest/meta-data/",
            "https://[::1]/api",
        ],
    )
    def test_private_and_loopback_addresses_are_refused(self, url):
        with pytest.raises(UnsafeUrl):
            assert_safe_url(url)

    def test_a_hostname_resolving_to_a_private_address_is_refused(self, monkeypatch):
        # An attacker controls DNS for a name they own, so a public-looking
        # hostname is not evidence of a public address.
        import app.services.probe as probe

        monkeypatch.setattr(probe, "_resolve", lambda host: ["10.0.0.7"])
        with pytest.raises(UnsafeUrl):
            assert_safe_url("https://totally-public.example/api")

    def test_a_host_that_does_not_resolve_is_refused(self, monkeypatch):
        import app.services.probe as probe

        monkeypatch.setattr(probe, "_resolve", lambda host: [])
        with pytest.raises(UnsafeUrl):
            assert_safe_url("https://nowhere.example/api")


class TestMessages:
    def test_the_message_says_what_to_do(self):
        with pytest.raises(UnsafeUrl) as exc:
            assert_safe_url("http://example.gov/api")
        assert "https" in str(exc.value).lower()


# --- what the probe reports ------------------------------------------------

import httpx  # noqa: E402
import pytest_asyncio  # noqa: F401,E402

from app.services.probe import detect_format, guess_records_path, probe_source  # noqa: E402

OCDS = {"releases": [{"ocid": "ocds-1", "tender": {"title": "A"}}]}
FLAT = {"data": {"items": [{"id": "1", "notice": {"subject": "A"}}, {"id": "2", "notice": {"subject": "B"}}]}}


class TestDetectFormat:
    def test_recognises_ocds(self):
        assert detect_format(OCDS) == "ocds"

    def test_unrecognised_json_is_json(self):
        assert detect_format(FLAT) == "json"

    def test_a_bare_list_is_json(self):
        assert detect_format([{"id": 1}]) == "json"


class TestGuessRecordsPath:
    def test_finds_the_longest_list_of_objects(self):
        assert guess_records_path(FLAT) == "data.items[]"

    def test_a_payload_with_no_list_guesses_nothing(self):
        assert guess_records_path({"meta": {"total": 0}}) == ""


def _client(monkeypatch, public_dns, handler):
    """Point the probe's httpx client at a handler instead of the network."""
    import app.services.probe as probe

    real = httpx.AsyncClient

    def build(**kwargs):
        kwargs.pop("follow_redirects", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(probe.httpx, "AsyncClient", build)


async def test_a_readable_source_reports_what_it_found(monkeypatch, public_dns, settings):
    _client(monkeypatch, public_dns, lambda request: httpx.Response(200, json=FLAT))
    result = await probe_source("https://example.gov/api", settings)
    assert result["ok"] is True
    assert result["found"] == 2
    assert result["format"] == "json"
    assert result["records_path"] == "data.items[]"
    assert any(p["path"] == "notice.subject" for p in result["paths"])


async def test_ocds_is_detected_without_a_mapping(monkeypatch, public_dns, settings):
    _client(monkeypatch, public_dns, lambda request: httpx.Response(200, json=OCDS))
    result = await probe_source("https://example.gov/api", settings)
    assert result["format"] == "ocds"
    assert result["ok"] is True


async def test_a_200_that_yields_nothing_is_not_a_success(monkeypatch, public_dns, settings):
    """The SAM.gov lesson: answering is not the same as working."""
    _client(monkeypatch, public_dns, lambda request: httpx.Response(200, json={"meta": {"total": 0}}))
    result = await probe_source("https://example.gov/api", settings)
    assert result["ok"] is False
    assert result["reason"] == "no_records"


async def test_a_bad_key_reads_differently_from_a_bad_url(monkeypatch, public_dns, settings):
    _client(monkeypatch, public_dns, lambda request: httpx.Response(401, text="invalid api key"))
    result = await probe_source("https://example.gov/api", settings)
    assert result["ok"] is False
    assert result["status"] == 401
    assert "invalid api key" in result["detail"]


async def test_a_non_json_response_says_so(monkeypatch, public_dns, settings):
    _client(monkeypatch, public_dns, lambda request: httpx.Response(200, text="<rss></rss>"))
    result = await probe_source("https://example.gov/api", settings)
    assert result["reason"] == "not_json"


async def test_the_credential_is_sent_the_way_the_source_declares(monkeypatch, public_dns, settings):
    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=FLAT)

    _client(monkeypatch, public_dns, handler)
    await probe_source("https://example.gov/api", settings, credential="K", auth="query", auth_param="key")
    assert "key=K" in seen["url"]

    await probe_source("https://example.gov/api", settings, credential="K", auth="bearer")
    assert seen["auth"] == "Bearer K"


async def test_a_redirect_to_a_private_address_is_refused(monkeypatch, settings):
    """A public host is free to redirect to the metadata endpoint."""
    import app.services.probe as probe

    monkeypatch.setattr(
        probe, "_resolve", lambda host: ["93.184.216.34"] if "example" in host else ["169.254.169.254"]
    )
    _client(
        monkeypatch,
        None,
        lambda request: httpx.Response(302, headers={"location": "https://metadata.internal/latest"}),
    )
    with pytest.raises(UnsafeUrl):
        await probe_source("https://example.gov/api", settings)
