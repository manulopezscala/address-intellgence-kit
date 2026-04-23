"""Herramienta de consulta a la API de Ubidata para resolución de direcciones argentinas."""

from collections.abc import Callable

import httpx

from src import config

_DEFAULT_COVERAGE_PREFIXES: list[str] = [
    "C1",  # CABA (C + 1xxx)
    "B1000", "B1100", "B1200", "B1300", "B1400",  # GBA norte/oeste
    "B1600", "B1700", "B1800", "B1900",  # GBA sur
]

_UBIDATA_ENDPOINT = "/api/query/unstructured"


def _derive_risk_level(similarity: float | None) -> str:
    """Clasifica un score de similitud en un nivel de riesgo.

    Args:
        similarity: Score de similitud 0-1 devuelto por Ubidata, o None si no
            hubo resultados.

    Returns:
        Uno de: "low", "medium", "high", "blocked".
    """
    if similarity is None:
        return "blocked"
    if similarity >= config.SIMILARITY_LOW_RISK:
        return "low"
    if similarity >= config.SIMILARITY_MEDIUM_RISK:
        return "medium"
    return "high"


def _build_normalized_string(result: dict) -> str:
    """Construye una dirección normalizada legible a partir de un resultado de Ubidata.

    Args:
        result: Diccionario de un candidato devuelto por la API de Ubidata.

    Returns:
        Cadena con formato "Calle Altura, Localidad, Provincia".
    """
    return f"{result['NOM_CALLE_ABR_C']}, {result['LOCALIDAD']}, {result['PROVINCIA']}"


async def _call_ubidata_api(query: str, max_results: int) -> list[dict]:
    """Realiza la llamada HTTP a la API de Ubidata.

    Args:
        query: Dirección en lenguaje natural.
        max_results: Cantidad máxima de candidatos a devolver.

    Returns:
        Lista de candidatos devueltos por la API (puede ser vacía).

    Raises:
        httpx.TimeoutException: Si la petición supera el timeout configurado.
        httpx.HTTPStatusError: Si la API responde con un código de error HTTP.
    """
    payload = {
        "query": query,
        "threshold_abs_list": [0.5],
        "thresholds_rel_list": [0.0],
        "max_addresses_to_show_list": [max_results],
    }
    headers = {"Authorization": f"Bearer {config.UBIDATA_API_KEY}"}
    url = f"{config.UBIDATA_BASE_URL}{_UBIDATA_ENDPOINT}"

    async with httpx.AsyncClient(timeout=config.UBIDATA_TIMEOUT) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def _handle_validate_address(
    address_query: str,
    max_results: int = 1,
) -> dict:
    """Verifica si una dirección existe en la base de Ubidata y devuelve su nivel de riesgo.

    Args:
        address_query: Dirección argentina en lenguaje natural.
        max_results: Máximo de candidatos a consultar (default 1).

    Returns:
        Diccionario con validated, result_similarity, normalized_address, cpa,
        coordinates, risk_level y confidence_score. En caso de error devuelve
        un dict con error_type, message, retryable y attempted_query.
    """
    try:
        results = await _call_ubidata_api(address_query, max_results)
    except httpx.TimeoutException as exc:
        return {
            "error_type": "transient",
            "message": str(exc),
            "retryable": True,
            "attempted_query": address_query,
        }
    except httpx.HTTPStatusError as exc:
        is_client_error = 400 <= exc.response.status_code < 500
        return {
            "error_type": "business" if is_client_error else "transient",
            "message": str(exc),
            "retryable": not is_client_error,
            "attempted_query": address_query,
        }
    except Exception as exc:
        return {
            "error_type": "transient",
            "message": str(exc),
            "retryable": True,
            "attempted_query": address_query,
        }

    if not results:
        return {
            "validated": False,
            "result_similarity": None,
            "normalized_address": None,
            "cpa": None,
            "coordinates": None,
            "risk_level": "blocked",
            "confidence_score": 0.0,
        }

    best = results[0]
    similarity: float = best["result_similarity"]
    return {
        "validated": True,
        "result_similarity": similarity,
        "normalized_address": _build_normalized_string(best),
        "cpa": best.get("CPA"),
        "coordinates": {"lat": best["LATITUD"], "lng": best["LONGITUD"]},
        "risk_level": _derive_risk_level(similarity),
        "confidence_score": similarity,
    }


