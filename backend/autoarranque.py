"""
backend/autoarranque.py
Inicio automático con Windows al iniciar sesión, por dos caminos independientes (ninguno requiere
Administrador):
  1. Registro: HKCU\\...\\Run
  2. Tarea programada "SistemaDPRH - Inicio automatico" (al iniciar sesión de este usuario)
Si un programa de limpieza o Windows deshabilita uno, el otro arranca igual el sistema; la segunda
ejecución en segundo plano se cierra sola (instancia única).
La preferencia del usuario se guarda en DATA_DIR y reparar() la vuelve a aplicar en cada inicio.
Única entrada del sistema, usada tanto por el panel Tkinter como por la API web.
"""

import os
import subprocess
import sys
from xml.sax.saxutils import escape

from backend.paths import DATA_DIR

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Donde el Administrador de tareas marca un programa de inicio como deshabilitado
STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
NOMBRE_VALOR = "RecursosHidricosDPRH"
# Nombre usado por versiones anteriores desde la API web: se elimina para no arrancar dos veces
NOMBRE_VALOR_ANTERIOR = "GestionPersonalRecursosHidricos"
NOMBRE_TAREA = "SistemaDPRH - Inicio automatico"
ARCHIVO_PREFERENCIA = os.path.join(DATA_DIR, "autoarranque_preferencia.txt")

_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def programa_y_argumentos():
    """(ejecutable, argumentos) que se lanzan al iniciar sesión (servidor en segundo plano)."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--hidden"
    launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_launcher.py")
    # pythonw evita la ventana de consola al iniciar sesión
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (pythonw if os.path.exists(pythonw) else sys.executable), f'"{launcher}" --hidden'


def comando_arranque() -> str:
    programa, argumentos = programa_y_argumentos()
    return f'"{programa}" {argumentos}'


# ── Preferencia ───────────────────────────────────────────────

def _leer_preferencia():
    try:
        with open(ARCHIVO_PREFERENCIA, "r", encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return None


def _guardar_preferencia(activar: bool) -> None:
    try:
        with open(ARCHIVO_PREFERENCIA, "w", encoding="utf-8") as f:
            f.write("1" if activar else "0")
    except OSError as e:
        print(f"[Autoarranque] No se pudo guardar la preferencia: {e}")


# ── Registro (HKCU\...\Run) ─────────────────────────────────────

def _run_activo() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, NOMBRE_VALOR)
            return bool(val)
    except OSError:
        return False


def _configurar_run(activar: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, NOMBRE_VALOR_ANTERIOR)
        except FileNotFoundError:
            pass
        if activar:
            winreg.SetValueEx(key, NOMBRE_VALOR, 0, winreg.REG_SZ, comando_arranque())
        else:
            try:
                winreg.DeleteValue(key, NOMBRE_VALOR)
            except FileNotFoundError:
                pass
    # Sin valor en StartupApproved el Administrador de tareas lo considera habilitado
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, NOMBRE_VALOR)
    except OSError:
        pass


# ── Tarea programada ───────────────────────────────────────────

def xml_tarea(programa: str, argumentos: str, usuario: str) -> str:
    """
    Tarea "al iniciar sesión" solo para este usuario. Definida por XML porque
    schtasks /SC ONLOGON exige Administrador; con el usuario explícito no hace falta.
    """
    usuario = escape(usuario)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Inicia el Sistema DPRH (servidor de Recursos Hidricos) al iniciar sesion en Windows.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{usuario}</UserId>
      <Delay>PT45S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{usuario}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(programa)}</Command>
      <Arguments>{escape(argumentos)}</Arguments>
      <WorkingDirectory>{escape(os.path.dirname(programa))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _usuario_actual() -> str:
    dominio = os.environ.get("USERDOMAIN", "")
    usuario = os.environ.get("USERNAME", "")
    return f"{dominio}\\{usuario}" if dominio else usuario


def _tarea_existe() -> bool:
    try:
        res = subprocess.run(["schtasks", "/Query", "/TN", NOMBRE_TAREA], capture_output=True, creationflags=_SIN_VENTANA)
        return res.returncode == 0
    except OSError:
        return False


def _crear_tarea() -> bool:
    programa, argumentos = programa_y_argumentos()
    ruta_xml = os.path.join(DATA_DIR, "tarea_inicio_automatico.xml")
    try:
        with open(ruta_xml, "w", encoding="utf-16") as f:
            f.write(xml_tarea(programa, argumentos, _usuario_actual()))
        res = subprocess.run(["schtasks", "/Create", "/TN", NOMBRE_TAREA, "/XML", ruta_xml, "/F"],
                             capture_output=True, creationflags=_SIN_VENTANA)
        if res.returncode != 0:
            print(f"[Autoarranque] No se pudo crear la tarea programada: {res.stderr.decode('mbcs', 'ignore').strip()}")
        return res.returncode == 0
    except OSError as e:
        print(f"[Autoarranque] No se pudo crear la tarea programada: {e}")
        return False
    finally:
        try:
            os.remove(ruta_xml)
        except OSError:
            pass


def _borrar_tarea() -> None:
    try:
        subprocess.run(["schtasks", "/Delete", "/TN", NOMBRE_TAREA, "/F"], capture_output=True, creationflags=_SIN_VENTANA)
    except OSError:
        pass


# ── API pública ───────────────────────────────────────────────

def esta_activo() -> bool:
    if os.name != "nt":
        return False
    return _run_activo() or _tarea_existe()


def configurar(activar: bool) -> None:
    """Activa o desactiva el inicio automático. Lanza OSError si el registro no es accesible."""
    if os.name != "nt":
        return
    _guardar_preferencia(activar)
    _configurar_run(activar)
    # La tarea se recrea siempre: así apunta al ejecutable actual (p. ej. tras reinstalar en otra carpeta)
    if activar:
        _crear_tarea()
    else:
        _borrar_tarea()


def reparar() -> bool:
    """
    Vuelve a aplicar el inicio automático si el usuario lo tiene activado (registro y tarea),
    por si un programa de limpieza o una actualización lo quitó. Devuelve si quedó activo.
    """
    if os.name != "nt":
        return False
    preferencia = _leer_preferencia()
    if preferencia is None:
        # Instalaciones anteriores sin archivo de preferencia: vale lo que haya en el registro
        preferencia = _run_activo()
        if not preferencia:
            return False
    if not preferencia:
        return False
    try:
        configurar(True)
    except OSError as e:
        print(f"[Autoarranque] No se pudo reparar el inicio automático: {e}")
    return esta_activo()
