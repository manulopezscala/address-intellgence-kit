"""Tests para el agente de limpieza de datos."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.data_cleaning import (
    _classify,
    _parse_agent_json,
    process_batch,
    run_data_cleaning_agent,
)


# ---------------------------------------------------------------------------
# Tests de _classify — sin mock de red
# ---------------------------------------------------------------------------


def test_classify_validated():
    assert _classify(0.90) == "validated"
    assert _classify(0.85) == "validated"


def test_classify_needs_review():
    assert _classify(0.72) == "needs_review"
    assert _classify(0.65) == "needs_review"


def test_classify_failed_low():
    assert _classify(0.50) == "failed"
    assert _classify(0.0) == "failed"


def test_classify_failed_none():
    assert _classify(None) == "failed"


def test_batch_classification():
    """Verifica los tres umbrales de clasificación en un solo test."""
    assert _classify(0.9) == "validated"
    assert _classify(0.7) == "needs_review"
    assert _classify(0.5) == "failed"
    assert _classify(None) == "failed"


# ---------------------------------------------------------------------------
# Tests de _parse_agent_json — sin mock del SDK
# ---------------------------------------------------------------------------


def test_parse_agent_json_validated():
    payload = {
        "status": "validated",
        "address": "Av. Santa Fe 2350, Ciudad Autónoma de Buenos Aires, Buenos Aires",
        "cpa": "C1425BGN",
        "result_similarity": 0.91,
    }
    assert _parse_agent_json(json.dumps(payload)) == payload


def test_parse_agent_json_needs_review():
    payload = {
        "status": "needs_review",
        "address": "Calle Belgrano 500, Córdoba, Córdoba",
        "cpa": "X5000ABC",
        "result_similarity": 0.71,
    }
    assert _parse_agent_json(json.dumps(payload)) == payload


def test_parse_fallback():
    result = _parse_agent_json("No pude procesar.")
    assert result["status"] == "failed"
    assert result["address"] is None
    assert result["cpa"] is None
    assert result["result_similarity"] is None


def test_parse_fallback_empty():
    result = _parse_agent_json("")
    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# Tests del agente single (mock del SDK)
# ---------------------------------------------------------------------------


def _make_result_message(json_str: str, num_turns: int = 2):
    from claude_agent_sdk import ResultMessage

    msg = MagicMock(spec=ResultMessage)
    msg.result = json_str
    msg.num_turns = num_turns
    msg.is_error = False
    return msg


async def _fake_query_validated(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "status": "validated",
                "address": "Av. Corrientes 1234, Ciudad Autónoma de Buenos Aires, Buenos Aires",
                "cpa": "C1043AAB",
                "result_similarity": 0.92,
            }
        )
    )


async def _fake_query_needs_review(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "status": "needs_review",
                "address": "Calle San Martín 456, Rosario, Santa Fe",
                "cpa": "S2000XYZ",
                "result_similarity": 0.72,
            }
        )
    )


async def _fake_query_failed(**kwargs):
    yield _make_result_message(
        json.dumps(
            {
                "status": "failed",
                "address": None,
                "cpa": None,
                "result_similarity": 0.40,
            }
        )
    )


async def _fake_query_no_json(**kwargs):
    yield _make_result_message("No se pudo clasificar la dirección.")


@pytest.mark.asyncio
async def test_data_cleaning_validated():
    with patch("src.agents.data_cleaning.query", side_effect=_fake_query_validated):
        result = await run_data_cleaning_agent("Av. Corrientes 1234, Buenos Aires")

    assert result["status"] == "validated"
    assert result["cpa"] == "C1043AAB"
    assert result["result_similarity"] == 0.92


@pytest.mark.asyncio
async def test_data_cleaning_needs_review():
    with patch("src.agents.data_cleaning.query", side_effect=_fake_query_needs_review):
        result = await run_data_cleaning_agent("San Martín 456, Rosario")

    assert result["status"] == "needs_review"
    assert result["result_similarity"] == 0.72


@pytest.mark.asyncio
async def test_data_cleaning_failed():
    with patch("src.agents.data_cleaning.query", side_effect=_fake_query_failed):
        result = await run_data_cleaning_agent("dirección muy ambigua")

    assert result["status"] == "failed"
    assert result["cpa"] is None


@pytest.mark.asyncio
async def test_data_cleaning_parse_fallback():
    with patch("src.agents.data_cleaning.query", side_effect=_fake_query_no_json):
        result = await run_data_cleaning_agent("texto sin json")

    assert result["status"] == "failed"
    assert result["address"] is None


# ---------------------------------------------------------------------------
# Test de process_batch (mock de la Batch API de Anthropic)
# ---------------------------------------------------------------------------


def _make_batch_result(custom_id: str, address_query: str, succeeded: bool = True):
    """Construye un mock de un resultado individual del batch."""
    result = MagicMock()
    result.custom_id = custom_id

    if succeeded:
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.input = {"address_query": address_query}

        result.result.type = "succeeded"
        result.result.message.content = [tool_use_block]
    else:
        result.result.type = "errored"

    return result


async def _async_results_iter(results):
    """Genera resultados del batch como async iterator."""
    for r in results:
        yield r


@pytest.mark.asyncio
async def test_process_batch_mock():
    """Verifica que process_batch produce el output correcto con Batch API mockeada.

    Usamos 3 addresses con batch-succeeded y similarities >= 0.65 para que la
    tasa de "failed" sea 0% y no se active el re-submit (umbral: > 10%).
    """
    addresses = [
        {"id": 1, "address": "Av. Corrientes 1234, CABA"},
        {"id": 2, "address": "San Martín 456, Rosario, Santa Fe"},
        {"id": 3, "address": "Belgrano 789, Mendoza, Mendoza"},
    ]

    # Las 3 respuestas del batch son exitosas (tool_use incluido)
    fake_batch_results = [
        _make_batch_result("1", "Av. Corrientes 1234, CABA"),
        _make_batch_result("2", "San Martín 456, Rosario, Santa Fe"),
        _make_batch_result("3", "Belgrano 789, Mendoza, Mendoza"),
    ]

    # Resultados simulados de _handle_validate_address — todas con similarity >= 0.65
    fake_ubidata_results = [
        {
            "validated": True,
            "result_similarity": 0.92,  # → "validated"
            "normalized_address": "Av. Corrientes 1234, Ciudad Autónoma de Buenos Aires, Buenos Aires",
            "cpa": "C1043AAB",
        },
        {
            "validated": True,
            "result_similarity": 0.71,  # → "needs_review"
            "normalized_address": "San Martín 456, Rosario, Santa Fe",
            "cpa": "S2000XYZ",
        },
        {
            "validated": True,
            "result_similarity": 0.67,  # → "needs_review"
            "normalized_address": "Belgrano 789, Mendoza, Mendoza",
            "cpa": "M5500ABC",
        },
    ]

    mock_batch = MagicMock()
    mock_batch.id = "batch_test_001"

    mock_status = MagicMock()
    mock_status.processing_status = "ended"

    def mock_results(*args, **kwargs):
        return _async_results_iter(fake_batch_results)

    mock_batches = MagicMock()
    mock_batches.create = AsyncMock(return_value=mock_batch)
    mock_batches.retrieve = AsyncMock(return_value=mock_status)
    mock_batches.results = mock_results

    mock_client = MagicMock()
    mock_client.beta.messages.batches = mock_batches

    with (
        patch(
            "src.agents.data_cleaning.AsyncAnthropic",
            return_value=mock_client,
        ),
        patch(
            "src.agents.data_cleaning._handle_validate_address",
            side_effect=fake_ubidata_results,
        ),
    ):
        result = await process_batch(addresses)

    assert result["total"] == 3
    assert result["validated"] == 1    # similarity 0.92 ≥ 0.85
    assert result["needs_review"] == 2  # similarities 0.71 y 0.67
    assert result["failed"] == 0
    assert result["validated"] + result["needs_review"] + result["failed"] == result["total"]
    assert result["batch_id"] == "batch_test_001"
