"""Herramienta de consulta a la API de Ubidata para resolución de direcciones argentinas."""

import re
from collections.abc import Callable
from difflib import SequenceMatcher

import httpx

from src import config

_DEFAULT_COVERAGE_PREFIXES: list[str] = [
    "C1",  # CABA (C + 1xxx)
    "B1000", "B1100", "B1200", "B1300", "B1400",  # GBA norte/oeste
    "B1600", "B1700", "B1800", "B1900",  # GBA sur
]

_UBIDATA_ENDPOINT = "/api/query/unstructured"

# Campos que se conservan en trim_ubidata_output. El resto (~40 % del token count)
# se descarta antes de devolver el resultado al modelo.
_KEEP_FIELDS: frozenset[str] = frozenset(
    {
        "result_similarity",
        "CPA",
        "PROVINCIA",
        "LOCALIDAD",
        "NOM_CALLE_ABR",
        "BAR_NOMBRE",
        "LATITUD",
        "LONGITUD",
        "HEIGHT",
    }
)


# ---------------------------------------------------------------------------
# Helpers públicos
# ---------------------------------------------------------------------------


def trim_ubidata_output(raw: dict) -> dict:
    """Filtra un candidato de Ubidata conservando sólo los campos relevantes.

    Reduce ~40 % del token count del tool result antes de enviarlo al modelo.

    Args:
        raw: Diccionario de un candidato devuelto por la API de Ubidata.

    Returns:
        Subconjunto del diccionario con únicamente los campos en _KEEP_FIELDS.
    """
    return {k: v for k, v in raw.items() if k in _KEEP_FIELDS}


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------


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

    Usa NOM_CALLE_ABR_C cuando está disponible (candidatos sin trim). Para
    candidatos ya trimeados, recurre a NOM_CALLE_ABR + HEIGHT.

    Args:
        result: Diccionario de un candidato devuelto por la API de Ubidata
            (completo o trimeado).

    Returns:
        Cadena con formato "Calle Altura, Localidad, Provincia".
    """
    if "NOM_CALLE_ABR_C" in result:
        street = result["NOM_CALLE_ABR_C"]
    else:
        nom = result.get("NOM_CALLE_ABR", "")
        height = result.get("HEIGHT", "")
        street = f"{nom} {height}".strip()
    return f"{street}, {result['LOCALIDAD']}, {result['PROVINCIA']}"


def _compute_field_confidence(query: str, raw_result: dict) -> dict:
    """Calcula scores de confianza por campo entre la query y el resultado de Ubidata.

    Args:
        query: Dirección original en lenguaje natural.
        raw_result: Candidato devuelto por la API de Ubidata.

    Returns:
        Diccionario con claves street, locality, number y overall (floats 0-1).
    """

    def _ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    street_conf = _ratio(query, raw_result.get("NOM_CALLE_ABR") or "")
    locality_conf = _ratio(query, raw_result.get("LOCALIDAD") or "")

    # Confianza del número: 1.0 si es exacto, decae linealmente hasta 0 en ±100
    nums = re.findall(r"\d+", query)
    height = raw_result.get("HEIGHT")
    if nums and height is not None:
        try:
            diff = abs(int(nums[-1]) - int(height))
            number_conf = max(0.0, 1.0 - diff / 100.0)
        except (ValueError, TypeError):
            number_conf = 0.0
    else:
        number_conf = 0.0

    overall = float(raw_result.get("result_similarity") or 0.0)

    return {
        "street": round(street_conf, 4),
        "locality": round(locality_conf, 4),
        "number": round(number_conf, 4),
        "overall": round(overall, 4),
    }


def _classify_http_error(exc: httpx.HTTPStatusError, query: str) -> dict:
    """Convierte un HTTPStatusError en un dict de error estructurado.

    Clasificación:
      401 / 403 → permission, retryable=False
      4xx restantes → business, retryable=False
      5xx → transient, retryable=True

    Args:
        exc: Excepción HTTP capturada.
        query: Dirección que se intentó consultar.

    Returns:
        Dict con error_type, message, retryable y attempted_query.
    """
    status = exc.response.status_code
    if status in (401, 403):
        return {
            "error_type": "permission",
            "message": str(exc),
            "retryable": False,
            "attempted_query": query,
        }
    if 400 <= status < 500:
        return {
            "error_type": "business",
            "message": str(exc),
            "retryable": False,
            "attempted_query": query,
        }
    return {
        "error_type": "transient",
        "message": str(exc),
        "retryable": True,
        "attempted_query": query,
    }


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
        coordinates, risk_level, confidence_score, field_confidence y
        needs_human_review. En caso de error devuelve un dict con error_type,
        message, retryable y attempted_query.
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
        return _classify_http_error(exc, address_query)
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
            "field_confidence": None,
            "needs_human_review": True,
        }

    best = results[0]
    similarity: float = best["result_similarity"]
    field_conf = _compute_field_confidence(address_query, best)
    needs_review = field_conf["street"] < 0.7 or field_conf["locality"] < 0.8

    return {
        "validated": True,
        "result_similarity": similarity,
        "normalized_address": _build_normalized_string(best),
        "cpa": best.get("CPA"),
        "coordinates": {"lat": best["LATITUD"], "lng": best["LONGITUD"]},
        "risk_level": _derive_risk_level(similarity),
        "confidence_score": similarity,
        "field_confidence": field_conf,
        "needs_human_review": needs_review,
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
        return _classify_http_error(exc, address_query)
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
