"""Brazil PNCP (Portal Nacional de Contratacoes Publicas) - Consulta API v1.

Docs: https://pncp.gov.br/api/consulta/swagger-ui/index.html
Endpoints used (all documented):
  /v1/contratacoes/atualizacao  - contratacoes updated in a date window
  /v1/contratacoes/proposta     - contratacoes currently receiving proposals
Portuguese source text is preserved verbatim.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.connectors.base import (
    STAGE_TENDER,
    ConnectorError,
    NormalizedTender,
    TenderConnector,
    parse_datetime,
)

BASE_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes"
UPDATED_URL = f"{BASE_URL}/atualizacao"
PROPOSAL_URL = f"{BASE_URL}/proposta"
NOTICE_URL = "https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}"
PAGE_SIZE = 50  # API maximum


class PncpConnector(TenderConnector):
    source_name = "pncp"
    display_name = "Brazil PNCP"
    homepage = "https://pncp.gov.br"
    prefilter = True
    notes = (
        "Documented atualizacao endpoint per modalidade (PNCP_MODALIDADES) plus the proposta "
        "endpoint for procurements still accepting proposals. PNCP has no server-side keyword "
        "search, so results are prefiltered locally and capped at PNCP_MAX_PAGES pages of 50 "
        "records per query - raise it for fuller coverage of the Brazilian feed."
    )

    async def fetch(self, date_from: datetime, date_to: datetime) -> list[NormalizedTender]:
        date_from, date_to = self.clamp_window(date_from, date_to)
        max_pages = max(1, self.settings.pncp_max_pages)
        out: list[NormalizedTender] = []
        seen: set[str] = set()
        errors: list[ConnectorError] = []
        # PNCP is comparatively slow; give it a longer read timeout.
        async with self.client(timeout=max(60, self.settings.request_timeout_seconds)) as client:
            for modalidade in self.settings.pncp_modalidade_codes:
                queries = [
                    (
                        UPDATED_URL,
                        {
                            "dataInicial": date_from.strftime("%Y%m%d"),
                            "dataFinal": date_to.strftime("%Y%m%d"),
                            "codigoModalidadeContratacao": modalidade,
                        },
                    ),
                    (
                        PROPOSAL_URL,
                        {
                            "dataFinal": (date_to + timedelta(days=60)).strftime("%Y%m%d"),
                            "codigoModalidadeContratacao": modalidade,
                        },
                    ),
                ]
                for url, base_params in queries:
                    for page in range(max_pages):
                        params = {**base_params, "pagina": page + 1, "tamanhoPagina": PAGE_SIZE}
                        try:
                            data = await self.request(client, "GET", url, params=params, expect="json")
                        except ConnectorError as exc:
                            # One slow modalidade must not discard the others.
                            errors.append(exc)
                            self.log_progress(endpoint=url.rsplit("/", 1)[-1], error=exc.message)
                            break
                        records = (data or {}).get("data") or []
                        self.log_progress(
                            endpoint=url.rsplit("/", 1)[-1],
                            modalidade=modalidade,
                            page=page + 1,
                            received=len(records),
                            total=(data or {}).get("totalRegistros"),
                        )
                        for raw in records:
                            try:
                                tender = self._normalize(raw)
                            except Exception:
                                continue
                            if not tender or tender.source_notice_id in seen:
                                continue
                            if not self.keep(tender.title, tender.description, tender.buyer_name):
                                continue
                            seen.add(tender.source_notice_id)
                            out.append(tender)
                        if not records or (page + 1) >= ((data or {}).get("totalPaginas") or 1):
                            break
        if errors and not out:
            raise errors[0]
        return out

    def _normalize(self, raw: dict[str, Any]) -> NormalizedTender | None:
        control_number = raw.get("numeroControlePNCP")
        if not control_number:
            return None
        orgao = raw.get("orgaoEntidade") or {}
        unidade = raw.get("unidadeOrgao") or {}
        published, published_tz = parse_datetime(raw.get("dataPublicacaoPncp") or raw.get("dataInclusao"))
        deadline, deadline_tz = parse_datetime(raw.get("dataEncerramentoProposta"))
        updated, _ = parse_datetime(raw.get("dataAtualizacao") or raw.get("dataAtualizacaoGlobal"))
        value = raw.get("valorTotalEstimado")
        try:
            value = float(value) if value not in (None, "", 0, 0.0) else None
        except (TypeError, ValueError):
            value = None
        location = ", ".join(str(p) for p in (unidade.get("municipioNome"), unidade.get("ufSigla")) if p)
        cnpj = orgao.get("cnpj")
        source_url = raw.get("linkSistemaOrigem")
        if cnpj and raw.get("anoCompra") and raw.get("sequencialCompra"):
            source_url = NOTICE_URL.format(
                cnpj=cnpj, ano=raw["anoCompra"], sequencial=raw["sequencialCompra"]
            )
        documents = [u for u in (raw.get("linkSistemaOrigem"), raw.get("linkProcessoEletronico")) if u]
        objeto = raw.get("objetoCompra") or ""
        return NormalizedTender(
            source=self.source_name,
            source_notice_id=str(control_number),
            source_url=source_url,
            reference_number=str(raw.get("processo") or raw.get("numeroCompra") or control_number),
            title=objeto[:400] or str(control_number),
            description=" | ".join(
                str(p)
                for p in (
                    objeto,
                    raw.get("informacaoComplementar"),
                    (raw.get("amparoLegal") or {}).get("descricao"),
                    raw.get("modalidadeNome"),
                )
                if p
            )[:20000]
            or None,
            buyer_name=orgao.get("razaoSocial") or unidade.get("nomeUnidade"),
            buyer_country="BR",
            delivery_location=location or unidade.get("ufNome"),
            publication_date=published,
            deadline=deadline,
            source_updated_at=updated or published,
            source_timezone=deadline_tz or published_tz,
            status=raw.get("situacaoCompraNome"),
            procurement_stage=STAGE_TENDER,
            notice_type=raw.get("modalidadeNome") or raw.get("tipoInstrumentoConvocatorioNome"),
            estimated_value=value,
            currency="BRL",
            classification_codes=(
                [{"scheme": "PNCP-MODALIDADE", "code": str(raw["modalidadeId"])}]
                if raw.get("modalidadeId")
                else []
            ),
            document_urls=documents,
            language="pt",
            raw_payload=raw,
        )
