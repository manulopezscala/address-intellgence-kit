"""Agente de logística: verifica cobertura de despacho a partir del CPA resuelto."""

import json
import re

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from src.tools._sdk_server import UBIDATA_MCP_SERVER

_MODEL = "claude-sonnet-4-20250514"

# Least privilege: logistics solo aprueba/rechaza, nunca corrige
_LOGISTICS_TOOLS = ["validate_address", "check_coverage"]

_SYSTEM_PROMPT = """\
Eres un agente de validación logística para Argentina. Decides si una dirección
puede recibir despacho. Nunca corrijas la dirección — solo aprueba, rechaza o bloquea.

Secuencia OBLIGATORIA:
1. Llamar validate_address con la dirección recibida exactamente como fue proporcionada.
2. Si y solo si result_similarity >= 0.80 → llamar check_coverage con el CPA obtenido.
3. Nunca llamar check_coverage antes de validate_address ni si validated=False.

Criterios de APROBACIÓN (TODOS deben cumplirse):
  - result_similarity >= 0.85
  - LATITUD entre -55.0 y -22.0 (rango continental Argentina)
  - LONGITUD entre -74.0 y -53.0
  - check_coverage devuelve covered=true

Criterios de RECHAZO (basta UNO):
  - result_similarity < 0.80
  - Coordenadas fuera del rango Argentina
  - Sin resultados de Ubidata (risk_level="blocked" sin error de red)

Criterios de BLOQUEO — escalar a operador humano:
  - risk_level="blocked" con error_type="transient" después de 2 intentos
  - Ambigüedad irresolvible sin información adicional del cliente

Al finalizar, responder ÚNICAMENTE con un objeto JSON con exactamente estas claves:
{"decision": "approved" o "rejected" o "blocked",
 "address": string,
 "cpa": string o null,
 "risk_level": string,
 "covered": true o false o null,
 "result_similarity": float o null,
 "rejection_reason": string o null}

"rejection_reason" debe ser null si decision="approved", y una oración corta en los demás casos.
"""


def _parse_agent_json(text: str, original_address: str = "") -> dict:
    """Extrae el primer objeto JSON del texto de respuesta del agente.

    Args:
        text: Texto de respuesta final del agente.
        original_address: Dirección original, usada en el fallback.

    Returns:
        Diccionario parseado, o el fallback de bloqueo si no hay JSON válido.
    """
    _fallback: dict = {
        "decision": "blocked",
        "address": original_address,
        "cpa": None,
        "risk_level": "blocked",
        "covered": None,
        "result_similarity": None,
        "rejection_reason": "Sin respuesta del agente.",
    }
    if not text:
        return _fallback
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return _fallback


async def run_logistics_agent(address: str) -> dict:
    """Valida una dirección y decide si puede recibir despacho logístico.

    Secuencia interna: validate_address → (si similarity >= 0.80) → check_coverage.
    El agente nunca intenta corregir la dirección — solo aprueba, rechaza o bloquea.

    Args:
        address: Dirección en lenguaje natural a evaluar para despacho.

    Returns:
        Diccionario con decision, address, cpa, risk_level, covered,
        result_similarity y rejection_reason.
    """
    result_text = ""

    async for message in query(
        prompt=address,
        options=ClaudeAgentOptions(
            system_prompt=_SYSTEM_PROMPT,
            mcp_servers={"ubidata": UBIDATA_MCP_SERVER},
            allowed_tools=_LOGISTICS_TOOLS,
            model=_MODEL,
            max_turns=10,
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result or ""

    return _parse_agent_json(result_text, original_address=address)
