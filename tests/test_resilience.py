"""Tests de resiliencia: clasificación de errores, hooks, trimming y confidence scoring."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.hooks import _post_tool_use_normalize, _pre_tool_use_short_query
from src.tools.ubidata import (
    _classify_http_error,
    _compute_field_confidence,
    _handle_validate_address,
    trim_ubidata_output,
)


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------


def _make_http_exc(status: int) -> httpx.HTTPStatusError:
    """Construye un HTTPStatusError con el status code indicado."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status
    return httpx.HTTPStatusError(
        message=f"HTTP {status}",
        request=MagicMock(spec=httpx.Request),
        response=response,
    )


def _make_mcp_content(result_dict: dict) -> dict:
    """Simula el input_data que llega al PostToolUse hook desde el MCP server."""
    return {"content": [{"type": "text", "text": json.dumps(result_dict)}]}


# ---------------------------------------------------------------------------
# 1. Clasificación de errores HTTP (_classify_http_error)
# ---------------------------------------------------------------------------


def test_classify_timeout_is_transient():
    """TimeoutException produce error transient + retryable=True."""
    exc = httpx.TimeoutException("read timeout")
    # TimeoutException se maneja directamente en _handle_validate_address;
    # aquí probamos _classify_http_error para 5xx (equivalente transient)
    http_exc = _make_http_exc(503)
    result = _classify_http_error(http_exc, "Av. Corrientes 1234")
    assert result["error_type"] == "transient"
    assert result["retryable"] is True
    assert result["attempted_query"] == "Av. Corrientes 1234"


def test_classify_422_is_business_not_retryable():
    """HTTP 422 (Unprocessable Entity) clasifica como business, retryable=False."""
    exc = _make_http_exc(422)
    result = _classify_http_error(exc, "query inválida")
    assert result["error_type"] == "business"
    assert result["retryable"] is False


def test_classify_400_is_business_not_retryable():
    """HTTP 400 clasifica como business, retryable=False."""
    exc = _make_http_exc(400)
    result = _classify_http_error(exc, "bad request")
    assert result["error_type"] == "business"
    assert result["retryable"] is False


def test_classify_401_is_permission():
    """HTTP 401 clasifica como permission, retryable=False."""
    exc = _make_http_exc(401)
    result = _classify_http_error(exc, "dirección privada")
    assert result["error_type"] == "permission"
    assert result["retryable"] is False


def test_classify_403_is_permission():
    """HTTP 403 clasifica como permission, retryable=False."""
    exc = _make_http_exc(403)
    result = _classify_http_error(exc, "zona restringida")
    assert result["error_type"] == "permission"
    assert result["retryable"] is False


def test_classify_500_is_transient():
    """HTTP 500 clasifica como transient, retryable=True."""
    exc = _make_http_exc(500)
    result = _classify_http_error(exc, "Rivadavia 1000")
    assert result["error_type"] == "transient"
    assert result["retryable"] is True


# ---------------------------------------------------------------------------
# 2. _with_retry en _handle_validate_address — timeout → segundo intento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_validate_address_retries_on_timeout():
    """Un TimeoutException en la primera llamada a Ubidata produce error transient con retryable=True."""
    with patch(
        "src.tools.ubidata._call_ubidata_api",
        side_effect=httpx.TimeoutException("timeout"),
    ):
        result = await _handle_validate_address("Av. Santa Fe 2350, CABA")

    assert result["error_type"] == "transient"
    assert result["retryable"] is True
    assert "timeout" in result["message"].lower()


@pytest.mark.asyncio
async def test_handle_validate_address_422_no_retry():
    """HTTP 422 produce error business retryable=False — no se reintenta."""
    with patch(
        "src.tools.ubidata._call_ubidata_api",
        side_effect=_make_http_exc(422),
    ):
        result = await _handle_validate_address("query inválida")

    assert result["error_type"] == "business"
    assert result["retryable"] is False


# ---------------------------------------------------------------------------
# 3. trim_ubidata_output — campos excluidos no aparecen
# ---------------------------------------------------------------------------


