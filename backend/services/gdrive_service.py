import os
import json
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from backend.database import DB_PATH, get_db_connection
from backend.services.auditoria_service import registrar_auditoria

from backend.paths import DATA_DIR

# Carpeta visible para el usuario dentro de "Documentos"
CARPETA_DOCUMENTOS = "Copia de Seguridad"
PREFIJO_BACKUP = "recursos_hidricos_backup_"
MAX_COPIAS = 3
# Si un respaldo automático falla, se reintenta más tarde en lugar de cada 30 segundos
ESPERA_TRAS_ERROR_SEG = 600

# Google Drive para escritorio (app oficial): si está instalada, las copias también se guardan
# en "Mi unidad" y la app las sube sola a la nube
URL_DESCARGA_DRIVE = "https://www.google.com/intl/es-419/drive/download/"
CARPETA_DRIVE = "Copia de Seguridad DPRH"
# Nombre de la carpeta "Mi unidad" según el idioma de la cuenta de Google
NOMBRES_MI_UNIDAD = ("Mi unidad", "My Drive", "Meu Drive", "Mon Drive", "Meine Ablage", "Il mio Drive")
_CACHE_DRIVE_SEG = 60
_cache_drive = {"momento": 0.0, "carpeta": None}

# Una copia a la vez (panel, API y respaldo automático pueden coincidir)
_backup_lock = threading.Lock()

# Guardar estado de Google Drive
GDRIVE_STATE = {
    "timer_thread": None,
    "interval_hours": 5,
    "running": False
}

