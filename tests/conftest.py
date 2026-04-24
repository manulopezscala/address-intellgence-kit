"""Configuración de pytest: establece variables de entorno requeridas antes de imports."""

import os

# Estas variables son requeridas por src/config.py al importarse.
# Se setean aquí (a nivel de módulo) para que estén disponibles antes de que
# pytest colecte e importe los módulos de src/.
os.environ.setdefault("UBIDATA_BASE_URL", "https://webservice.startb.com.ar/ubidata-webservice")
os.environ.setdefault("UBIDATA_API_KEY", "test-key-placeholder")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-placeholder")
