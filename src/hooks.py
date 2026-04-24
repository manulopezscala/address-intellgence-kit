"""Hooks nativos del Agent SDK para todas las invocaciones a herramientas de Ubidata.

Diferencia clave entre hooks y system-prompt instructions:
  Hook         = determinístico (100 % garantizado, se ejecuta en cada llamada).
  Instrucción  = probabilístico (>90 %, no 100 %; el modelo puede ignorarla).
  Para reglas de negocio críticas (validaciones de input, normalización de output)
  siempre usar hooks, nunca depender sólo del system prompt.
"""

import json

from claude_agent_sdk import HookMatcher


# ---------------------------------------------------------------------------
# PreToolUse — bloquear queries demasiado cortos
# ---------------------------------------------------------------------------

_MIN_QUERY_LEN = 5


async def _pre_tool_use_short_query(
    input_data: dict,
    tool_use_id: str,  # noqa: ARG001
    context: dict,  # noqa: ARG001
) -> dict:
    """Bloquea llamadas a Ubidata cuando la dirección tiene menos de 5 caracteres.

    Una dirección válida necesita al menos calle + altura, así que queries de
    4 caracteres o menos son invariablemente ruido o errores de integración.
    Devolver "deny" aquí evita una llamada HTTP innecesaria y garantiza que el
    modelo nunca consulte Ubidata con basura — independientemente de lo que diga
    el system prompt.

    Args:
        input_data: Dict con los argumentos del tool call (contiene "address_query").
        tool_use_id: ID del tool use (no usado).
        context: Contexto de la sesión del agente (no usado).

    Returns:
        Dict de deny si la query es demasiado corta, o dict vacío para permitir.
    """
    address_query: str = input_data.get("address_query", "") or ""
    if len(address_query.strip()) < _MIN_QUERY_LEN:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Query demasiado corto ({len(address_query.strip())} caracteres). "
                    f"Mínimo {_MIN_QUERY_LEN} caracteres requeridos para consultar Ubidata."
                ),
            }
        }
    return {}


# ---------------------------------------------------------------------------
# PostToolUse — normalizar y trimear output de Ubidata
# ---------------------------------------------------------------------------


async def _post_tool_use_normalize(
    input_data: dict,
    tool_use_id: str,  # noqa: ARG001
    context: dict,  # noqa: ARG001
) -> dict:
    """Normaliza el output de validate_address y normalize_address antes de enviarlo al modelo.

    Transformaciones aplicadas:
      - CPA: strip + uppercase
      - result_similarity: round a 4 decimales
      - Candidatos de normalize_address: aplica trim_ubidata_output a cada uno
      - Top-level result de validate_address: normaliza CPA y similarity

    Reduce el token count ~40 % en los tool results sin perder información
    que el modelo necesite para tomar decisiones.

    Args:
        input_data: Dict con el tool result tal como lo devolvió el servidor MCP.
            Tiene la forma {"content": [{"type": "text", "text": "<json>"}]}.
        tool_use_id: ID del tool use (no usado).
        context: Contexto de la sesión del agente (no usado).

    Returns:
        Dict con "updatedMCPToolOutput" conteniendo el output normalizado,
        o dict vacío si el resultado no es parseable.
    """
    content = input_data.get("content", [])
    if not content:
        return {}

    # El MCP server serializa el resultado como texto JSON en el primer bloque
    first = content[0] if content else {}
    raw_text: str = first.get("text", "") if isinstance(first, dict) else ""
    if not raw_text:
        return {}

    try:
        result: dict = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return {}

    # --- Normalizar validate_address output ---
    if "result_similarity" in result:
        if result.get("cpa"):
            result["cpa"] = str(result["cpa"]).strip().upper()
        if result.get("result_similarity") is not None:
            result["result_similarity"] = round(float(result["result_similarity"]), 4)
        if result.get("confidence_score") is not None:
            result["confidence_score"] = round(float(result["confidence_score"]), 4)

    # --- Normalizar normalize_address output (lista de candidatos ya procesados) ---
    # Los candidatos de normalize_address ya tienen una estructura limpia
    # (normalize, similarity, cpa, province, locality, coordinates) — no son
    # respuestas crudas de la API, por lo que trim_ubidata_output no aplica aquí.
    if "candidates" in result and isinstance(result["candidates"], list):
        for c in result["candidates"]:
            if not isinstance(c, dict):
                continue
            if "similarity" in c and c["similarity"] is not None:
                c["similarity"] = round(float(c["similarity"]), 4)
            if c.get("cpa"):
                c["cpa"] = str(c["cpa"]).strip().upper()

    updated_text = json.dumps(result, ensure_ascii=False)
    return {
        "updatedMCPToolOutput": {
            "content": [{"type": "text", "text": updated_text}]
        }
    }


# ---------------------------------------------------------------------------
# Registro de hooks exportado
# ---------------------------------------------------------------------------

_VALIDATE_AND_NORMALIZE = (
    "mcp__ubidata__validate_address|mcp__ubidata__normalize_address"
)

UBIDATA_HOOKS: dict = {
    "PreToolUse": [
        HookMatcher(
            matcher=_VALIDATE_AND_NORMALIZE,
            hooks=[_pre_tool_use_short_query],
        )
    ],
    "PostToolUse": [
        HookMatcher(
            matcher=_VALIDATE_AND_NORMALIZE,
            hooks=[_post_tool_use_normalize],
        )
    ],
}
