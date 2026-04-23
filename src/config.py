"""Configuración centralizada cargada desde variables de entorno."""

import os

from dotenv import load_dotenv

load_dotenv()

# Umbrales de similitud para clasificación de risk_level
SIMILARITY_LOW_RISK: float = 0.85
SIMILARITY_MEDIUM_RISK: float = 0.65

# Cliente HTTP
UBIDATA_TIMEOUT: int = 10  # segundos

# Credenciales y endpoints
UBIDATA_BASE_URL: str = os.environ["UBIDATA_BASE_URL"]
UBIDATA_API_KEY: str = os.environ["UBIDATA_API_KEY"]
