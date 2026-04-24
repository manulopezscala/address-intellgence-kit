"""Agente de onboarding: valida direcciones durante el alta de nuevos clientes."""

import json
import re

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from src.hooks import UBIDATA_HOOKS
from src.tools._sdk_server import UBIDATA_MCP_SERVER, UBIDATA_TOOL_NAMES

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """\
Eres un asistente de validación de direcciones para el alta de nuevos clientes en Argentina.
Tu objetivo: confirmar que la dirección del cliente existe y es inequívoca antes de registrarla.

Reglas:
1. Si la dirección parece incompleta o con abreviaciones informales → llamar normalize_address primero.
2. Si parece completa (calle + altura + localidad) → llamar validate_address directamente.
3. Según risk_level del resultado:
   - "low" (similarity >= 0.85): dirección válida, confirmar.
   - "medium" (0.65–0.84): confirmar igualmente mostrando la versión normalizada.
   - "high" o "blocked": intentar normalize_address si aún no se llamó; si persiste, escalar.
4. Máximo 3 llamadas a herramientas de validación en total. Si se supera ese límite, escalar con
   mensaje claro indicando que la dirección no pudo resolverse automáticamente.
5. Al finalizar, responder ÚNICAMENTE con un objeto JSON con exactamente estas claves:
   {"status": "validated" o "escalated", "validated_address": string o null,
    "cpa": string o null, "risk_level": string}

Ejemplos:
---
Input: "Av. Corrientes 1234, Baires"
→ Llamar normalize_address (abreviación informal de localidad)
→ Tomar el candidato con mayor similarity
→ Llamar validate_address con la dirección normalizada
→ Si risk_level="low": {"status": "validated", "validated_address": "Av. Corrientes 1234, Ciudad Autónoma de Buenos Aires, Buenos Aires", "cpa": "C1043AAB", "risk_level": "low"}
---
Input: "Belgrano, Capital"
→ Sin altura → no llamar ninguna herramienta
→ {"status": "escalated", "validated_address": null, "cpa": null, "risk_level": "blocked"}
---
Input: "Rivadavia 1000, CABA"
→ Dirección completa → llamar validate_address directamente
→ Si similarity >= 0.85: {"status": "validated", "validated_address": "Rivadavia 1000, Ciudad Autónoma de Buenos Aires, Buenos Aires", "cpa": "C1002AAT", "risk_level": "low"}
"""


def _parse_agent_json(text: str) -> dict:
    """Extrae el primer objeto JSON del texto de respuesta del agente.

    Args:
        text: Texto de respuesta final del agente.

    Returns:
        Diccionario parseado, o el fallback de escalación si no hay JSON válido.
    """
    _fallback = {
        "status": "escalated",
        "validated_address": None,
        "cpa": None,
        "risk_level": "blocked",
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


async def run_onboarding_agent(address: str) -> dict:
    """Valida la dirección de un nuevo cliente usando la API de Ubidata.

    Args:
        address: Dirección en lenguaje natural proporcionada por el usuario.

    Returns:
        Diccionario con status, validated_address, cpa, risk_level, attempts
        y conversation_turns.
    """
    onboarding_tools = [t for t in UBIDATA_TOOL_NAMES if t != "check_coverage"]

    result_text = ""
    num_turns = 0

    async for message in query(
        prompt=address,
        options=ClaudeAgentOptions(
            system_prompt=_SYSTEM_PROMPT,
            mcp_servers={"ubidata": UBIDATA_MCP_SERVER},
            allowed_tools=onboarding_tools,
            hooks=UBIDATA_HOOKS,
            model=_MODEL,
            max_turns=10,
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result or ""
            num_turns = message.num_turns

    parsed = _parse_agent_json(result_text)
    return {**parsed, "attempts": num_turns, "conversation_turns": num_turns}