async def _handle_normalize_address(
    address_query: str,
    max_candidates: int = 3,
) -> dict:
    """Estandariza una dirección y devuelve múltiples candidatos ordenados por similitud.

    Args:
        address_query: Dirección argentina en lenguaje natural, posiblemente mal formada.
        max_candidates: Máximo de candidatos a devolver (default 3).

    Returns:
        Diccionario con candidates (lista) y total_found. En caso de error devuelve
        un dict con error_type, message, retryable y attempted_query.
    """
    try:
        results = await _call_ubidata_api(address_query, max_candidates)
    except httpx.TimeoutException as exc:
        return {
            "error_type": "transient",
            "message": str(exc),
            "retryable": True,
            "attempted_query": address_query,
        }
    except httpx.HTTPStatusError as exc:
        is_client_error = 400 <= exc.response.status_code < 500
        return {
            "error_type": "business" if is_client_error else "transient",
            "message": str(exc),
            "retryable": not is_client_error,
            "attempted_query": address_query,
        }
    except Exception as exc:
        return {
            "error_type": "transient",
            "message": str(exc),
            "retryable": True,
            "attempted_query": address_query,
        }

    candidates = [
        {
            "normalized": _build_normalized_string(r),
            "similarity": r["result_similarity"],
            "cpa": r.get("CPA"),
            "province": r.get("PROVINCIA"),
            "locality": r.get("LOCALIDAD"),
            "coordinates": {"lat": r["LATITUD"], "lng": r["LONGITUD"]},
        }
        for r in results
    ]
    return {"candidates": candidates, "total_found": len(candidates)}


async def _handle_check_coverage(
    cpa: str,
    coverage_zones: list[str] | None = None,
) -> dict:
    """Verifica si un CPA tiene cobertura de despacho logístico.

    Args:
        cpa: Código Postal Argentino de 7 caracteres (ej. "C1043AAB").
        coverage_zones: Lista de prefijos de CPA cubiertos. Si se omite, usa la
            lista demo que cubre CABA y GBA.

    Returns:
        Diccionario con covered, cpa, zone y message.
    """
    zones = coverage_zones or _DEFAULT_COVERAGE_PREFIXES
    cpa_upper = cpa.strip().upper()
    covered = any(cpa_upper.startswith(z.upper()) for z in zones)

    if cpa_upper and cpa_upper[0] == "C":
        zone: str | None = "CABA"
    elif cpa_upper and cpa_upper[0] == "B":
        zone = "Gran Buenos Aires"
    else:
        zone = None

    if covered:
        zone_label = zone or "zona configurada"
        message = f"CPA {cpa_upper} tiene cobertura en {zone_label}."
    else:
        message = f"CPA {cpa_upper} no tiene cobertura en las zonas configuradas."

    return {"covered": covered, "cpa": cpa_upper, "zone": zone, "message": message}


TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "validate_address",
        "description": (
            "Verifica si una dirección argentina existe en la base de Ubidata y "
            "devuelve su nivel de riesgo basado en similarity. Usar cuando el "
            "objetivo es CONFIRMAR LA EXISTENCIA de una dirección. No usar para "
            "corregir o estandarizar formato — para eso usar normalize_address."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address_query": {
                    "type": "string",
                    "description": "Dirección argentina en lenguaje natural.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Máximo de candidatos a consultar. Default 1.",
                    "default": 1,
                },
            },
            "required": ["address_query"],
        },
    },
    {
        "name": "normalize_address",
        "description": (
            "Estandariza el formato de una dirección argentina y devuelve "
            "múltiples candidatos ordenados por similarity. Usar cuando el "
            "objetivo es CORREGIR o FORMATEAR una dirección, no validarla. "
            "Ideal para onboarding cuando el usuario escribió mal la dirección."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address_query": {
                    "type": "string",
                    "description": "Dirección argentina en lenguaje natural, posiblemente mal formada.",
                },
                "max_candidates": {
                    "type": "integer",
                    "description": "Máximo de candidatos a devolver. Default 3.",
                    "default": 3,
                },
            },
            "required": ["address_query"],
        },
    },
    {
        "name": "check_coverage",
        "description": (
            "Verifica si un CPA (Código Postal Argentino) tiene cobertura de "
            "despacho logístico. Usar DESPUÉS de validate_address, cuando ya "
            "se tiene un CPA y se necesita saber si el operador puede entregar "
            "en esa zona. No llama a Ubidata — usa lista de zonas cubiertas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cpa": {
                    "type": "string",
                    "description": "Código Postal Argentino de 7 caracteres (ej. 'C1043AAB').",
                },
                "coverage_zones": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Lista de prefijos de CPA cubiertos (ej. ['C1', 'B1600']). "
                        "Si se omite, usa lista demo de CABA y GBA."
                    ),
                },
            },
            "required": ["cpa"],
        },
    },
]

TOOL_HANDLERS: dict[str, Callable] = {
    "validate_address": _handle_validate_address,
    "normalize_address": _handle_normalize_address,
    "check_coverage": _handle_check_coverage,
}
