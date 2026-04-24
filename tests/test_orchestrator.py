"""Tests para el agente orquestador."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agents.orchestrator import _parse_orchestrator_json, run_orchestrator


# ---------------------------------------------------------------------------
# Tests de _parse_orchestrator_json — sin mock del SDK
# ---------------------------------------------------------------------------


def test_parse_orchestrator_json_valid():
    payload = {
        "agents_invoked": ["onboarding"],
        "parallel": False,
        "results": {
            "onboarding": {
                "status": "validated",
                "validated_address": "Av. Corrientes 1234, CABA, Buenos Aires",
                "cpa": "C1043AAB",
                "risk_level": "low",
            }
        },
        "routing_reasoning": "Request de alta de nuevo cliente.",
    }
    assert _parse_orchestrator_json(json.dumps(payload)) == payload


def test_parse_orchestrator_json_parallel():
    payload = {
        "agents_invoked": ["onboarding", "logistics"],
        "parallel": True,
        "results": {
            "onboarding": {"status": "validated"},
            "logistics": {"decision": "approved"},
        },
        "routing_reasoning": "Alta y verificación de despacho simultáneos.",
    }
    result = _parse_orchestrator_json(json.dumps(payload))
    assert result["parallel"] is True
    assert len(result["agents_invoked"]) == 2


def test_parse_orchestrator_json_embedded_in_text():
    text = (
        "Análisis completado.\n"
        '{"agents_invoked": ["support"], "parallel": false, '
        '"results": {"support": {"status": "confirmed"}}, '
        '"routing_reasoning": "Cambio de domicilio de cliente existente."}\n'
    )
    result = _parse_orchestrator_json(text)
    assert result["agents_invoked"] == ["support"]
    assert result["parallel"] is False


def test_parse_orchestrator_json_fallback_on_empty():
    result = _parse_orchestrator_json("")
    assert result["agents_invoked"] == []
    assert result["parallel"] is False
    assert result["results"] == {}
    assert "routing_reasoning" in result


def test_parse_orchestrator_json_fallback_on_no_json():
    result = _parse_orchestrator_json("No pude determinar el agente adecuado.")
    assert result["agents_invoked"] == []
    assert result["results"] == {}


# ---------------------------------------------------------------------------
# Tests con mock del SDK — routing
# ---------------------------------------------------------------------------


def _make_result_message(json_str: str, num_turns: int = 3):
    from claude_agent_sdk import ResultMessage

    msg = MagicMock(spec=ResultMessage)
    msg.result = json_str
    msg.num_turns = num_turns
    msg.is_error = False
    return msg


async def _fake_query_onboarding(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "agents_invoked": ["onboarding"],
                "parallel": False,
                "results": {
                    "onboarding": {
                        "status": "validated",
                        "validated_address": "Av. Corrientes 1234, Ciudad Autónoma de Buenos Aires, Buenos Aires",
                        "cpa": "C1043AAB",
                        "risk_level": "low",
                    }
                },
                "routing_reasoning": "El request menciona registro de nuevo cliente.",
            }
        )
    )


async def _fake_query_support(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "agents_invoked": ["support"],
                "parallel": False,
                "results": {
                    "support": {
                        "status": "confirmed",
                        "new_address": "Av. Santa Fe 2350, CABA, Buenos Aires",
                        "cpa": "C1425BGN",
                        "covered": True,
                        "message": "Cambio de domicilio confirmado.",
                    }
                },
                "routing_reasoning": "El request menciona cambio de domicilio.",
            }
        )
    )


async def _fake_query_parallel(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "agents_invoked": ["onboarding", "logistics"],
                "parallel": True,
                "results": {
                    "onboarding": {
                        "status": "validated",
                        "validated_address": "Belgrano 500, Rosario, Santa Fe",
                        "cpa": "S2000XYZ",
                        "risk_level": "low",
                    },
                    "logistics": {
                        "decision": "approved",
                        "address": "Belgrano 500, Rosario, Santa Fe",
                        "cpa": "S2000XYZ",
                        "risk_level": "low",
                        "covered": True,
                        "result_similarity": 0.91,
                        "rejection_reason": None,
                    },
                },
                "routing_reasoning": "Alta de cliente con verificación de despacho simultánea.",
            }
        )
    )


async def _fake_query_no_json(**kwargs):
    yield _make_result_message("No pude determinar el agente.", num_turns=1)


@pytest.mark.asyncio
async def test_routing_onboarding():
    """Requests de alta enrutan al agente onboarding."""
    with patch("src.agents.orchestrator.query", side_effect=_fake_query_onboarding):
        result = await run_orchestrator("Quiero registrar mi dirección para darme de alta.")

    assert result["agents_invoked"] == ["onboarding"]
    assert result["parallel"] is False
    assert "onboarding" in result["results"]
    assert result["results"]["onboarding"]["status"] == "validated"


@pytest.mark.asyncio
async def test_routing_support():
    """Requests de cambio de domicilio enrutan al agente support."""
    with patch("src.agents.orchestrator.query", side_effect=_fake_query_support):
        result = await run_orchestrator("Necesito cambiar mi domicilio registrado.")

    assert result["agents_invoked"] == ["support"]
    assert result["parallel"] is False
    assert "support" in result["results"]
    assert result["results"]["support"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_parallel_invocation():
    """Requests que combinan alta + despacho disparan dos agentes en paralelo."""
    with patch("src.agents.orchestrator.query", side_effect=_fake_query_parallel):
        result = await run_orchestrator(
            "Quiero registrarme y también verificar si llegan envíos a Belgrano 500, Rosario."
        )

    assert result["parallel"] is True
    assert set(result["agents_invoked"]) == {"onboarding", "logistics"}
    assert result["results"]["onboarding"]["status"] == "validated"
    assert result["results"]["logistics"]["decision"] == "approved"


@pytest.mark.asyncio
async def test_orchestrator_fallback_on_no_json():
    """Si el orquestador no produce JSON, devuelve el fallback."""
    with patch("src.agents.orchestrator.query", side_effect=_fake_query_no_json):
        result = await run_orchestrator("solicitud ambigua")

    assert result["agents_invoked"] == []
    assert result["results"] == {}
