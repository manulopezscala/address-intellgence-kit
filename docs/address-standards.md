# Estándares de Direcciones — Argentina

## Formato estándar

```
<Tipo de vía> <Nombre de vía> <Altura>, <Localidad>, <Provincia>
```

**Ejemplos:**
- `Av. Corrientes 1234, Ciudad Autónoma de Buenos Aires, Buenos Aires`
- `Calle San Martín 456, Rosario, Santa Fe`
- `Ruta Nacional 9 Km 1045, Córdoba, Córdoba`

El formato es posicional: siempre `[calle] [número], [localidad], [provincia]`. Omitir cualquiera de estos elementos degrada la resolución de la API.

---

## Abreviaciones comunes

| Abreviación | Forma expandida |
|---|---|
| `Av.` / `Avda.` | Avenida |
| `Bv.` / `Blvd.` | Boulevard |
| `Pje.` | Pasaje |
| `Cno.` | Camino |
| `Rta.` / `Rn.` | Ruta / Ruta Nacional |
| `Rp.` | Ruta Provincial |
| `Gral.` | General |
| `Pte.` | Presidente |
| `Dr.` | Doctor |
| `Ing.` | Ingeniero |
| `CABA` | Ciudad Autónoma de Buenos Aires |
| `GBA` | Gran Buenos Aires |
| `PBA` | Provincia de Buenos Aires |

La API de Ubidata acepta tanto las formas abreviadas como las expandidas, pero las formas expandidas suelen producir scores de similitud más altos.

---

## El CPA (Código Postal Argentino)

El **CPA** es el código postal argentino de 7 caracteres introducido en 1999 para reemplazar al código postal de 4 dígitos. Estructura:

```
[Letra de provincia] [4 dígitos de CP tradicional] [3 letras de cara del edificio]
```

Ejemplo: `C1043AAB` → CABA, CP 1043, cara B del edificio.

### Uso para validar cobertura de despacho

El CPA es el identificador granular que usan los operadores logísticos para determinar cobertura:

1. La API de Ubidata devuelve el `CPA` del candidato resuelto.
2. El agente de logística consulta la tabla de cobertura del cliente contra ese `CPA`.
3. Si el `CPA` está fuera de cobertura, se informa al usuario antes de aceptar el pedido.
4. CPAs con `risk_level = "high"` o `"blocked"` no deben usarse para decisiones de despacho — escalar a humano.

---

## Ejemplos de direcciones

### Bien formadas ✓

| Dirección | Por qué es correcta |
|---|---|
| `Av. Santa Fe 2350, Buenos Aires, Buenos Aires` | Tipo de vía + nombre + altura + localidad + provincia |
| `Calle Belgrano 789, San Miguel de Tucumán, Tucumán` | Altura presente, localidad y provincia completas |
| `Ruta Nacional 40 Km 2100, Mendoza, Mendoza` | Formato de ruta con kilómetro como altura |

### Mal formadas ✗

| Dirección | Problema |
|---|---|
| `Corrientes 1234` | Sin localidad ni provincia — alta ambigüedad |
| `Buenos Aires, frente al banco` | Sin nombre de calle ni altura |
| `Av. 9 de Julio s/n, CABA` | Altura `s/n` (sin número) no es resoluble geográficamente |
