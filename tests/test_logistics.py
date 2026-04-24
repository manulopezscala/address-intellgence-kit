"""Tests para el agente de logística."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agents.logistics import _parse_agent_json, run_logistics_agent


# ---------------------------------------------------------------------------
# Tests de _parse_agent_json — sin mock del SDK
# ---------------------------------------------------------------------------


def test_parse_agent_json_approved():
    payload = {
        "decision": "approved",
        "address": "Av. Corrientes 1234, Ciudad Autónoma de Buenos Aires, Buenos Aires",
        "cpa": "C1043AAB",
        "risk_level": "low",
        "covered": True,
        "result_similarity": 0.92,
        "rejection_reason": None,
    }
    assert _parse_agent_json(json.dumps(payload)) == payload


def test_parse_agent_json_rejected():
    payload = {
        "decision": "rejected",
        "address": "Calle Inventada 999, Lugar Inventado",
        "cpa": None,
        "risk_level": "high",
        "covered": None,
        "result_similarity": 0.45,
        "rejection_reason": "Similarity demasiado baja para garantizar despacho seguro.",
    }
    assert _parse_agent_json(json.dumps(payload)) == payload


def test_parse_agent_json_embedded_in_text():
    text = (
        "Análisis completado.\n"
        '{"decision": "approved", "address": "Av. Santa Fe 2350, CABA, Buenos Aires", '
        '"cpa": "C1425BGN", "risk_level": "low", "covered": true, '
        '"result_similarity": 0.91, "rejection_reason": null}\n'
        "Listo."
    )
    result = _parse_agent_json(text)
    assert result["decision"] == "approved"
    assert result["cpa"] == "C1425BGN"
    assert result["rejection_reason"] is None


def test_parse_agent_json_fallback():
    result = _parse_agent_json("No pude determinar la decisión.")
    assert result["decision"] == "blocked"
    assert result["cpa"] is None
    assert result["rejection_reason"] is not None


def test_parse_agent_json_empty():
    result = _parse_agent_json("")
    assert result["decision"] == "blocked"
    assert result["result_similarity"] is None


def test_parse_agent_json_preserves_original_address():
    result = _parse_agent_json("", original_address="Belgrano 100, CABA")
    assert result["address"] == "Belgrano 100, CABA"


# ---------------------------------------------------------------------------
# Tests con mock del SDK
# ---------------------------------------------------------------------------


def _make_result_message(json_str: str, num_turns: int = 2):
    from claude_agent_sdk import ResultMessage

    msg = MagicMock(spec=ResultMessage)
    msg.result = json_str
    msg.num_turns = num_turns
    msg.is_error = False
    return msg


async def _fake_query_approved(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "decision": "approved",
                "address": "Av. Corrientes 1234, Ciudad Autónoma de Buenos Aires, Buenos Aires",
                "cpa": "C1043AAB",
                "risk_level": "low",
                "covered": True,
                "result_similarity": 0.92,
                "rejection_reason": None,
            }
        ),
        num_turns=2,
    )


async def _fake_query_rejected(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "decision": "rejected",
                "address": "Calle Sin Salida 0, Lugar Inventado",
                "cpa": None,
                "risk_level": "high",
                "covered": None,
                "result_similarity": 0.45,
                "rejection_reason": "Similarity inferior al umbral mínimo de despacho.",
            }
        ),
        num_turns=2,
    )


async def _fake_query_blocked(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "decision": "blocked",
                "address": "Dirección ambigua sin datos suficientes",
                "cpa": None,
                "risk_level": "blocked",
                "covered": None,
                "result_similarity": None,
                "rejection_reason": "No se encontraron resultados en Ubidata tras reintentos.",
            }
        ),
        num_turns=3,
    )


async def _fake_query_no_json(**kwargs):
    yield _make_result_message("No pude procesar la solicitud.", num_turns=1)


@pytest.mark.asyncio
async def test_logistics_approved():
    with patch("src.agents.logistics.query", side_effect=_fake_query_approved):
        result = await run_logistics_agent("Av. Corrientes 1234, Buenos Aires")

    assert result["decision"] == "approved"
    assert result["covered"] is True
    assert result["cpa"] == "C1043AAB"
    assert result["result_similarity"] == 0.92
    assert result["rejection_reason"] is None


@pytest.mark.asyncio
async def test_logistics_rejected():
    with patch("src.agents.logistics.query", side_effect=_fake_query_rejected):
        result = await run_logistics_agent("Calle Sin Salida 0, Lugar Inventado")

    assert result["decision"] == "rejected"
    assert result["covered"] is None
    assert result["cpa"] is None
    assert result["rejection_reason"] is not None


@pytest.mark.asyncio
async def test_logistics_blocked():
    with patch("src.agents.logistics.query", side_effect=_fake_query_blocked):
        result = await run_logistics_agent("Dirección ambigua sin datos suficientes")

    assert result["decision"] == "blocked"
    assert result["risk_level"] == "blocked"


@pytest.mark.asyncio
async def test_logistics_parse_failure_falls_back():
    with patch("src.agents.logistics.query", side_effect=_fake_query_no_json):
        result = await run_logistics_agent("texto inválido")

    assert result["decision"] == "blocked"
    assert result["cpa"] is None
