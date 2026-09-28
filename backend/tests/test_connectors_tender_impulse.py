"""Tender Impulse: id-paged, AES-encrypted, checksum-verified (D41).

No live record existed when this was written - the credentials are issued on a
paid agreement - so every response here is built by encrypting a plaintext
fixture exactly as the docs describe the server doing it. That makes the
decrypt path, the checksum and the paging all real code under test; what the
fixture cannot prove is that the *field values* look like production's.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import select

from app.connectors.base import ConnectorError
from app.connectors.registry import SOURCE_NAMES, source_catalog
from app.connectors.tender_impulse import TenderImpulseConnector, decrypt
from app.models import AppSetting, FetchRun
from app.services import cursors, ingest
from app.services.credentials import (
    OPAQUE_SECRETS,
    SETTINGS_SECRETS,
    set_credential,
    set_secret,
    settings_with_stored_credentials,
)
from app.settings import Settings
from tests.conftest import fixture_json

TOKEN = "ti-token-not-real"
KEY = "ti-key-not-real"  # 15 bytes: exercises the "0"-padding rule
START = 8156393


def encrypt(plaintext: str, key: str, iv: bytes | None = None) -> str:
    """The server side, as the docs describe it: AES-128-CBC, PKCS#7, `<ct>:<iv>`."""
    iv = iv or os.urandom(16)
    raw_key = key.encode("utf-8").ljust(16, b"0")[:16]
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(raw_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return f"{base64.b64encode(ciphertext).decode()}:{base64.b64encode(iv).decode()}"


def envelope(payload: dict, key: str = KEY, crc: str | None = None) -> dict:
    plaintext = json.dumps(payload)
    return {
        "data": encrypt(plaintext, key),
        "crc": crc if crc is not None else hashlib.md5(plaintext.encode("utf-8")).hexdigest().upper(),
    }


def feed(calls: list[httpx.Request], *, fail_after: int | None = None, status: int = 200):
    """Two batches after START, then empty - each keyed by the lastid that asks for it."""
    batches = fixture_json("tender_impulse_tenders.json")["batches"]
    by_last_id: dict[int, dict] = {}
    last = START
    for batch in batches:
        by_last_id[last] = batch
        last = batch["fetchid"]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status != 200:
            return httpx.Response(status, text="Invalid Token", headers={"content-type": "text/html"})
        if fail_after is not None and len(calls) > fail_after:
            return httpx.Response(502, text="bad gateway", headers={"content-type": "text/html"})
        last_id = int(request.url.params["lastid"])
        batch = by_last_id.get(last_id, {"tenders": [], "fetchid": last_id})
        body = envelope({"status": "success", "tenders": batch["tenders"], "fetchid": batch["fetchid"]})
        # PHP's default content type, whatever the body is.
        return httpx.Response(
            200, content=json.dumps(body), headers={"content-type": "text/html; charset=UTF-8"}
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def ti_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "tender_impulse_access_token": TOKEN,
            "tender_impulse_encryption_key": KEY,
            "tender_impulse_start_id": str(START),
            "tender_impulse_page_pause_seconds": 0.0,
            "retry_backoff_seconds": 0.0,
        }
    )


async def sweep(connector: TenderImpulseConnector):
    from tests.test_connectors import DATE_FROM, DATE_TO

    return await connector.fetch(DATE_FROM, DATE_TO)


# --- decryption ------------------------------------------------------------


@pytest.mark.parametrize("key", ["short", "exactly16bytes!!", "a-key-longer-than-sixteen-bytes"])
def test_decrypt_matches_the_documented_key_rule(key):
    """UTF-8, padded with ASCII "0" or truncated to 16 bytes - all four reference clients agree."""
    assert decrypt(encrypt('{"status":"success"}', key), key) == '{"status":"success"}'


def test_decrypt_is_byte_compatible_with_the_vendor_rule_not_just_with_itself():
    """A fixed vector, so a symmetric mistake in both helpers cannot pass.

    `short` pads to b"short00000000000"; this ciphertext was produced from that
    literal key, independently of `encrypt`'s padding code.
    """
    iv = bytes(range(16))
    padder = padding.PKCS7(128).padder()
    padded = padder.update(b'{"a":1}') + padder.finalize()
    enc = Cipher(algorithms.AES(b"short00000000000"), modes.CBC(iv)).encryptor()
    data = f"{base64.b64encode(enc.update(padded) + enc.finalize()).decode()}:{base64.b64encode(iv).decode()}"
    assert decrypt(data, "short") == '{"a":1}'


