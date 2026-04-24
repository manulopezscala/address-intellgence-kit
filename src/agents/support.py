"""Agente de soporte: corrige y confirma direcciones reportadas como incorrectas por usuarios."""

import json
import re

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from src.hooks import UBIDATA_HOOKS
from src.tools._sdk_server import UBIDATA_MCP_SERVER, UBIDATA_TOOL_NAMES

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """\
Eres un asistente de soporte para cambios de dirección de clientes existentes en Argentina.

Secuencia OBLIGATORIA — seguirla en este orden exacto:
1. validate_address con la nueva dirección.
2. Si validated=True y risk_level="low" → check_coverage con el CPA obtenido.
3. Nunca llamar check_coverage antes de validate_address.
4. Nunca llamar check_coverage si validated=False.

Reglas de decisión:
- Si validated=False → intentar normalize_address para obtener candidatos;
  si ninguno tiene risk_level "low", estado final: invalid_address.
- Si risk_level="high" o "blocked" → estado final: escalated.
- Si check_coverage devuelve covered=False → estado final: no_coverage;
  NO confirmar el cambio; informar que esa zona no tiene cobertura de despacho.
- Si validated=True, risk_level="low" y covered=True → estado final: confirmed.

Al finalizar, responder ÚNICAMENTE con un objeto JSON con exactamente estas claves:
{"status": "confirmed" o "no_coverage" o "invalid_address" o "escalated",
 "new_address": string o null, "cpa": string o null,
 "covered": true o false o null, "message": string}

El campo "message" debe ser una oración breve explicando el resultado al cliente.
"""


def _parse_agent_json(text: str, default_status: str = "escalated") -> dict:
    """Extrae el primer objeto JSON del texto de respuesta del agente.

    Args:
        text: Texto de respuesta final del agente.
        default_status: Status a usar en el fallback si no hay JSON válido.

    Returns:
        Diccionario parseado, o fallback con default_status si no hay JSON válido.
    """
    _fallback = {
        "status": default_status,
        "new_address": None,
        "cpa": None,
        "covered": None,
        "message": "No se pudo procesar la solicitud. Por favor contacte soporte.",
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


async def run_support_agent(
    new_address: str,
    coverage_zones: list[str] | None = None,
) -> dict:
    """Valida una nueva dirección y verifica cobertura de despacho antes de confirmar el cambio.

    Args:
        new_address: Nueva dirección propuesta por el cliente.
        coverage_zones: Lista de prefijos de CPA cubiertos. Si se omite, usa la
            lista demo de CABA y GBA definida en ubidata.py.

    Returns:
        Diccionario con status, new_address, cpa, covered y message.
    """
    prompt = new_address
    if coverage_zones:
        prompt += f"\n[Zonas de cobertura disponibles: {coverage_zones}]"

    result_text = ""

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=_SYSTEM_PROMPT,
            mcp_servers={"ubidata": UBIDATA_MCP_SERVER},
            allowed_tools=UBIDATA_TOOL_NAMES,
            hooks=UBIDATA_HOOKS,
            model=_MODEL,
            max_turns=10,
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result or ""

    return _parse_agent_json(result_text)
