"""
"Borrar todo Sistema DPRH.bat": cmd + PowerShell embebido en el mismo archivo.
Se verifica el formato (ASCII, CRLF), que el PowerShell sea válido y que cubra todo lo que crea el programa.
"""
import os
import shutil
import subprocess

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAT = os.path.join(ROOT_DIR, "scripts", "Borrar todo Sistema DPRH.bat")
MARCA = "#POWERSHELL#"


def _contenido():
    with open(BAT, "rb") as f:
        return f.read()


def _powershell():
    texto = _contenido().decode("ascii")
    return texto[texto.index(MARCA) + len(MARCA):]


def test_formato_ascii_crlf_y_una_sola_marca():
    datos = _contenido()
    datos.decode("ascii")
    assert b"\n" not in datos.replace(b"\r\n", b"")
    assert datos.count(MARCA.encode()) == 1


def test_cubre_todo_lo_que_crea_el_programa():
    ps = _powershell()
    for esperado in [
        "Read-Host",                               # confirmación escribiendo BORRAR
        "unins000.exe",                            # desinstalador de Inno Setup
        "Programs\\SistemaDPRH", "'SistemaDPRH'",  # carpeta del programa y de datos
        "RecursosHidricosDPRH", "StartupApproved", "SistemaDPRH - Inicio automatico",
        "recursos_hidricos_backup_", "Copia de Seguridad DPRH",
        "Certificado Sistema DPRH", "recursoshidricos-acceso-web",
        "certutil -user -delstore Root", "certutil -delstore Root",
        "recursoshidricos\\.local",
    ]:
        assert esperado in ps, esperado
    # La unidad Cert: falla en silencio si el .bat se lanza desde PowerShell 7: se usa .NET
    assert "Cert:\\" not in ps
    assert "set PSModulePath=" in _contenido().decode("ascii")


@pytest.mark.skipif(not shutil.which("powershell"), reason="requiere Windows PowerShell")
def test_powershell_embebido_sin_errores_de_sintaxis(tmp_path):
    script = tmp_path / "codigo.ps1"
    script.write_text(_powershell(), encoding="ascii")
    comando = (
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$e); $e.Count"
    )
    res = subprocess.run(["powershell", "-NoProfile", "-Command", comando], capture_output=True, text=True, timeout=60)
    assert res.stdout.strip() == "0", res.stdout + res.stderr