def test_decrypt_refuses_a_payload_without_an_iv():
    with pytest.raises(ValueError):
        decrypt("bm90aGluZw==", KEY)


# --- paging and the bookmark ---------------------------------------------


async def test_pages_forward_from_the_start_id_until_an_empty_batch(ti_settings):
    calls: list[httpx.Request] = []
    connector = TenderImpulseConnector(ti_settings, transport=feed(calls))
    tenders = await sweep(connector)

    assert [int(c.url.params["lastid"]) for c in calls] == [START, 8156396, 8156398]
    assert connector.next_cursor == "8156398"
    assert all(c.headers["authorization"] == f"Bearer {TOKEN}" for c in calls)
    # Road works and furniture are dropped; the lab notice is kept on its
    # other_information alone - its title says nothing topical.
    assert sorted(t.source_notice_id for t in tenders) == ["8156394", "8156396", "8156398"]


async def test_a_stored_bookmark_beats_the_start_id(ti_settings):
    calls: list[httpx.Request] = []
    resumed = ti_settings.model_copy(update={"tender_impulse_cursor": "8156396"})
    connector = TenderImpulseConnector(resumed, transport=feed(calls))
    tenders = await sweep(connector)
    assert int(calls[0].url.params["lastid"]) == 8156396
    assert [t.source_notice_id for t in tenders] == ["8156398"]


async def test_nothing_new_leaves_the_bookmark_where_it_was(ti_settings):
    calls: list[httpx.Request] = []
    caught_up = ti_settings.model_copy(update={"tender_impulse_cursor": "8156398"})
    connector = TenderImpulseConnector(caught_up, transport=feed(calls))
    assert await sweep(connector) == []
    assert len(calls) == 1
    assert connector.next_cursor is None


async def test_the_request_budget_stops_the_sweep_and_the_next_one_resumes(ti_settings):
    calls: list[httpx.Request] = []
    capped = ti_settings.model_copy(update={"tender_impulse_max_requests_per_sweep": 1})
    first = TenderImpulseConnector(capped, transport=feed(calls))
    await sweep(first)
    assert len(calls) == 1
    assert first.next_cursor == "8156396"

    second = TenderImpulseConnector(
        capped.model_copy(update={"tender_impulse_cursor": first.next_cursor}), transport=feed(calls)
    )
    tenders = await sweep(second)
    assert [t.source_notice_id for t in tenders] == ["8156398"]


async def test_a_failure_after_progress_keeps_what_was_read_and_stops_the_bookmark_there(ti_settings):
    calls: list[httpx.Request] = []
    connector = TenderImpulseConnector(ti_settings, transport=feed(calls, fail_after=1))
    tenders = await sweep(connector)
    assert {t.source_notice_id for t in tenders} == {"8156394", "8156396"}
    # Not 8156398: that batch was never read, so the next sweep must ask again.
    assert connector.next_cursor == "8156396"


async def test_a_failure_on_the_first_request_is_a_failed_run(ti_settings):
    connector = TenderImpulseConnector(ti_settings, transport=feed([], fail_after=0))
    with pytest.raises(ConnectorError):
        await sweep(connector)
    assert connector.next_cursor is None


async def test_a_refused_token_says_so(ti_settings):
    connector = TenderImpulseConnector(ti_settings, transport=feed([], status=401))
    with pytest.raises(ConnectorError) as err:
        await sweep(connector)
    assert err.value.status == 401
    assert "access token" in err.value.message


async def test_a_wrong_key_says_so_rather_than_crashing(ti_settings):
    wrong = ti_settings.model_copy(update={"tender_impulse_encryption_key": "not-the-key-at-all"})
    with pytest.raises(ConnectorError) as err:
        await sweep(TenderImpulseConnector(wrong, transport=feed([])))
    assert "encryption key" in err.value.message


async def test_a_checksum_mismatch_discards_the_batch(ti_settings):
    def handler(request):
        body = envelope({"status": "success", "tenders": [{"tender_id": 1}], "fetchid": 1}, crc="0" * 32)
        return httpx.Response(200, json=body)

    with pytest.raises(ConnectorError) as err:
        await sweep(TenderImpulseConnector(ti_settings, transport=httpx.MockTransport(handler)))
    assert "checksum" in err.value.message


async def test_an_error_inside_a_200_is_a_failure(ti_settings):
    """The API reports a lastid past its maximum inside an HTTP 200 body."""

    def handler(request):
        msg = "lastid exceeds the maximum allowed value 9999999"
        return httpx.Response(200, json=envelope({"status": "error", "msg": msg}))

    with pytest.raises(ConnectorError) as err:
        await sweep(TenderImpulseConnector(ti_settings, transport=httpx.MockTransport(handler)))
    assert "9999999" in err.value.message


