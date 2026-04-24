"""Agente de limpieza de datos: normaliza y enriquece bases de direcciones en lote."""

import asyncio
import json
import re
from typing import Any

from anthropic import AsyncAnthropic
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from src import config
from src.tools._sdk_server import UBIDATA_MCP_SERVER
from src.tools.ubidata import TOOL_DEFINITIONS, _handle_validate_address

_MODEL = "claude-sonnet-4-20250514"

# Least privilege: data cleaning solo necesita validar, no normalizar ni verificar cobertura
_DC_TOOLS = ["validate_address"]

_DC_SYSTEM_PROMPT = """\
Eres un asistente de limpieza de bases de datos de direcciones argentinas.
Para cada dirección recibida, debes:
1. Llamar validate_address con la dirección exactamente como fue proporcionada.
2. Clasificar según el result_similarity obtenido:
   - result_similarity >= 0.85 → "validated"
   - 0.65 <= result_similarity < 0.85 → "needs_review"
   - result_similarity < 0.65 o sin resultados → "failed"
3. Al finalizar, responder ÚNICAMENTE con un objeto JSON con exactamente estas claves:
   {"status": "validated" o "needs_review" o "failed",
    "address": string o null,
    "cpa": string o null,
    "result_similarity": float o null}

"address" debe ser la dirección normalizada devuelta por Ubidata, o null si no hubo resultado.
"""

_BATCH_SYSTEM_PROMPT = (
    "Eres un validador de direcciones argentinas. "
    "Para la dirección recibida, llama validate_address con ella tal como fue proporcionada."
)


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------


def _classify(similarity: float | None) -> str:
    """Clasifica un score de similitud en una categoría de limpieza.

    Args:
        similarity: Score de similitud 0-1 devuelto por Ubidata, o None.

    Returns:
        "validated", "needs_review" o "failed".
    """
    if similarity is None or similarity < config.SIMILARITY_MEDIUM_RISK:
        return "failed"
    if similarity < config.SIMILARITY_LOW_RISK:
        return "needs_review"
    return "validated"


