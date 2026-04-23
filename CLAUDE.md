# Address Intelligence Agent Kit

## Descripción del proyecto

Biblioteca de agentes Claude para validación y enriquecimiento de direcciones postales en Argentina. Desarrollada para **Ubidata** (ubidata.com.ar), empresa especializada en inteligencia de datos geográficos.

El kit expone agentes especializados (onboarding, soporte, logística, limpieza de datos) que comparten un conjunto de herramientas comunes para consultar la API de Ubidata y tomar decisiones basadas en la calidad de las coincidencias.

## Cliente: Ubidata

- Sitio: https://ubidata.com.ar
- API base: https://api.ubidata.com.ar
- La API de Ubidata resuelve direcciones en lenguaje natural contra una base de direcciones argentina y devuelve candidatos georreferenciados con un score de similitud.

---

## Convenciones de código

- **Lenguaje**: Python 3.11+
- **Type hints**: obligatorio en todas las funciones y métodos públicos.
- **Docstrings**: estilo Google (Args / Returns / Raises) en todas las funciones y clases públicas.
- **Errores**: explícitos y con contexto — no usar bare `except`, siempre propagar o convertir a excepciones de dominio definidas en `src/`.
- **Formato**: Black + isort (line length 88).
- **Tests**: pytest, un archivo por módulo bajo `tests/`.

---

## API de Ubidata

### Endpoint

```
POST https://api.ubidata.com.ar/api/query/unstructured
```

### Request body

```json
{
  "query": "Av. Corrientes 1234, Buenos Aires",
  "threshold_abs_list": [0.5],
  "thresholds_rel_list": [0.0],
  "max_addresses_to_show_list": [3]
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `query` | str | Dirección en lenguaje natural |
| `threshold_abs_list` | list[float] | Umbral mínimo absoluto de similitud (default 0.5) |
| `thresholds_rel_list` | list[float] | Umbral relativo respecto al mejor candidato (default 0.0) |
| `max_addresses_to_show_list` | list[int] | Máximo de candidatos a devolver (default 3) |

### Response — campos por candidato

| Campo | Tipo | Descripción |
|---|---|---|
| `result_similarity` | float 0–1 | Score de confianza de la coincidencia |
| `CPA` | str | Código Postal Argentino (7 caracteres) — identifica la dirección de forma única |
| `PROVINCIA` | str | Nombre de la provincia |
| `PARTIDO` | str | Partido o departamento |
| `MUNICIPIO` | str | Municipio |
| `LOCALIDAD` | str | Localidad o barrio administrativo |
| `LATITUD` | float | Latitud WGS84 |
| `LONGITUD` | float | Longitud WGS84 |
| `NOM_CALLE_ABR` | str | Nombre de calle abreviado |
| `NOM_CALLE_ABR_C` | str | Nombre de calle abreviado con altura |
| `BAR_NOMBRE` | str | Nombre del barrio |
| `HEIGHT` | int | Altura (número de puerta) |
| `COD_DESDE` | int | Inicio del rango de numeración del tramo |
| `COD_HASTA` | int | Fin del rango de numeración del tramo |

---

## Niveles de riesgo (risk_level)

Basados en `result_similarity` del mejor candidato devuelto:

| Condición | risk_level | Significado |
|---|---|---|
| `result_similarity >= 0.85` | `"low"` | Dirección válida — proceder automáticamente |
| `0.65 <= result_similarity < 0.85` | `"medium"` | Requiere revisión — mostrar candidatos al usuario |
| `result_similarity < 0.65` | `"high"` | Probable error — escalar a humano |
| Sin resultados de la API | `"blocked"` | Dirección irresoluble — escalar a humano |

## Regla de escalación

**`high` y `blocked` siempre escalan a un operador humano.** Los agentes no deben tomar decisiones autónomas con estos niveles. Deben:
1. Registrar el intento con la dirección original.
2. Notificar al usuario que la dirección no pudo validarse.
3. Derivar el caso al flujo de revisión manual.

---

@./docs/address-standards.md