async def test_a_fetchid_that_does_not_advance_cannot_loop(ti_settings):
    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        last_id = int(request.url.params["lastid"])
        tenders = [{"tender_id": last_id + 1, "title": "Safety data sheet service"}]
        return httpx.Response(
            200, json=envelope({"status": "success", "tenders": tenders, "fetchid": last_id})
        )

    connector = TenderImpulseConnector(ti_settings, transport=httpx.MockTransport(handler))
    await sweep(connector)
    assert len(calls) == 1
    assert connector.next_cursor is None


# --- normalisation ---------------------------------------------------------


async def test_normalises_the_documented_fields(ti_settings):
    tenders = {
        t.source_notice_id: t for t in await sweep(TenderImpulseConnector(ti_settings, transport=feed([])))
    }
    sds = tenders["8156394"]
    assert sds.source == "tender_impulse"
    assert sds.buyer_name == "Queensland Health"
    assert sds.buyer_country == "Australia"
    assert sds.reference_number == "QH-2026-0412"
    assert sds.delivery_location == "Brisbane, Queensland"
    assert sds.deadline is not None and sds.deadline.date().isoformat() == "2030-06-22"
    assert sds.status == "open"
    assert sds.procurement_stage == "tender"
    assert sds.notice_type == "Services"
    assert (sds.estimated_value, sds.currency) == (450000.0, "AUD")
    assert sds.classification_codes == [
        {"scheme": "CPV", "code": "48000000", "description": "Software package and information systems"},
        {"scheme": "CPV", "code": "72260000", "description": "Software-related services"},
    ]
    assert sds.document_urls == ["https://tenderimpulse.com/documents/8156394/specification.pdf"]
    assert sds.source_url == "https://qtenders.example.invalid/tender/QH-2026-0412"

    lab = tenders["8156396"]
    assert lab.status == "closed"
    assert lab.document_urls == [] and lab.source_url is None
    assert (lab.estimated_value, lab.classification_codes) == (None, [])

    ehs = tenders["8156398"]
    assert (ehs.estimated_value, ehs.currency) == (1250000.0, "INR")
    assert ehs.classification_codes == [{"scheme": "CPV", "code": "48000000"}]


async def test_free_text_values_are_not_guessed_into_numbers(ti_settings):
    connector = TenderImpulseConnector(ti_settings)
    for text in ("Refer to tender document", "1-2 million", "N/A", ""):
        tender = connector._normalize({"tender_id": 1, "title": "x", "value_of_contract": text})
        assert tender.estimated_value is None, text


async def test_no_credential_reaches_a_stored_field(ti_settings):
    tenders = await sweep(TenderImpulseConnector(ti_settings, transport=feed([])))
    for tender in tenders:
        blob = tender.model_dump_json()
        assert TOKEN not in blob and KEY not in blob
    # A token some upstream portal put in its own URL is masked too.
    ehs = next(t for t in tenders if t.source_notice_id == "8156398")
    assert "leaked-by-the-portal" not in ehs.model_dump_json()


async def test_the_general_prefilter_switch_does_not_reach_this_source(ti_settings):
    off = ti_settings.model_copy(update={"apply_keyword_prefilter": False})
    assert len(await sweep(TenderImpulseConnector(off, transport=feed([])))) == 3
    unfiltered = ti_settings.model_copy(update={"tender_impulse_store_unfiltered": True})
    assert len(await sweep(TenderImpulseConnector(unfiltered, transport=feed([])))) == 5


# --- availability and the card ---------------------------------------------


def test_each_missing_piece_is_named_with_where_it_goes(settings):
    reason = TenderImpulseConnector(settings).unavailable_reason()
    assert "ACCESS_TOKEN" in reason

    token_only = settings.model_copy(update={"tender_impulse_access_token": TOKEN})
    assert "'Encryption key' box" in TenderImpulseConnector(token_only).unavailable_reason()

    pair = token_only.model_copy(update={"tender_impulse_encryption_key": KEY})
    assert "'Starting id' box" in TenderImpulseConnector(pair).unavailable_reason()

    # Once a bookmark exists the start id is never needed again.
    resumed = pair.model_copy(update={"tender_impulse_cursor": "8156398"})
    assert TenderImpulseConnector(resumed).unavailable_reason() is None