def test_trim_keeps_required_fields():
    """trim_ubidata_output conserva exactamente los campos de _KEEP_FIELDS."""
    raw = {
        "result_similarity": 0.92,
        "CPA": "C1043AAB",
        "PROVINCIA": "Buenos Aires",
        "LOCALIDAD": "CABA",
        "NOM_CALLE_ABR": "Av. Corrientes",
        "BAR_NOMBRE": "San Nicolás",
        "LATITUD": -34.6,
        "LONGITUD": -58.4,
        "HEIGHT": 1234,
        # Campos que deben ser eliminados:
        "NOM_CALLE_ABR_C": "Av. Corrientes 1234",
        "SIN_NOMBRE": False,
        "COD_DESDE": 1200,
        "COD_HASTA": 1300,
        "TIPO_COORD": "centroide",
        "localidad_original": "Capital Federal",
        "MUNICIPIO": "CABA",
        "PARTIDO": "CABA",
    }
    trimmed = trim_ubidata_output(raw)
    assert "NOM_CALLE_ABR_C" not in trimmed
    assert "SIN_NOMBRE" not in trimmed
    assert "COD_DESDE" not in trimmed
    assert "COD_HASTA" not in trimmed
    assert "TIPO_COORD" not in trimmed
    assert "localidad_original" not in trimmed
    assert "MUNICIPIO" not in trimmed
    assert "PARTIDO" not in trimmed


def test_trim_preserves_values():
    """Los valores de los campos conservados no se modifican."""
    raw = {
        "result_similarity": 0.88,
        "CPA": "B1640HJK",
        "PROVINCIA": "Buenos Aires",
        "LOCALIDAD": "Martínez",
        "NOM_CALLE_ABR": "Av. del Libertador",
        "BAR_NOMBRE": "",
        "LATITUD": -34.49,
        "LONGITUD": -58.50,
        "HEIGHT": 5000,
        "PARTIDO": "San Isidro",  # debe desaparecer
    }
    trimmed = trim_ubidata_output(raw)
    assert trimmed["result_similarity"] == 0.88
    assert trimmed["CPA"] == "B1640HJK"
    assert trimmed["LATITUD"] == -34.49
    assert "PARTIDO" not in trimmed


# ---------------------------------------------------------------------------
# 4. _compute_field_confidence
# ---------------------------------------------------------------------------


def test_field_confidence_exact_street():
    """Dirección cuya calle coincide exactamente produce street > 0."""
    raw = {
        "NOM_CALLE_ABR": "Av. Corrientes",
        "LOCALIDAD": "CABA",
        "HEIGHT": 1234,
        "result_similarity": 0.92,
    }
    conf = _compute_field_confidence("Av. Corrientes 1234, CABA", raw)
    assert conf["street"] > 0
    assert conf["locality"] > 0
    assert 0.0 <= conf["number"] <= 1.0
    assert conf["overall"] == 0.92


def test_field_confidence_exact_number():
    """Número exacto en query produce number=1.0."""
    raw = {
        "NOM_CALLE_ABR": "Rivadavia",
        "LOCALIDAD": "CABA",
        "HEIGHT": 1000,
        "result_similarity": 0.85,
    }
    conf = _compute_field_confidence("Rivadavia 1000, CABA", raw)
    assert conf["number"] == 1.0


def test_field_confidence_number_decay():
    """Número con diferencia de 50 produce number=0.5."""
    raw = {
        "NOM_CALLE_ABR": "San Martín",
        "LOCALIDAD": "Rosario",
        "HEIGHT": 550,
        "result_similarity": 0.80,
    }
    conf = _compute_field_confidence("San Martín 500, Rosario", raw)
    # diff = 50, decay = 50/100 = 0.5 → number = 0.5
    assert conf["number"] == pytest.approx(0.5, abs=0.01)


def test_field_confidence_no_number_in_query():
    """Sin número en la query, number=0.0."""
    raw = {
        "NOM_CALLE_ABR": "Corrientes",
        "LOCALIDAD": "CABA",
        "HEIGHT": 1234,
        "result_similarity": 0.70,
    }
    conf = _compute_field_confidence("Corrientes, CABA", raw)
    assert conf["number"] == 0.0