def _carpeta_documentos_usuario() -> str:
    """Carpeta "Documentos" real del usuario (respeta la redirección de Windows/OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_PERSONAL = 5 (Documentos)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return buf.value
        except Exception:
            pass
    userprofile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for nombre in ("Documents", "Documentos"):
        ruta = os.path.join(userprofile, nombre)
        if os.path.isdir(ruta):
            return ruta
    return os.path.join(userprofile, "Documents")

def obtener_directorio_documentos_backup() -> str:
    """
    Carpeta Documentos\\Copia de Seguridad. Si no se puede crear (permisos, OneDrive sin
    sincronizar...), se usa una carpeta equivalente dentro de los datos del sistema.
    """
    for base in (_carpeta_documentos_usuario(), DATA_DIR):
        destino = os.path.join(base, CARPETA_DOCUMENTOS)
        try:
            os.makedirs(destino, exist_ok=True)
            if os.access(destino, os.W_OK):
                return destino
        except OSError:
            continue
    raise OSError("No hay ninguna carpeta disponible para guardar las copias de seguridad.")

def drive_escritorio_instalado() -> bool:
    """True si Google Drive para escritorio está instalado en este equipo."""
    if os.name != "nt":
        return False
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), r"C:\Program Files"):
        if base and os.path.isdir(os.path.join(base, "Google", "Drive File Stream")):
            return True
    try:
        import winreg
        for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                winreg.CloseKey(winreg.OpenKey(raiz, r"Software\Google\DriveFS"))
                return True
            except OSError:
                continue
    except Exception:
        pass
    return False

def _letras_candidatas_drive() -> list:
    """Letras de unidad donde puede estar montado Google Drive (primero la configurada, por defecto G)."""
    letras = []
    try:
        import winreg
        for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for clave in (r"Software\Policies\Google\DriveFS", r"Software\Google\DriveFS"):
                try:
                    with winreg.OpenKey(raiz, clave) as k:
                        valor, _ = winreg.QueryValueEx(k, "DefaultMountPoint")
                    if valor and str(valor)[0].isalpha():
                        letras.append(str(valor)[0].upper())
                except OSError:
                    continue
    except Exception:
        pass
    letras.append("G")
    letras += [chr(c) for c in range(ord("D"), ord("Z") + 1)]

    try:
        import ctypes
        existentes = ctypes.windll.kernel32.GetLogicalDrives()
        get_tipo = ctypes.windll.kernel32.GetDriveTypeW
    except Exception:
        return []

    resultado = []
    for letra in letras:
        if letra in resultado or not existentes & (1 << (ord(letra) - ord("A"))):
            continue
        # Se omiten unidades de red (pueden tardar si están desconectadas) y lectoras de CD
        if get_tipo(f"{letra}:\\") in (4, 5):
            continue
        resultado.append(letra)
    return resultado

def detectar_mi_unidad_drive():
    """
    Carpeta "Mi unidad" de Google Drive para escritorio (unidad virtual G: o modo espejo en
    el perfil del usuario). None si no está instalado o no se inició sesión.
    """
    if os.name != "nt":
        return None
    ahora = time.time()
    if ahora - _cache_drive["momento"] < _CACHE_DRIVE_SEG:
        return _cache_drive["carpeta"]

    encontrada = None
    try:
        for letra in _letras_candidatas_drive():
            for nombre in NOMBRES_MI_UNIDAD:
                ruta = f"{letra}:\\{nombre}"
                if os.path.isdir(ruta):
                    encontrada = ruta
                    break
            if encontrada:
                break
        if not encontrada:
            perfil = os.environ.get("USERPROFILE") or os.path.expanduser("~")
            for nombre in NOMBRES_MI_UNIDAD:
                ruta = os.path.join(perfil, nombre)
                if os.path.isdir(ruta):
                    encontrada = ruta
                    break
    except Exception:
        encontrada = None

    _cache_drive["momento"] = ahora
    _cache_drive["carpeta"] = encontrada
    return encontrada

def obtener_directorio_drive_escritorio():
    """Mi unidad\\Copia de Seguridad DPRH si Google Drive para escritorio está disponible; si no, None."""
    mi_unidad = detectar_mi_unidad_drive()
    if not mi_unidad:
        return None
    destino = os.path.join(mi_unidad, CARPETA_DRIVE)
    try:
        os.makedirs(destino, exist_ok=True)
        return destino if os.access(destino, os.W_OK) else None
    except OSError:
        return None

def estado_drive_escritorio() -> dict:
    """Estado de Google Drive para escritorio para mostrar en el panel y en la web."""
    carpeta = obtener_directorio_drive_escritorio()
    instalado = bool(carpeta) or drive_escritorio_instalado()
    if carpeta:
        mensaje = f"Google Drive para escritorio detectado: las copias también se guardan en {carpeta} y se suben solas a Google Drive."
    elif instalado:
        mensaje = "Google Drive para escritorio está instalado pero no se encontró \"Mi unidad\": abra Google Drive e inicie sesión con su cuenta."
    else:
        mensaje = "Google Drive para escritorio no está instalado en la PC del servidor: las copias quedan sólo en ese equipo. Instálelo para que también se guarden en la nube."
    return {
        "instalado": instalado,
        "activo": bool(carpeta),
        "carpeta": carpeta,
        "mensaje": mensaje,
        "url_descarga": URL_DESCARGA_DRIVE
    }

def obtener_directorio_backups():
    """
    Carpeta principal de copias: la carpeta sincronizada configurada (p. ej. Google Drive)
    si existe en este equipo; si no, Documentos\\Copia de Seguridad.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT valor FROM configuracion WHERE clave = 'gdrive_folder_path'")
        row = cursor.fetchone()
        conn.close()
        if row and row[0] and row[0].strip():
            target = row[0].strip()
            # Sólo si ya existe: una ruta de otro equipo (copiada con la base) no debe recrearse aquí
            if os.path.isdir(target) and os.access(target, os.W_OK):
                return target
    except Exception:
        pass
    return obtener_directorio_documentos_backup()

