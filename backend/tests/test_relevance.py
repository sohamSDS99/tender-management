"""Relevance engine: scoring, disambiguation, caps and explainability."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.relevance import (
    DEP_CLOUD_ALLOWED,
    DEP_CLOUD_REQUIRED,
    DEP_HYBRID,
    DEP_OFFLINE,
    DEP_ON_PREM,
    DEP_UNSPECIFIED,
    FIT_HIGH,
    FIT_NOT,
    FIT_REVIEW,
    FIT_STATUSES,
    RelevanceEngine,
    get_engine,
    load_config,
    normalize,
)

NOW = datetime(2026, 8, 21, 12, 0, 0)
FUTURE = NOW + timedelta(days=30)
PAST = NOW - timedelta(days=5)


@pytest.fixture(scope="module")
def engine(request) -> RelevanceEngine:
    from tests.conftest import CONFIG

    return RelevanceEngine(load_config(CONFIG))


def score(engine, title, description, **kwargs):
    kwargs.setdefault("deadline", FUTURE)
    kwargs.setdefault("now", NOW)
    return engine.score(title=title, description=description, **kwargs)


# --- normalization ---------------------------------------------------------


def test_normalize_folds_accents_and_punctuation():
    assert normalize("Fiche de données de sécurité (SDS)!") == " fiche de donnees de securite sds "
    assert normalize("cloud-based platform") == " cloud based platform "
    assert normalize(None) == " "


def test_phrase_matching_respects_word_boundaries(engine):
    # "tracking cloud" must not match the "g cloud" review pattern.
    result = score(engine, "Corrective action tracking", "Cloud deployment is permitted.")
    assert not any("g cloud" in flag for flag in result.review_flags)


# --- happy path ------------------------------------------------------------


def test_cloud_sds_platform_scores_excellent(engine):
    result = score(
        engine,
        "Cloud platform for authoring, approving and distributing safety data sheets",
        "The authority requires a software as a service platform for SDS authoring, GHS hazard "
        "classification, chemical inventory management and distribution of safety data sheets. "
        "Subscription licensing, API integration and single sign on are required. The solution "
        "must be cloud based.",
        classification_codes=[{"scheme": "CPV", "code": "48000000"}],
    )
    assert result.relevance_score >= 85
    assert result.fit_status == FIT_HIGH
    assert result.deployment_fit == DEP_CLOUD_REQUIRED
    assert result.relevance_category in {"sds_management", "sds_authoring", "sds_distribution"}
    assert result.disqualifiers == []
    assert any("cloud" in reason.lower() for reason in result.relevance_reasons)


def test_multiple_modules_add_credit(engine):
    single = score(engine, "Incident management software", "Cloud hosted incident management software.")
    multi = score(
        engine,
        "Incident management software",
        "Cloud hosted platform covering incident management, inspection management, audit "
        "management and risk assessment.",
    )
    assert multi.relevance_score > single.relevance_score
    assert any("capability areas" in r for r in multi.relevance_reasons)


def test_title_weighs_more_than_description(engine):
    in_title = score(engine, "Safety data sheet management system", "Generic supporting text.")
    in_body = score(engine, "Generic supporting text", "Safety data sheet management system.")
    assert in_title.topic_relevance_score >= in_body.topic_relevance_score


def test_classification_codes_are_signals_not_filters(engine):
    with_code = score(
        engine,
        "EHS management system",
        "Cloud hosted EHS management system.",
        classification_codes=[{"scheme": "CPV", "code": "48000000"}],
    )
    without = score(engine, "EHS management system", "Cloud hosted EHS management system.")
    assert with_code.relevance_score >= without.relevance_score
    assert any("48000000" in r for r in with_code.relevance_reasons)
    # A code on its own never produces a relevant score.
    code_only = score(
        engine,
        "Supply of office furniture",
        "Desks and chairs.",
        classification_codes=[{"scheme": "CPV", "code": "48000000"}],
    )
    assert code_only.relevance_score < 50


def test_multilingual_terms_are_recognised(engine):
    for title, body in (
        ("Fourniture d'un logiciel HSE", "Logiciel HSE en mode SaaS pour la gestion des incidents."),
        (
            "Sicherheitsdatenblatt Management",
            "Cloud Software fuer Gefahrstoffmanagement und Sicherheitsdatenblatt.",
        ),
        ("Plataforma em nuvem para gestao de fichas de dados de seguranca", "Software como servico."),
        ("Software de seguridad y salud en el trabajo", "Plataforma en la nube para gestion de incidentes."),
    ):
        result = score(engine, title, body)
        assert result.relevance_category is not None, title
        assert result.relevance_score >= 40, (title, result.relevance_score)


# --- SDS disambiguation ----------------------------------------------------


def test_software_defined_storage_is_disqualified(engine):
    result = score(
        engine,
        "Software defined storage (SDS) capacity expansion",
        "Procurement of software defined storage for the data centre including SDS management "
        "console licences and migration services.",
        classification_codes=[{"scheme": "CPV", "code": "48000000"}],
    )
    assert result.relevance_score <= 20
    assert result.fit_status == FIT_NOT
    assert any("unrelated sense" in d for d in result.disqualifiers)


def test_bare_sds_without_context_is_flagged_not_scored(engine):
    no_context = score(engine, "SDS management tool", "The SDS management tool must support tagging.")
    with_context = score(
        engine,
        "SDS management tool",
        "The SDS management tool stores safety data sheets for every hazardous chemical and "
        "supports GHS labelling.",
    )
    assert with_context.topic_relevance_score > no_context.topic_relevance_score
    assert any("abbreviation 'SDS'" in f for f in no_context.review_flags)


def test_reach_as_plain_english_verb_does_not_score(engine):
    verb = score(engine, "Community outreach programme", "The project aims to reach 10,000 households.")
    assert verb.relevance_score < 25
    regulation = score(
        engine,
        "REACH compliance services for chemical substances",
        "Support for REACH registration of chemical substances and safety data sheet authoring.",
    )
    assert regulation.relevance_score > verb.relevance_score


# --- deployment fit --------------------------------------------------------


def test_mandatory_on_premises_caps_score_at_20(engine):
    result = score(
        engine,
        "SaaS EHS incident management platform with SDS management",
        "The authority requires an EHS incident management system with safety data sheet "
        "management, chemical inventory, audit management and inspection management. "
        "All application components must reside on the buyer's network and the software must "
        "be installed on customer servers.",
        classification_codes=[{"scheme": "CPV", "code": "48000000"}],
    )
    assert result.relevance_score <= 20
    assert result.fit_status == FIT_NOT
    assert result.deployment_fit == DEP_ON_PREM
    assert result.disqualifiers
    # Keyword matches must not override the cap.
    assert result.topic_relevance_score >= 70


def test_air_gapped_is_disqualified(engine):
    result = score(
        engine,
        "Chemical inventory and safety data sheet management system",
        "The system will operate in an air gapped environment with no internet connectivity allowed.",
    )
    assert result.deployment_fit == DEP_OFFLINE
    assert result.relevance_score <= 20
    assert result.fit_status == FIT_NOT


def test_hybrid_deployment_goes_to_manual_review(engine):
    result = score(
        engine,
        "Web based inspection and audit management solution",
        "The council seeks a hosted solution for inspection management, audit management and "
        "incident management with corrective action tracking. Cloud deployment is required. "
        "An optional locally hosted deployment may also be proposed.",
        classification_codes=[{"scheme": "CPV", "code": "72000000"}],
    )
    assert result.deployment_fit == DEP_HYBRID
    assert result.fit_status == FIT_REVIEW
    assert result.disqualifiers == []
    assert any("cloud-only" in f for f in result.review_flags)


def test_private_cloud_creates_review_flag_not_rejection(engine):
    result = score(
        engine,
        "Chemical compliance and safety data sheet management platform",
        "The platform shall be operated in a government cloud provided by the buyer. "
        "Implementation, configuration and annual support are included, with API integration.",
    )
    assert result.deployment_fit != DEP_ON_PREM
    assert any("Hosting model needs review" in f for f in result.review_flags)
    assert result.relevance_score >= 50


def test_unspecified_deployment_is_not_penalised(engine):
    unspecified = score(
        engine, "EHS management system software", "Incident management and audit management modules."
    )
    assert unspecified.deployment_fit == DEP_UNSPECIFIED
    assert unspecified.disqualifiers == []
    allowed = score(
        engine,
        "EHS management system software",
        "Incident management and audit management modules. Cloud hosting is permitted.",
    )
    assert allowed.deployment_fit == DEP_CLOUD_ALLOWED
    assert allowed.relevance_score >= unspecified.relevance_score


# --- false positives -------------------------------------------------------


def test_chemical_purchase_requiring_sds_documents_caps_at_15(engine):
    result = score(
        engine,
        "Supply of laboratory chemicals and reagents",
        "The supplier must provide an SDS for every chemical delivered and safety data sheets "
        "shall be provided with each delivery.",
    )
    assert result.relevance_score <= 15
    assert result.fit_status == FIT_NOT
    assert any("only a required delivery document" in d for d in result.disqualifiers)


def test_ehs_training_without_software_caps_at_35(engine):
    result = score(
        engine,
        "Health and safety training and consultancy services",
        "Provision of health and safety training services and appointment of a safety advisor "
        "for construction site safety supervision.",
    )
    assert result.relevance_score <= 35
    assert any("consultancy, training or staffing" in d for d in result.disqualifiers)


def test_ppe_purchase_caps_at_10(engine):
    result = score(
        engine,
        "Supply of personal protective equipment",
        "Purchase of safety boots, protective clothing and safety helmets, plus safety signage.",
    )
    assert result.relevance_score <= 10
    assert any("Physical safety equipment" in d for d in result.disqualifiers)


def test_consultancy_that_implements_software_stays_relevant(engine):
    result = score(
        engine,
        "Consultancy for implementation and configuration of an EHS software platform",
        "Consulting services for the implementation, configuration and support of an EHS "
        "software platform with chemical management and incident management modules, hosted "
        "by the vendor as a managed cloud service.",
    )
    assert result.relevance_score >= 60
    assert result.disqualifiers == []
    assert result.fit_status != FIT_NOT


# --- actionability ---------------------------------------------------------


def test_expired_deadline_is_penalised_but_keeps_topic_score(engine):
    kwargs = dict(
        title="Cloud EHS incident management platform",
        description="Cloud hosted EHS incident management platform with near miss reporting, "
        "inspection management and audit management.",
    )
    live = score(engine, deadline=FUTURE, **kwargs)
    expired = score(engine, deadline=PAST, **kwargs)
    assert expired.relevance_score < live.relevance_score
    assert expired.topic_relevance_score == live.topic_relevance_score
    assert expired.is_actionable is False
    assert any("deadline passed" in f.lower() for f in expired.review_flags)


def test_cancelled_notice_is_not_actionable(engine):
    result = score(
        engine,
        "SaaS safety data sheet management system",
        "Cloud hosted SDS management for chemical inventory.",
        status="cancelled",
    )
    assert result.is_actionable is False
    assert any("not actionable" in f for f in result.review_flags)


# --- contract --------------------------------------------------------------


def test_result_shape_and_bands(engine):
    result = score(engine, "Irrelevant road resurfacing works", "Asphalt laying and kerb replacement.")
    payload = result.as_dict()
    assert set(payload) == {
        "relevance_score",
        "relevance_category",
        "fit_status",
        "deployment_fit",
        "relevance_reasons",
        "disqualifiers",
        "review_flags",
        "topic_relevance_score",
        "product_fit_score",
        "procurement_intent_score",
        "is_actionable",
    }
    assert payload["fit_status"] in FIT_STATUSES
    assert 0 <= payload["relevance_score"] <= 100
    assert payload["relevance_reasons"]
    assert engine.band_for(90) == "excellent"
    assert engine.band_for(75) == "good"
    assert engine.band_for(55) == "possible"
    assert engine.band_for(30) == "weak"
    assert engine.band_for(5) == "irrelevant"


def test_engine_is_deterministic(engine):
    args = ("SaaS SDS authoring platform", "Cloud based safety data sheet authoring with GHS labels.")
    first = score(engine, *args)
    second = score(engine, *args)
    assert first.as_dict() == second.as_dict()


def test_profiles_cover_the_required_set():
    engine = get_engine(None)
    keys = {p["key"] for p in engine.profile_metadata()}
    assert keys == {
        "sds_management",
        "sds_authoring",
        "sds_distribution",
        "chemical_compliance",
        "ehs_platform",
        "incident_management",
        "inspection_management",
        "audit_management",
    }
