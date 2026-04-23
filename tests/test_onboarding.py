"""Tests básicos para el agente de onboarding."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.onboarding import _parse_agent_json, run_onboarding_agent
from src.tools.ubidata import _handle_check_coverage


# --- Tests sin HTTP ni SDK ---


def test_parse_agent_json_valid():
    payload = {
        "status": "validated",
        "validated_address": "Av. Corrientes 1234, CABA, Buenos Aires",
        "cpa": "C1043AAB",
        "risk_level": "low",
    }
    assert _parse_agent_json(json.dumps(payload)) == payload


def test_parse_agent_json_embedded_in_text():
    text = 'La dirección fue validada.\n{"status": "validated", "validated_address": "X", "cpa": "C1", "risk_level": "low"}\nFin.'
    result = _parse_agent_json(text)
    assert result["status"] == "validated"
    assert result["cpa"] == "C1"


def test_parse_agent_json_fallback_on_no_json():
    result = _parse_agent_json("No pude procesar la dirección.")
    assert result["status"] == "escalated"
    assert result["validated_address"] is None
    assert result["risk_level"] == "blocked"


def test_parse_agent_json_empty():
    result = _parse_agent_json("")
    assert result["status"] == "escalated"


def test_check_coverage_caba_no_http():
    result = asyncio.get_event_loop().run_until_complete(
        _handle_check_coverage("C1043AAB")
    )
    assert result["covered"] is True
    assert result["zone"] == "CABA"
    assert result["cpa"] == "C1043AAB"


def test_check_coverage_outside_no_http():
    result = asyncio.get_event_loop().run_until_complete(
        _handle_check_coverage("X9000AAA")
    )
    assert result["covered"] is False


# --- Tests con mock del SDK ---


def _make_result_message(json_str: str, num_turns: int = 2):
    from claude_agent_sdk import ResultMessage

    msg = MagicMock(spec=ResultMessage)
    msg.result = json_str
    msg.num_turns = num_turns
    msg.is_error = False
    return msg


async def _fake_query_validated(**kwargs):
    yield _make_result_message(
        '{"status": "validated", "validated_address": "Av. Corrientes 1234, CABA, Buenos Aires", "cpa": "C1043AAB", "risk_level": "low"}',
        num_turns=2,
    )


async def _fake_query_escalated(**kwargs):
    yield _make_result_message(
        '{"status": "escalated", "validated_address": null, "cpa": null, "risk_level": "blocked"}',
        num_turns=1,
    )


async def _fake_query_no_json(**kwargs):
    yield _make_result_message("No se pudo validar la dirección.", num_turns=1)


@pytest.mark.asyncio
async def test_onboarding_validated_happy_path():
    with patch("src.agents.onboarding.query", side_effect=_fake_query_validated):
        result = await run_onboarding_agent("Av. Corrientes 1234, Buenos Aires")

    assert result["status"] == "validated"
    assert result["cpa"] == "C1043AAB"
    assert result["risk_level"] == "low"
    assert result["validated_address"] is not None
    assert result["conversation_turns"] == 2


@pytest.mark.asyncio
async def test_onboarding_escalated_when_blocked():
    with patch("src.agents.onboarding.query", side_effect=_fake_query_escalated):
        result = await run_onboarding_agent("Belgrano, Capital")

    assert result["status"] == "escalated"
    assert result["cpa"] is None


@pytest.mark.asyncio
async def test_onboarding_parse_failure_falls_back():
    with patch("src.agents.onboarding.query", side_effect=_fake_query_no_json):
        result = await run_onboarding_agent("dirección inválida")

    assert result["status"] == "escalated"
    assert result["risk_level"] == "blocked"
