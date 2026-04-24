"""Orquestador: enruta cada consulta de validación al agente especializado correspondiente."""

import json
import re

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ResultMessage, query

from src.agents.data_cleaning import _DC_SYSTEM_PROMPT as _DATA_CLEANING_PROMPT
from src.agents.logistics import _SYSTEM_PROMPT as _LOGISTICS_PROMPT
from src.agents.onboarding import _SYSTEM_PROMPT as _ONBOARDING_PROMPT
from src.agents.support import _SYSTEM_PROMPT as _SUPPORT_PROMPT
from src.tools._sdk_server import UBIDATA_MCP_SERVER

_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Definiciones de subagentes
# Los tools usan el prefijo mcp__ubidata__ que el SDK asigna al servidor MCP.
# ---------------------------------------------------------------------------

_SUBAGENTS: dict[str, AgentDefinition] = {
    "onboarding": AgentDefinition(
        description=(
            "Valida y corrige la dirección de un nuevo usuario. "
            "Usar cuando el request es de alta, primer registro, o nuevo cliente."
        ),
        prompt=_ONBOARDING_PROMPT,
        tools=[
            "mcp__ubidata__validate_address",
            "mcp__ubidata__normalize_address",
        ],
    ),
    "support": AgentDefinition(
        description=(
            "Maneja cambios de dirección de clientes existentes. "
            "Usar cuando el request menciona cambio, actualización o modificación de domicilio."
        ),
        prompt=_SUPPORT_PROMPT,
        tools=[
            "mcp__ubidata__validate_address",
            "mcp__ubidata__normalize_address",
            "mcp__ubidata__check_coverage",
        ],
    ),
    "logistics": AgentDefinition(
        description=(
            "Valida una dirección antes de un despacho o envío. "
            "Usar cuando el request viene de fulfillment o menciona despacho, envío o entrega."
        ),
        prompt=_LOGISTICS_PROMPT,
        tools=[
            "mcp__ubidata__validate_address",
            "mcp__ubidata__check_coverage",
        ],
    ),
    "data_cleaning": AgentDefinition(
        description=(
            "Valida una dirección de una base de datos existente. "
            "Usar cuando el request menciona lote, batch, base de datos, CSV o múltiples registros."
        ),
        prompt=_DATA_CLEANING_PROMPT,
        tools=[
            "mcp__ubidata__validate_address",
        ],
    ),
}

# ---------------------------------------------------------------------------
# System prompt del Orchestrator
# ---------------------------------------------------------------------------

_ORCHESTRATOR_SYSTEM_PROMPT = """\
Eres un orquestador de validación de direcciones argentinas. Tu única responsabilidad
es identificar qué agente especializado debe manejar cada request y delegarlo correctamente.

Reglas de routing:
- Si el request es de alta, primer registro o nuevo cliente → usar subagente "onboarding".
- Si el request menciona cambio, actualización o modificación de domicilio → usar "support".
- Si el request viene de fulfillment, menciona despacho, envío o entrega → usar "logistics".
- Si el request menciona lote, batch, base de datos, CSV o múltiples registros → usar "data_cleaning".
- Si el request aplica a más de un agente (ej. alta + verificación de despacho simultáneo)
  → invocar ambos en paralelo emitiendo múltiples Agent calls en una sola respuesta.

Reglas de context passing:
- Los subagentes NO heredan el contexto de esta conversación automáticamente.
- Al invocar un subagente, incluir en el prompt el request completo del usuario más
  cualquier contexto relevante (zonas de cobertura, ID de cliente, etc.).

Al finalizar TODOS los subagentes invocados, responder ÚNICAMENTE con un objeto JSON:
{"agents_invoked": [lista de nombres de subagentes usados],
 "parallel": true si se invocaron múltiples en paralelo o false si fue secuencial,
 "results": {nombre_agente: resultado_devuelto_por_el_subagente},
 "routing_reasoning": "oración corta explicando por qué se eligió ese routing"}
"""

# ---------------------------------------------------------------------------
# Parser de respuesta
# ---------------------------------------------------------------------------


def _parse_orchestrator_json(text: str) -> dict:
    """Extrae el JSON de respuesta del orchestrator.

    Args:
        text: Texto final devuelto por el orchestrator.

    Returns:
        Diccionario con agents_invoked, parallel, results y routing_reasoning.
        En caso de fallo devuelve un fallback con agents_invoked vacío.
    """
    _fallback: dict = {
        "agents_invoked": [],
        "parallel": False,
        "results": {},
        "routing_reasoning": "Sin respuesta del orquestador.",
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


# ---------------------------------------------------------------------------
# Función pública
# ---------------------------------------------------------------------------


async def run_orchestrator(user_request: str) -> dict:
    """Enruta un request de validación al agente especializado correcto.

    El orquestador analiza el request, elige uno o más subagentes, los invoca
    (en paralelo si corresponde) y devuelve un resumen consolidado.

    Args:
        user_request: Descripción en lenguaje natural del request de validación.

    Returns:
        Diccionario con agents_invoked, parallel, results y routing_reasoning.
    """
    result_text = ""

    async for message in query(
        prompt=user_request,
        options=ClaudeAgentOptions(
            system_prompt=_ORCHESTRATOR_SYSTEM_PROMPT,
            mcp_servers={"ubidata": UBIDATA_MCP_SERVER},
            allowed_tools=["Agent"],  # habilita el spawning de subagentes
            agents=_SUBAGENTS,
            model=_MODEL,
            max_turns=20,
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result or ""

    return _parse_orchestrator_json(result_text)