def _parse_agent_json(text: str) -> dict:
    """Extrae el primer objeto JSON del texto de respuesta del agente.

    Args:
        text: Texto de respuesta final del agente.

    Returns:
        Diccionario parseado, o el fallback de fallo si no hay JSON válido.
    """
    _fallback: dict = {
        "status": "failed",
        "address": None,
        "cpa": None,
        "result_similarity": None,
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
# Parte A: agente single-address (Claude Agent SDK)
# ---------------------------------------------------------------------------


async def run_data_cleaning_agent(address: str) -> dict:
    """Valida y clasifica una única dirección para limpieza de base de datos.

    Usa el Claude Agent SDK (multi-turn). Para procesar un volumen alto de
    direcciones en lote, usar process_batch() en su lugar.

    Args:
        address: Dirección en lenguaje natural a clasificar.

    Returns:
        Diccionario con status ("validated" | "needs_review" | "failed"),
        address (normalizada o null), cpa y result_similarity.
    """
    result_text = ""

    async for message in query(
        prompt=address,
        options=ClaudeAgentOptions(
            system_prompt=_DC_SYSTEM_PROMPT,
            mcp_servers={"ubidata": UBIDATA_MCP_SERVER},
            allowed_tools=_DC_TOOLS,
            model=_MODEL,
            max_turns=5,
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result or ""

    return _parse_agent_json(result_text)


# ---------------------------------------------------------------------------
# Parte B: procesamiento en lote (Anthropic Batch API — sin Agent SDK)
# ---------------------------------------------------------------------------
#
# Batch API no soporta multi-turn tool calling.
# Cada dirección es un request independiente y sin estado.
# Agent SDK es para flujos multi-turn; Batch API para volumen alto sin latencia SLA.
# 50% de ahorro en costo vs. requests individuales síncronos.


def _build_batch_requests(
    addresses: list[dict],
    validate_def: dict,
) -> list[dict]:
    """Construye la lista de requests para la Batch API.

    Args:
        addresses: Lista de dicts con "id" y "address".
        validate_def: Definición de la tool validate_address.

    Returns:
        Lista de dicts compatibles con client.beta.messages.batches.create.
    """
    return [
        {
            "custom_id": str(addr["id"]),
            "params": {
                "model": _MODEL,
                "max_tokens": 256,
                "system": _BATCH_SYSTEM_PROMPT,
                "tools": [validate_def],
                "tool_choice": {"type": "any"},  # fuerza el tool call
                "messages": [{"role": "user", "content": addr["address"]}],
            },
        }
        for addr in addresses
    ]


async def _poll_until_done(client: AsyncAnthropic, batch_id: str) -> Any:
    """Espera a que un batch termine sondeando cada 30 segundos.

    Args:
        client: Cliente async de Anthropic.
        batch_id: ID del batch a monitorear.

    Returns:
        Objeto de estado final del batch.
    """
    while True:
        status = await client.beta.messages.batches.retrieve(batch_id)
        if status.processing_status != "in_progress":
            return status
        await asyncio.sleep(30)


async def _submit_and_collect(
    client: AsyncAnthropic,
    addresses: list[dict],
    validate_def: dict,
) -> tuple[dict[str, dict], str]:
    """Submite un batch, espera su finalización y ejecuta los tool calls en paralelo.

    Args:
        client: Cliente async de Anthropic.
        addresses: Lista de dicts con "id" y "address".
        validate_def: Definición de la tool validate_address.

    Returns:
        Tupla (mapa de custom_id → result_dict, batch_id).
    """
    requests = _build_batch_requests(addresses, validate_def)
    batch = await client.beta.messages.batches.create(requests=requests)
    batch_id: str = batch.id

    await _poll_until_done(client, batch_id)

    # Recolectar tool_use inputs de las respuestas exitosas
    pending_ids: list[str] = []
    pending_queries: list[str] = []
    results: dict[str, dict] = {}

    async for result in client.beta.messages.batches.results(batch_id):
        custom_id = result.custom_id
        if result.result.type != "succeeded":
            results[custom_id] = {
                "status": "failed",
                "address": None,
                "cpa": None,
                "result_similarity": None,
            }
            continue

        tool_use = next(
            (b for b in result.result.message.content if b.type == "tool_use"),
            None,
        )
        if tool_use is None:
            results[custom_id] = {
                "status": "failed",
                "address": None,
                "cpa": None,
                "result_similarity": None,
            }
            continue

        address_query: str = tool_use.input.get("address_query", "")
        pending_ids.append(custom_id)
        pending_queries.append(address_query)

    # Ejecutar todas las llamadas a Ubidata en paralelo
    if pending_queries:
        tool_results = await asyncio.gather(
            *[_handle_validate_address(address_query=aq) for aq in pending_queries]
        )
        for custom_id, tool_result in zip(pending_ids, tool_results):
            similarity: float | None = tool_result.get("result_similarity")
            results[custom_id] = {
                "status": _classify(similarity),
                "address": tool_result.get("normalized_address"),
                "cpa": tool_result.get("cpa"),
                "result_similarity": similarity,
            }

    return results, batch_id


async def process_batch(addresses: list[dict]) -> dict:
    """Procesa un lote de direcciones usando la Anthropic Batch API.

    Cada dirección se valida contra Ubidata de forma independiente y se
    clasifica según su result_similarity. Si más del 10 % de las direcciones
    fallan, se reintenta ese subconjunto automáticamente.

    Args:
        addresses: Lista de dicts con al menos las claves "id" (int o str) y
            "address" (str). Ejemplo::

                [{"id": 1, "address": "Av. Corrientes 1234, CABA"},
                 {"id": 2, "address": "San Martín 500, Rosario, Santa Fe"}]

    Returns:
        Diccionario con total, validated, needs_review, failed y batch_id.
    """
    validate_def = next(t for t in TOOL_DEFINITIONS if t["name"] == "validate_address")
    client = AsyncAnthropic()

    # Primera pasada
    results, batch_id = await _submit_and_collect(client, addresses, validate_def)

    # Reintentar si tasa de fallo supera el 10 %
    failed_ids = {cid for cid, r in results.items() if r["status"] == "failed"}
    if len(failed_ids) / max(len(addresses), 1) > 0.10:
        failed_addresses = [a for a in addresses if str(a["id"]) in failed_ids]
        retry_results, _ = await _submit_and_collect(client, failed_addresses, validate_def)
        results.update(retry_results)

    return {
        "total": len(addresses),
        "validated": sum(1 for r in results.values() if r["status"] == "validated"),
        "needs_review": sum(1 for r in results.values() if r["status"] == "needs_review"),
        "failed": sum(1 for r in results.values() if r["status"] == "failed"),
        "batch_id": batch_id,
    }