# ---------------------------------------------------------------------------
# 5. PreToolUse hook — bloquear queries cortos
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_hook_blocks_short_query():
    """Query de 4 caracteres o menos produce permissionDecision='deny'."""
    input_data = {"address_query": "abc"}  # 3 chars
    result = await _pre_tool_use_short_query(input_data, "tool-id-1", {})
    assert "hookSpecificOutput" in result
    hook_out = result["hookSpecificOutput"]
    assert hook_out["permissionDecision"] == "deny"
    assert hook_out["hookEventName"] == "PreToolUse"


@pytest.mark.asyncio
async def test_pre_hook_allows_valid_query():
    """Query de 5+ caracteres devuelve dict vacío (permitido)."""
    input_data = {"address_query": "Rivadavia 1000"}
    result = await _pre_tool_use_short_query(input_data, "tool-id-2", {})
    assert result == {}


@pytest.mark.asyncio
async def test_pre_hook_blocks_empty_query():
    """Query vacía produce deny."""
    input_data = {"address_query": ""}
    result = await _pre_tool_use_short_query(input_data, "tool-id-3", {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_pre_hook_blocks_whitespace_only():
    """Query con sólo espacios produce deny (strip reduce a 0 chars)."""
    input_data = {"address_query": "   "}
    result = await _pre_tool_use_short_query(input_data, "tool-id-4", {})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# 6. PostToolUse hook — normalización de output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_hook_normalizes_cpa_and_similarity():
    """PostToolUse convierte CPA a uppercase y redondea similarity a 4 decimales."""
    raw = {
        "validated": True,
        "result_similarity": 0.923456789,
        "cpa": "c1043aab",  # minúsculas, deben quedar en mayúsculas
        "confidence_score": 0.923456789,
    }
    input_data = _make_mcp_content(raw)
    result = await _post_tool_use_normalize(input_data, "tool-id-5", {})

    assert "updatedMCPToolOutput" in result
    updated_text = result["updatedMCPToolOutput"]["content"][0]["text"]
    updated = json.loads(updated_text)

    assert updated["cpa"] == "C1043AAB"
    assert updated["result_similarity"] == 0.9235
    assert updated["confidence_score"] == 0.9235


@pytest.mark.asyncio
async def test_post_hook_normalizes_normalize_candidates():
    """PostToolUse normaliza CPA y similarity en candidatos de normalize_address.

    Los candidatos ya están en formato procesado (normalize, similarity, cpa, ...),
    no en formato crudo de la API — el hook normaliza sin aplicar trim.
    """
    raw = {
        "candidates": [
            {
                "normalized": "Av. del Libertador 5000, Martínez, Buenos Aires",
                "similarity": 0.91234,
                "cpa": "b1640hjk",  # minúsculas → deben quedar en mayúsculas
                "province": "Buenos Aires",
                "locality": "Martínez",
                "coordinates": {"lat": -34.49, "lng": -58.50},
            }
        ],
        "total_found": 1,
    }
    input_data = _make_mcp_content(raw)
    result = await _post_tool_use_normalize(input_data, "tool-id-6", {})

    updated_text = result["updatedMCPToolOutput"]["content"][0]["text"]
    updated = json.loads(updated_text)
    candidate = updated["candidates"][0]

    assert candidate["cpa"] == "B1640HJK"
    assert candidate["similarity"] == pytest.approx(0.9123, abs=0.001)
    # Campos del candidato procesado se conservan intactos
    assert "normalized" in candidate
    assert "locality" in candidate


@pytest.mark.asyncio
async def test_post_hook_empty_content_returns_empty():
    """PostToolUse con content vacío devuelve dict vacío sin error."""
    result = await _post_tool_use_normalize({}, "tool-id-7", {})
    assert result == {}


@pytest.mark.asyncio
async def test_post_hook_invalid_json_returns_empty():
    """PostToolUse con texto no-JSON devuelve dict vacío sin lanzar excepción."""
    input_data = {"content": [{"type": "text", "text": "not json {{{"}]}
    result = await _post_tool_use_normalize(input_data, "tool-id-8", {})
    assert result == {}
