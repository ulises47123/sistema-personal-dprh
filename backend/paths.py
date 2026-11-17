"""
backend/paths.py
Rutas centrales del sistema.

- RESOURCE_DIR: archivos de solo lectura que vienen con el programa (frontend/, plantillas).
- DATA_DIR: datos que el sistema crea y modifica (base de datos, certificados, backups,
  legajos subidos, logs, llave SSH).

Ejecutando desde el código fuente ambas son la carpeta del proyecto (comportamiento de siempre).
En el ejecutable de Windows los datos van a %LOCALAPPDATA%\\SistemaDPRH: la carpeta del
programa puede no tener permisos de escritura y así el usuario sólo ve el .exe.
DPRH_DATA_DIR permite elegir otra carpeta de datos.
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if getattr(sys, "frozen", False):
    RESOURCE_DIR = getattr(sys, "_MEIPASS", ROOT_DIR)
    DATA_DIR = os.environ.get("DPRH_DATA_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SistemaDPRH"
    )
else:
    RESOURCE_DIR = ROOT_DIR
    DATA_DIR = os.environ.get("DPRH_DATA_DIR") or ROOT_DIR

os.makedirs(DATA_DIR, exist_ok=True)

FRONTEND_DIR = os.path.join(RESOURCE_DIR, "frontend")
