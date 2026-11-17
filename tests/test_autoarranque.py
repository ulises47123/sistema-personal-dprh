"""
Inicio automático: tarea programada por XML (sin Administrador), preferencia del usuario,
reparación en cada inicio e instancia única sin mostrar el panel al arrancar en segundo plano.
"""
import os
import socket
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend import autoarranque

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_xml_tarea_al_iniciar_sesion_del_usuario():
    xml = autoarranque.xml_tarea(r"C:\Program Files & Co\SistemaDPRH.exe", "--hidden", r"PC\Jose")
    raiz = ET.fromstring(xml.split("?>", 1)[1])

    assert raiz.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text == r"PC\Jose"
    assert raiz.find("t:Principals/t:Principal/t:UserId", NS).text == r"PC\Jose"
    assert raiz.find("t:Principals/t:Principal/t:RunLevel", NS).text == "LeastPrivilege"
    assert raiz.find("t:Actions/t:Exec/t:Command", NS).text == r"C:\Program Files & Co\SistemaDPRH.exe"
    assert raiz.find("t:Actions/t:Exec/t:Arguments", NS).text == "--hidden"
    # Sin límite de tiempo (por defecto Windows corta las tareas a las 72 h) y también con batería
    assert raiz.find("t:Settings/t:ExecutionTimeLimit", NS).text == "PT0S"
    assert raiz.find("t:Settings/t:DisallowStartIfOnBatteries", NS).text == "false"
    assert raiz.find("t:Settings/t:StopIfGoingOnBatteries", NS).text == "false"


@pytest.fixture
def windows_simulado(tmp_path, monkeypatch):
    """Registro y tarea simulados: nunca toca el inicio automático real del equipo."""
    estado = {"run": False, "tarea": False}
    monkeypatch.setattr(autoarranque.os, "name", "nt")
    monkeypatch.setattr(autoarranque, "ARCHIVO_PREFERENCIA", str(tmp_path / "pref.txt"))
    monkeypatch.setattr(autoarranque, "_run_activo", lambda: estado["run"])
    monkeypatch.setattr(autoarranque, "_configurar_run", lambda activar: estado.update(run=activar))
    monkeypatch.setattr(autoarranque, "_tarea_existe", lambda: estado["tarea"])
    monkeypatch.setattr(autoarranque, "_crear_tarea", lambda: estado.update(tarea=True) or True)
    monkeypatch.setattr(autoarranque, "_borrar_tarea", lambda: estado.update(tarea=False))
    return estado


def test_configurar_activa_y_desactiva_ambos_caminos(windows_simulado):
    autoarranque.configurar(True)
    assert windows_simulado == {"run": True, "tarea": True}
    assert autoarranque.esta_activo()

    autoarranque.configurar(False)
    assert windows_simulado == {"run": False, "tarea": False}
    assert not autoarranque.esta_activo()


def test_reparar_restaura_lo_que_se_borro(windows_simulado):
    autoarranque.configurar(True)
    # Un programa de limpieza borra la entrada del registro y la tarea
    windows_simulado.update(run=False, tarea=False)

    assert autoarranque.reparar()
    assert windows_simulado == {"run": True, "tarea": True}


def test_reparar_respeta_la_desactivacion_del_usuario(windows_simulado):
    autoarranque.configurar(False)
    assert not autoarranque.reparar()
    assert windows_simulado == {"run": False, "tarea": False}


def test_reparar_migra_instalaciones_anteriores_con_registro(windows_simulado):
    windows_simulado["run"] = True  # versión anterior: solo registro, sin archivo de preferencia
    assert autoarranque.reparar()
    assert windows_simulado["tarea"]
    with open(autoarranque.ARCHIVO_PREFERENCIA, encoding="utf-8") as f:
        assert f.read() == "1"


def test_segunda_instancia_en_segundo_plano_no_muestra_el_panel(monkeypatch):
    from backend import gui_launcher

    ocupado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ocupado.bind(("127.0.0.1", 0))
    ocupado.listen(5)
    ocupado.settimeout(0.5)
    monkeypatch.setattr(gui_launcher, "PUERTO_INSTANCIA", ocupado.getsockname()[1])
    try:
        assert gui_launcher.tomar_instancia_unica(avisar=False) is None
        with pytest.raises(socket.timeout):
            ocupado.accept()  # nadie le pidió a la primera instancia que muestre su panel

        assert gui_launcher.tomar_instancia_unica(avisar=True) is None
        conn, _ = ocupado.accept()
        conn.close()
    finally:
        ocupado.close()
