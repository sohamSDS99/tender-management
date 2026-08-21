"""Connector registry: the single place that knows every source."""

from __future__ import annotations

import httpx

from app.connectors.austender import AusTenderConnector
from app.connectors.base import ConnectorError, NormalizedTender, TenderConnector
from app.connectors.canada_buys import CanadaBuysConnector
from app.connectors.contracts_finder import ContractsFinderConnector
from app.connectors.find_a_tender import FindATenderConnector
from app.connectors.pncp import PncpConnector
from app.connectors.sam import SamGovConnector
from app.connectors.ted import TedConnector
from app.connectors.world_bank import WorldBankConnector
from app.settings import Settings, get_settings

CONNECTOR_CLASSES: tuple[type[TenderConnector], ...] = (
    TedConnector,
    SamGovConnector,
    FindATenderConnector,
    ContractsFinderConnector,
    WorldBankConnector,
    CanadaBuysConnector,
    AusTenderConnector,
    PncpConnector,
)

SOURCE_NAMES: tuple[str, ...] = tuple(c.source_name for c in CONNECTOR_CLASSES)


def build_connector(
    source_name: str,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TenderConnector:
    settings = settings or get_settings()
    for cls in CONNECTOR_CLASSES:
        if cls.source_name == source_name:
            return cls(settings, transport=transport)
    raise KeyError(f"unknown source '{source_name}'")


def build_all(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> list[TenderConnector]:
    settings = settings or get_settings()
    return [cls(settings, transport=transport) for cls in CONNECTOR_CLASSES]


def enabled_sources(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    out = []
    for connector in build_all(settings):
        if connector.enabled and connector.unavailable_reason() is None:
            out.append(connector.source_name)
    return out


def source_catalog(settings: Settings | None = None) -> list[dict[str, object]]:
    settings = settings or get_settings()
    catalog: list[dict[str, object]] = []
    for connector in build_all(settings):
        catalog.append(
            {
                "name": connector.source_name,
                "display_name": connector.display_name,
                "homepage": connector.homepage,
                "enabled": connector.enabled,
                "requires_api_key": connector.requires_api_key,
                "unavailable_reason": connector.unavailable_reason(),
                "keyword_prefiltered": connector.prefilter,
                "notes": connector.notes,
            }
        )
    return catalog


__all__ = [
    "CONNECTOR_CLASSES",
    "SOURCE_NAMES",
    "ConnectorError",
    "NormalizedTender",
    "TenderConnector",
    "build_all",
    "build_connector",
    "enabled_sources",
    "source_catalog",
]
