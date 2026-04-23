"""Tests básicos para el agente de soporte."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agents.support import _parse_agent_json, run_support_agent


# --- Tests de _parse_agent_json ---


def test_parse_confirmed():
    payload = {
        "status": "confirmed",
        "new_address": "Av. Santa Fe 2350, CABA, Buenos Aires",
        "cpa": "C1425BGN",
        "covered": True,
        "message": "Cambio de dirección confirmado.",
    }
    assert _parse_agent_json(json.dumps(payload)) == payload


def test_parse_no_coverage():
    payload = {
        "status": "no_coverage",
        "new_address": "Calle 50 N° 123, La Plata, Buenos Aires",
        "cpa": "B1900TFH",
        "covered": False,
        "message": "Esa zona no tiene cobertura de despacho.",
    }
    assert _parse_agent_json(json.dumps(payload)) == payload


def test_parse_fallback():
    result = _parse_agent_json("", default_status="escalated")
    assert result["status"] == "escalated"
    assert result["covered"] is None
    assert "message" in result


# --- Tests con mock del SDK ---


def _make_result_message(json_str: str):
    from claude_agent_sdk import ResultMessage

    msg = MagicMock(spec=ResultMessage)
    msg.result = json_str
    msg.num_turns = 3
    msg.is_error = False
    return msg


async def _fake_query_confirmed(**kwargs):
    yield _make_result_message(
        '{"status": "confirmed", "new_address": "Av. Santa Fe 2350, CABA, Buenos Aires", "cpa": "C1425BGN", "covered": true, "message": "Cambio confirmado."}'
    )


async def _fake_query_no_coverage(**kwargs):
    yield _make_result_message(
        '{"status": "no_coverage", "new_address": "Calle 50 N° 123, La Plata, Buenos Aires", "cpa": "B1900TFH", "covered": false, "message": "Sin cobertura en esa zona."}'
    )


async def _fake_query_invalid(**kwargs):
    yield _make_result_message(
        '{"status": "invalid_address", "new_address": null, "cpa": null, "covered": null, "message": "La dirección no pudo resolverse."}'
    )


async def _fake_query_escalated(**kwargs):
    yield _make_result_message(
        '{"status": "escalated", "new_address": null, "cpa": null, "covered": null, "message": "Requiere revisión manual."}'
    )


@pytest.mark.asyncio
async def test_support_confirmed():
    with patch("src.agents.support.query", side_effect=_fake_query_confirmed):
        result = await run_support_agent("Av. Santa Fe 2350, Buenos Aires")

    assert result["status"] == "confirmed"
    assert result["covered"] is True
    assert result["cpa"] == "C1425BGN"


@pytest.mark.asyncio
async def test_support_no_coverage():
    with patch("src.agents.support.query", side_effect=_fake_query_no_coverage):
        result = await run_support_agent("Calle 50 123, La Plata, Buenos Aires")

    assert result["status"] == "no_coverage"
    assert result["covered"] is False
    assert "cobertura" in result["message"].lower()


@pytest.mark.asyncio
async def test_support_invalid_address():
    with patch("src.agents.support.query", side_effect=_fake_query_invalid):
        result = await run_support_agent("xyzzy 999, lugar inventado")

    assert result["status"] == "invalid_address"
    assert result["new_address"] is None


@pytest.mark.asyncio
async def test_support_escalated():
    with patch("src.agents.support.query", side_effect=_fake_query_escalated):
        result = await run_support_agent("Dirección con alta ambigüedad")

    assert result["status"] == "escalated"


@pytest.mark.asyncio
async def test_support_with_custom_coverage_zones():
    """coverage_zones se inyecta en el prompt sin romper el flujo."""

    async def fake_with_zones(**kwargs):
        prompt = kwargs.get("prompt", "")
        assert "B1900" in prompt
        yield _make_result_message(
            '{"status": "confirmed", "new_address": "Calle 7 N° 1000, La Plata, Buenos Aires", "cpa": "B1900TFH", "covered": true, "message": "Confirmado."}'
        )

    with patch("src.agents.support.query", side_effect=fake_with_zones):
        result = await run_support_agent(
            "Calle 7 1000, La Plata, Buenos Aires",
            coverage_zones=["B1900"],
        )

    assert result["status"] == "confirmed"
