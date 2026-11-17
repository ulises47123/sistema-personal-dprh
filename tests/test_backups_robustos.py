"""
Robustez de las copias de seguridad: carpeta Documentos/Copia de Seguridad, copias consistentes
con la base en uso (modo WAL), verificación de integridad, rotación y restauración segura.
"""
import os
import sqlite3
import tempfile
import shutil
from unittest.mock import patch

import pytest

from backend.database import DB_PATH, init_db, get_db_connection
from backend.services import gdrive_service


@pytest.fixture
def carpeta_docs():
    """Documentos/Copia de Seguridad temporal y sin carpeta de Google Drive configurada."""
    init_db()
    tmp = tempfile.mkdtemp(prefix="dprh_docs_")
    gdrive_service.guardar_config_gdrive({"gdrive_folder_path": "", "gdrive_connected": "0", "gdrive_auto_backup": "1"})
    with patch.object(gdrive_service, "obtener_directorio_documentos_backup", lambda: tmp):
        yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _valor_marca(ruta_db):
    conn = sqlite3.connect(ruta_db)
    try:
        row = conn.execute("SELECT valor FROM configuracion WHERE clave = 'test_marca_backup'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _escribir_marca(valor):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES ('test_marca_backup', ?)", (valor,))
    conn.commit()
    conn.close()


def test_sin_drive_las_copias_van_a_documentos(carpeta_docs):
    res = gdrive_service.realizar_backup_gdrive(usuario="Test")
    assert res["success"] is True
    assert os.path.isfile(os.path.join(carpeta_docs, res["backup_creado"]))
    assert res["carpeta"] == carpeta_docs


def test_ruta_drive_de_otro_equipo_no_se_recrea(carpeta_docs):
    """Una ruta guardada en la base que no existe en este equipo se ignora (no se crea)."""
    ruta_ajena = os.path.join(tempfile.gettempdir(), "dprh_no_existe_otro_equipo", "Drive")
    gdrive_service.guardar_config_gdrive({"gdrive_folder_path": ruta_ajena})
    res = gdrive_service.realizar_backup_gdrive(usuario="Test")
    assert res["carpeta"] == carpeta_docs
    assert not os.path.exists(ruta_ajena)


def test_copia_incluye_cambios_pendientes_en_wal(carpeta_docs):
    """La copia debe incluir datos recién escritos aunque sigan en el archivo -wal."""
    lector = get_db_connection()  # conexión abierta: impide que el WAL se vuelque solo
    try:
        _escribir_marca("valor_reciente_wal")
        res = gdrive_service.realizar_backup_gdrive(usuario="Test")
    finally:
        lector.close()
    copia = os.path.join(carpeta_docs, res["backup_creado"])
    assert _valor_marca(copia) == "valor_reciente_wal"
    # Copia autocontenida: sin archivos -wal/-shm al lado
    assert not os.path.exists(copia + "-wal")
    assert gdrive_service._verificar_integridad(copia)


def test_rotacion_no_borra_otros_archivos(carpeta_docs):
    ajeno = os.path.join(carpeta_docs, "mis_datos_personales.db")
    with open(ajeno, "wb") as f:
        f.write(b"no tocar")
    for i in range(5):
        gdrive_service.realizar_backup_gdrive(usuario=f"Test_{i}")
    copias = [f for f in os.listdir(carpeta_docs) if f.startswith(gdrive_service.PREFIJO_BACKUP)]
    assert len(copias) == 3
    assert os.path.exists(ajeno)


def test_replica_en_documentos_cuando_hay_carpeta_drive(carpeta_docs):
    drive = tempfile.mkdtemp(prefix="dprh_drive_")
    try:
        gdrive_service.guardar_config_gdrive({"gdrive_folder_path": drive})
        res = gdrive_service.realizar_backup_gdrive(usuario="Test")
        assert res["carpeta"] == drive
        assert os.path.isfile(os.path.join(drive, res["backup_creado"]))
        assert os.path.isfile(os.path.join(carpeta_docs, res["backup_creado"]))
    finally:
        gdrive_service.guardar_config_gdrive({"gdrive_folder_path": ""})
        shutil.rmtree(drive, ignore_errors=True)


def test_restaurar_recupera_datos(carpeta_docs):
    _escribir_marca("antes")
    res = gdrive_service.realizar_backup_gdrive(usuario="Test")
    _escribir_marca("despues")
    gdrive_service.restaurar_backup_gdrive(res["backup_creado"], usuario="Test")
    assert _valor_marca(DB_PATH) == "antes"
    conn = get_db_connection()
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_restaurar_rechaza_copia_danada(carpeta_docs):
    _escribir_marca("intacto")
    danada = gdrive_service.PREFIJO_BACKUP + "20990101_000000_000000.db"
    with open(os.path.join(carpeta_docs, danada), "wb") as f:
        f.write(b"esto no es una base sqlite" * 100)
    with pytest.raises(ValueError):
        gdrive_service.restaurar_backup_gdrive(danada, usuario="Test")
    assert _valor_marca(DB_PATH) == "intacto"


