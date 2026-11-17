"""
Aislamiento de la suite: los tests trabajan sobre una copia temporal de la base
de datos y nunca escriben en recursos_hidricos.db ni en Documentos/Copia de Seguridad.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

_TMP_DIR = tempfile.mkdtemp(prefix="dprh_tests_")
_TMP_DB = os.path.join(_TMP_DIR, "recursos_hidricos_test.db")
_REAL_DB = os.path.join(ROOT_DIR, "recursos_hidricos.db")

if os.path.exists(_REAL_DB):
    src = sqlite3.connect(_REAL_DB)
    dst = sqlite3.connect(_TMP_DB)
    src.backup(dst)
    # Sin "señal verde" no se intenta publicar en GitHub (p. ej. en el atexit de auto_readme_sync)
    dst.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ('github_signal_ok', 'false')")
    dst.commit()
    dst.close()
    src.close()

# Debe definirse antes de importar backend.database
os.environ["DPRH_DB_PATH"] = _TMP_DB
# Logs, docs/, certificados y llaves que generen los tests también van a la carpeta temporal
os.environ["DPRH_DATA_DIR"] = _TMP_DIR

from backend.database import init_db  # noqa: E402
from backend.services import gdrive_service  # noqa: E402

init_db()

_TMP_DOCS = os.path.join(_TMP_DIR, "Documentos_Backup")
os.makedirs(_TMP_DOCS, exist_ok=True)
# Se conserva la función real para los tests que la prueban directamente
gdrive_service._obtener_directorio_documentos_backup_real = gdrive_service.obtener_directorio_documentos_backup
gdrive_service.obtener_directorio_documentos_backup = lambda: _TMP_DOCS
# Nunca escribir en el Google Drive real de quien ejecuta los tests
gdrive_service._obtener_directorio_drive_escritorio_real = gdrive_service.obtener_directorio_drive_escritorio
gdrive_service.obtener_directorio_drive_escritorio = lambda: None


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