def test_every_card_setup_field_is_settable():
    """A box on the card that PUT refuses would be a box that does nothing."""
    for entry in source_catalog(Settings(_env_file=None, database_url="sqlite://")):
        for field in entry["setup_fields"]:
            assert field["field"] in SETTINGS_SECRETS, field
        if entry["credential_extra_field"]:
            assert entry["credential_extra_field"] in SETTINGS_SECRETS
    assert "tender_impulse" in SOURCE_NAMES


def test_all_three_pieces_stored_from_the_dashboard_make_it_available(db_session):
    assert set_credential(db_session, "tender_impulse", TOKEN)
    assert set_secret(db_session, "tender_impulse_encryption_key", KEY)
    assert set_secret(db_session, "tender_impulse_start_id", str(START))
    resolved = settings_with_stored_credentials(
        db_session, Settings(_env_file=None, database_url="sqlite://")
    )
    assert TenderImpulseConnector(resolved).unavailable_reason() is None


def test_the_sources_endpoint_masks_the_encryption_key(client, db_session):
    secret_key = "a-real-looking-encryption-key-1234"
    set_secret(db_session, "tender_impulse_encryption_key", secret_key)
    set_secret(db_session, "tender_impulse_start_id", str(START))
    body = client.get("/api/sources").text
    assert secret_key not in body
    card = next(s for s in client.get("/api/sources").json() if s["name"] == "tender_impulse")
    assert card["credential_extra_secret"] is True
    assert card["credential_extra_value"] == "…1234"
    assert card["setup_fields"] == [
        {
            "field": "tender_impulse_start_id",
            "label": "Starting id",
            "hint": card["setup_fields"][0]["hint"],
            "placeholder": "8156393",
            "configured": True,
            "value": str(START),
        }
    ]
    # The other pairs still read back in full - their second half is not a secret.
    set_secret(db_session, "spend_network_email", "tenders@example.invalid")
    sn = next(s for s in client.get("/api/sources").json() if s["name"] == "spend_network")
    assert (sn["credential_extra_value"], sn["credential_extra_secret"]) == ("tenders@example.invalid", False)
    assert "tender_impulse_encryption_key" in OPAQUE_SECRETS


def test_the_start_id_can_be_set_through_the_card_endpoint(client, db_session):
    assert (
        client.put("/api/settings/secrets/tender_impulse_start_id", json={"value": "8156393"}).status_code
        == 204
    )
    row = db_session.get(AppSetting, "secret.tender_impulse_start_id")
    assert row is not None and row.value == "8156393"


# --- ingest owns the bookmark ----------------------------------------------


@pytest.fixture
def patched_session(monkeypatch, db_session):
    monkeypatch.setattr(ingest, "SessionLocal", db_session.info["factory"])
    return db_session


async def test_ingest_stores_the_batch_then_moves_the_bookmark(patched_session, ti_settings, monkeypatch):
    calls: list[httpx.Request] = []
    monkeypatch.setattr(
        ingest,
        "build_connector",
        lambda source, s=None, transport=None, **kw: TenderImpulseConnector(s, transport=feed(calls)),
    )
    await ingest.run_fetch(["tender_impulse"], days_back=1, settings=ti_settings)
    run = patched_session.execute(select(FetchRun)).scalar_one()
    assert run.status == "success", run.error_message
    assert run.records_created == 3
    assert cursors.stored(patched_session, "tender_impulse") == "8156398"

    # The next sweep reads the bookmark back through the same overlay, and asks
    # for what comes after it.
    before = len(calls)
    await ingest.run_fetch(["tender_impulse"], days_back=1, settings=ti_settings)
    # The *first* request, and only one: the last request of a sweep from the
    # start id is also lastid=8156398, so asserting on that proved nothing.
    assert [int(c.url.params["lastid"]) for c in calls[before:]] == [8156398]


async def test_a_store_that_fails_never_moves_the_bookmark(patched_session, ti_settings, monkeypatch):
    """The vendor's warning, as a test: move it first, fail, and the batch is gone for good."""
    monkeypatch.setattr(
        ingest,
        "build_connector",
        lambda source, s=None, transport=None, **kw: TenderImpulseConnector(s, transport=feed([])),
    )

    def broken_store(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(ingest, "store_tenders", broken_store)
    await ingest.run_fetch(["tender_impulse"], days_back=1, settings=ti_settings)
    run = patched_session.execute(select(FetchRun)).scalar_one()
    assert run.status == "failed"
    assert cursors.stored(patched_session, "tender_impulse") is None