def test_restaurar_no_sale_de_la_carpeta(carpeta_docs):
    with pytest.raises(FileNotFoundError):
        gdrive_service.restaurar_backup_gdrive("..\\..\\recursos_hidricos.db", usuario="Test")


def test_respaldo_automatico_sin_google_drive(carpeta_docs):
    """En una instalación nueva (Drive sin conectar) igual se hacen copias automáticas."""
    gdrive_service.guardar_config_gdrive({"gdrive_connected": "0", "gdrive_last_backup": ""})
    gdrive_service._verificar_y_ejecutar_backup_si_corresponde()
    assert len(gdrive_service.listar_backups_gdrive()) == 1


def test_documentos_no_escribible_usa_carpeta_de_datos():
    tmp = tempfile.mkdtemp(prefix="dprh_datos_")
    try:
        bloqueo = os.path.join(tmp, "archivo_no_carpeta")
        with open(bloqueo, "w") as f:
            f.write("x")
        # "Documentos" apunta a un archivo: no se puede crear la carpeta adentro
        with patch.object(gdrive_service, "_carpeta_documentos_usuario", lambda: bloqueo), \
             patch.object(gdrive_service, "DATA_DIR", tmp):
            destino = gdrive_service._obtener_directorio_documentos_backup_real()
        assert destino == os.path.join(tmp, gdrive_service.CARPETA_DOCUMENTOS)
        assert os.path.isdir(destino)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_carpeta_documentos_real_existe():
    assert os.path.isdir(gdrive_service._carpeta_documentos_usuario())


# ───────── Google Drive para escritorio ─────────

def test_copia_se_replica_en_google_drive_escritorio(carpeta_docs):
    drive = tempfile.mkdtemp(prefix="dprh_mi_unidad_")
    try:
        with patch.object(gdrive_service, "obtener_directorio_drive_escritorio", lambda: drive):
            for i in range(4):
                res = gdrive_service.realizar_backup_gdrive(usuario=f"Test_{i}")
        assert res["carpeta_drive"] == drive
        assert os.path.isfile(os.path.join(drive, res["backup_creado"]))
        assert gdrive_service._verificar_integridad(os.path.join(drive, res["backup_creado"]))
        # La rotación también se aplica en Drive
        assert len([f for f in os.listdir(drive) if f.startswith(gdrive_service.PREFIJO_BACKUP)]) == 3
    finally:
        shutil.rmtree(drive, ignore_errors=True)


def test_falla_de_drive_no_impide_el_respaldo(carpeta_docs):
    def _drive_roto():
        raise OSError("Google Drive sin conexión")
    with patch.object(gdrive_service, "obtener_directorio_drive_escritorio", _drive_roto):
        res = gdrive_service.realizar_backup_gdrive(usuario="Test")
    assert res["success"] is True
    assert res["carpeta_drive"] is None
    assert os.path.isfile(os.path.join(carpeta_docs, res["backup_creado"]))


def test_estado_sin_drive_ofrece_enlace_de_descarga():
    with patch.object(gdrive_service, "obtener_directorio_drive_escritorio", lambda: None), \
         patch.object(gdrive_service, "drive_escritorio_instalado", lambda: False):
        estado = gdrive_service.estado_drive_escritorio()
    assert estado["activo"] is False and estado["instalado"] is False
    assert estado["url_descarga"].startswith("https://www.google.com/")
    assert "drive/download" in estado["url_descarga"]


def test_estado_drive_instalado_sin_sesion():
    with patch.object(gdrive_service, "obtener_directorio_drive_escritorio", lambda: None), \
         patch.object(gdrive_service, "drive_escritorio_instalado", lambda: True):
        estado = gdrive_service.estado_drive_escritorio()
    assert estado["instalado"] is True and estado["activo"] is False
    assert "inicie sesión" in estado["mensaje"]


def test_detecta_mi_unidad_en_modo_espejo():
    perfil = tempfile.mkdtemp(prefix="dprh_perfil_")
    try:
        os.makedirs(os.path.join(perfil, "Mi unidad"))
        gdrive_service._cache_drive["momento"] = 0.0
        with patch.object(gdrive_service, "_letras_candidatas_drive", lambda: []), \
             patch.dict(os.environ, {"USERPROFILE": perfil}):
            if os.name == "nt":
                assert gdrive_service.detectar_mi_unidad_drive() == os.path.join(perfil, "Mi unidad")
                destino = gdrive_service._obtener_directorio_drive_escritorio_real()
                assert destino == os.path.join(perfil, "Mi unidad", gdrive_service.CARPETA_DRIVE)
                assert os.path.isdir(destino)
    finally:
        gdrive_service._cache_drive["momento"] = 0.0
        shutil.rmtree(perfil, ignore_errors=True)


def test_config_incluye_estado_drive(carpeta_docs):
    cfg = gdrive_service.obtener_config_gdrive()
    assert "drive_escritorio" in cfg
    assert cfg["drive_escritorio"]["url_descarga"] == gdrive_service.URL_DESCARGA_DRIVE
    assert cfg["carpeta_efectiva"] == carpeta_docs
