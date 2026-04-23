# Address Intelligence Agent Kit

Biblioteca de agentes Claude para validación y enriquecimiento de direcciones postales en Argentina, integrada con la API de [Ubidata](https://ubidata.com.ar).

## Agentes disponibles

| Agente | Módulo | Propósito |
|---|---|---|
| Onboarding | `src/agents/onboarding.py` | Validación de direcciones en flujos de alta de clientes |
| Soporte | `src/agents/support.py` | Corrección de direcciones reportadas por usuarios |
| Logística | `src/agents/logistics.py` | Verificación de cobertura de despacho por CPA |
| Limpieza de datos | `src/agents/data_cleaning.py` | Normalización masiva de bases de direcciones |
| Orquestador | `src/agents/orchestrator.py` | Enrutamiento de consultas al agente correcto |

## Instalación

```bash
pip install -r requirements.txt
cp .env.example .env
# Completar .env con las credenciales correspondientes
```

## Estructura del proyecto

```
address-intelligence-kit/
├── CLAUDE.md               # Contexto para Claude Code
├── .mcp.json               # Servidores MCP (Google Drive, GitHub)
├── .env.example            # Variables de entorno requeridas
├── src/
│   ├── config.py           # Configuración centralizada
│   ├── tools/
│   │   └── ubidata.py      # Herramienta de consulta a la API
│   └── agents/             # Agentes especializados
├── tests/                  # Suite de tests (pytest)
└── docs/
    └── address-standards.md  # Estándares de formato de direcciones AR
```

## Variables de entorno

Ver [.env.example](.env.example) para la lista completa de variables requeridas.