def obtener_config_gdrive():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT clave, valor FROM configuracion
        WHERE clave IN (
            'gdrive_connected', 'gdrive_user_email', 'gdrive_folder_id',
            'gdrive_folder_name', 'gdrive_auto_backup', 'gdrive_last_backup',
            'gdrive_credentials_json', 'gdrive_folder_path'
        )
    """)
    filas = dict(cursor.fetchall())
    conn.close()

    try:
        carpeta_efectiva = obtener_directorio_backups()
    except OSError:
        carpeta_efectiva = filas.get("gdrive_folder_path", "")

    return {
        "drive_escritorio": estado_drive_escritorio(),
        "carpeta_efectiva": carpeta_efectiva,
        "conectado": filas.get("gdrive_connected", "0") == "1",
        "email": filas.get("gdrive_user_email", ""),
        "folder_id": filas.get("gdrive_folder_id", "root"),
        "folder_name": filas.get("gdrive_folder_name", "Recursos Hidricos Backups"),
        "folder_path": filas.get("gdrive_folder_path", ""),
        "auto_backup": filas.get("gdrive_auto_backup", "1") == "1",
        "last_backup": filas.get("gdrive_last_backup", ""),
        "has_credentials": bool(filas.get("gdrive_credentials_json", ""))
    }

def guardar_config_gdrive(data: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    for k, v in data.items():
        cursor.execute("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()

def autenticar_simulado_o_credenciales(email: str, credentials_json: str = "", folder_path: str = ""):
    """Guarda la conexión de Google Drive y la ruta de sincronización."""
    config = {
        "gdrive_connected": "1",
        "gdrive_user_email": email.strip() or "admin.recursoshidricos@gmail.com",
        "gdrive_folder_name": "Recursos Hidricos Backups",
        "gdrive_auto_backup": "1"
    }
    if folder_path and folder_path.strip():
        config["gdrive_folder_path"] = folder_path.strip()
    if credentials_json:
        config["gdrive_credentials_json"] = credentials_json
    guardar_config_gdrive(config)
    registrar_auditoria("CONFIGURACION", "gdrive", None, f"Google Drive conectado con cuenta {config['gdrive_user_email']}")
    return {"success": True, "email": config["gdrive_user_email"]}

def desconectar_gdrive():
    guardar_config_gdrive({
        "gdrive_connected": "0",
        "gdrive_user_email": "",
        "gdrive_credentials_json": ""
    })
    registrar_auditoria("CONFIGURACION", "gdrive", None, "Google Drive desconectado")
    return {"success": True}

def _listar_copias(directorio: str):
    """Copias generadas por el sistema en una carpeta, de la más reciente a la más antigua."""
    if not os.path.isdir(directorio):
        return []

    archivos = []
    for f in os.listdir(directorio):
        # Sólo las copias del sistema: nunca tocar otros .db que el usuario tenga en la carpeta
        if f.startswith(PREFIJO_BACKUP) and f.endswith(".db"):
            ruta = os.path.join(directorio, f)
            try:
                stat = os.stat(ruta)
            except OSError:
                continue
            archivos.append({
                "nombre": f,
                "ruta": ruta,
                "tamano": stat.st_size,
                "fecha_modificacion": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": stat.st_mtime
            })

    # Ordenar de más reciente a más antiguo (por mtime y por nombre descendente)
    archivos.sort(key=lambda x: (x["timestamp"], x["nombre"]), reverse=True)
    return archivos

def listar_backups_gdrive():
    """
    Retorna la lista de backups disponibles manteniendo política de 3 copias.
    """
    try:
        return _listar_copias(obtener_directorio_backups())
    except OSError:
        return []

def _rotar_copias(directorio: str) -> list:
    """Mantiene como máximo MAX_COPIAS copias en la carpeta (elimina las más antiguas)."""
    eliminados = []
    for b in _listar_copias(directorio)[MAX_COPIAS:]:
        try:
            os.remove(b["ruta"])
            eliminados.append(b["nombre"])
        except Exception as e:
            print(f"Error eliminando backup antiguo: {e}")
    return eliminados

def _uri_solo_lectura(ruta_db: str) -> str:
    """URI de SQLite en solo lectura (as_uri codifica espacios como los de "Copia de Seguridad")."""
    return Path(os.path.abspath(ruta_db)).as_uri() + "?mode=ro"

def _verificar_integridad(ruta_db: str) -> bool:
    """True si el archivo es una base SQLite íntegra."""
    try:
        conn = sqlite3.connect(_uri_solo_lectura(ruta_db), uri=True, timeout=30)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False

def _copiar_sqlite_seguro(origen: str, destino: str) -> None:
    """
    Copia consistente de una base SQLite en uso (API de backup de SQLite, incluye lo pendiente
    en el archivo -wal). Se escribe en un temporal, se verifica y recién entonces reemplaza al
    destino: un corte a mitad de copia nunca deja una copia dañada.
    """
    temporal = destino + ".tmp"
    try:
        src = sqlite3.connect(origen, timeout=30)
        try:
            dst = sqlite3.connect(temporal)
            try:
                src.backup(dst)
                # Archivo autocontenido (sin -wal/-shm al lado de la copia)
                dst.execute("PRAGMA journal_mode = DELETE")
            finally:
                dst.close()
        finally:
            src.close()
        if not _verificar_integridad(temporal):
            raise RuntimeError("La copia generada no superó la verificación de integridad.")
        os.replace(temporal, destino)
    finally:
        if os.path.exists(temporal):
            try:
                os.remove(temporal)
            except OSError:
                pass

def _copiar_archivo_seguro(origen: str, destino: str) -> None:
    """Copia un archivo ya verificado a otra carpeta sin dejar copias a medias."""
    temporal = destino + ".tmp"
    try:
        shutil.copy2(origen, temporal)
        os.replace(temporal, destino)
    finally:
        if os.path.exists(temporal):
            try:
                os.remove(temporal)
            except OSError:
                pass

def _replicar_copia(copia: str, carpeta_principal: str, obtener_carpeta, descripcion: str):
    """Copia una copia ya verificada a otra carpeta (con su rotación). Nunca interrumpe el respaldo."""
    try:
        carpeta = obtener_carpeta()
        if not carpeta:
            return None
        if os.path.normcase(os.path.abspath(carpeta)) != os.path.normcase(os.path.abspath(carpeta_principal)):
            _copiar_archivo_seguro(copia, os.path.join(carpeta, os.path.basename(copia)))
            _rotar_copias(carpeta)
        return carpeta
    except Exception as e:
        print(f"[Backup Aviso] No se pudo copiar a {descripcion}: {e}")
        return None

def realizar_backup_gdrive(usuario: str = "Admin"):
    """
    Crea una copia verificada de la base de datos en la carpeta principal de copias y la replica
    en Documentos\\Copia de Seguridad y en Google Drive para escritorio (si está instalado).
    Aplica rotación de máximo 3 copias en cada carpeta.
    """
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"No se encontró la base de datos en {DB_PATH}")

    with _backup_lock:
        dir_backups = obtener_directorio_backups()
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_nombre = f"{PREFIJO_BACKUP}{timestamp_str}.db"
        destino = os.path.join(dir_backups, backup_nombre)

        _copiar_sqlite_seguro(DB_PATH, destino)
        eliminados = _rotar_copias(dir_backups)

        # Réplicas: Documentos\Copia de Seguridad y, si está instalado, Google Drive para escritorio
        copia_documentos = _replicar_copia(destino, dir_backups, obtener_directorio_documentos_backup, f"Documentos\\{CARPETA_DOCUMENTOS}")
        copia_drive = _replicar_copia(destino, dir_backups, obtener_directorio_drive_escritorio, "Google Drive")

        # Guardar último backup en configuración
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        guardar_config_gdrive({"gdrive_last_backup": now_str})
        registrar_auditoria("BACKUP", "database", None, f"Backup generado: {backup_nombre}. Rotación aplicó sobre {len(eliminados)} archivos.", usuario)

        return {
            "success": True,
            "backup_creado": backup_nombre,
            "carpeta": dir_backups,
            "carpeta_documentos": copia_documentos,
            "carpeta_drive": copia_drive,
            "fecha": now_str,
            "total_backups_vigentes": len(_listar_copias(dir_backups)),
            "eliminados_rotacion": eliminados
        }

def restaurar_backup_gdrive(backup_nombre: str, usuario: str = "Admin"):
    """
    Restaura la base de datos a partir de una de las copias existentes.
    """
    dir_backups = obtener_directorio_backups()
    # Sólo el nombre del archivo: evita restaurar desde rutas fuera de la carpeta de copias
    nombre = os.path.basename(backup_nombre or "")
    origen = os.path.join(dir_backups, nombre)
    if not nombre or not os.path.isfile(origen):
        raise FileNotFoundError(f"El archivo de respaldo {backup_nombre} no existe.")
    if not _verificar_integridad(origen):
        raise ValueError(f"La copia {nombre} está dañada o no es una base de datos válida; no se restauró nada.")

    with _backup_lock:
        # Crear backup de rescate antes de sobreescribir (aunque la base actual esté dañada)
        rescate = f"{DB_PATH}.previo_restauracion.bak"
        try:
            _copiar_sqlite_seguro(DB_PATH, rescate)
        except Exception:
            try:
                shutil.copy2(DB_PATH, rescate)
            except Exception:
                pass

        # Restaurar con la API de backup de SQLite sobre la base en uso: respeta el modo WAL y
        # las conexiones abiertas (copiar el archivo encima podría corromperla)
        src = sqlite3.connect(_uri_solo_lectura(origen), uri=True, timeout=30)
        try:
            dst = sqlite3.connect(DB_PATH, timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    registrar_auditoria("RESTAURAR", "database", None, f"Base de datos restaurada desde copia {nombre}", usuario)
    return {"success": True, "mensaje": f"Base de datos restaurada exitosamente desde {nombre}"}

def _verificar_y_ejecutar_backup_si_corresponde():
    cfg = obtener_config_gdrive()
    # Las copias locales (Documentos\Copia de Seguridad) no dependen de tener Google Drive conectado
    if not cfg.get("auto_backup"):
        return

    last_backup_str = cfg.get("last_backup", "")
    debe_ejecutar = False
    if not last_backup_str:
        debe_ejecutar = True
    else:
        try:
            last_dt = datetime.strptime(last_backup_str, "%Y-%m-%d %H:%M:%S")
            delta = datetime.now() - last_dt
            # Un reloj atrasado (fecha futura) tampoco debe impedir los respaldos
            if delta.total_seconds() >= (GDRIVE_STATE["interval_hours"] * 3600) or delta.total_seconds() < 0:
                debe_ejecutar = True
        except Exception:
            debe_ejecutar = True

    if debe_ejecutar:
        print("[AutoBackup] Ejecutando respaldo automático (intervalo 5h cumplido)...")
        realizar_backup_gdrive(usuario="Sistema_Programado_5h")

def _bucle_backup_automatico():
    """Ejecuta un respaldo cada 5 horas si está habilitado y al iniciar si correspondiera."""
    while GDRIVE_STATE["running"]:
        espera = 30
        try:
            _verificar_y_ejecutar_backup_si_corresponde()
        except Exception as e:
            print(f"[AutoBackup Error] {e}")
            espera = ESPERA_TRAS_ERROR_SEG
        # Revisar cada 30 segundos si se cumplieron las 5 horas
        for _ in range(espera):
            if not GDRIVE_STATE["running"]:
                break
            time.sleep(1)

def iniciar_demonio_backup():
    if not GDRIVE_STATE["running"]:
        GDRIVE_STATE["running"] = True
        t = threading.Thread(target=_bucle_backup_automatico, daemon=True)
        t.start()
        GDRIVE_STATE["timer_thread"] = t
