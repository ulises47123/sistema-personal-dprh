"""
scripts/build_exe.py
Empaqueta el Sistema DPRH (panel Tkinter + servidor FastAPI) como ejecutable de Windows.

La arquitectura del ejecutable es la del intérprete que corre este script:
  - Python de 64 bits  ->  dist/win64/SistemaDPRH/  y  dist/SistemaDPRH_win64.zip
  - Python de 32 bits  ->  dist/win32/SistemaDPRH/  y  dist/SistemaDPRH_win32.zip

Uso (con PyInstaller y requirements.txt instalados en ese intérprete):
    python scripts/build_exe.py

En 64 bits, si Inno Setup 6 está instalado (o la variable ISCC apunta a ISCC.exe), también genera
el instalador para el usuario final: dist/SistemaDPRH_Instalador_win64.exe

La base de datos, certificados, backups y documentos subidos se crean junto al .exe
en el primer arranque; no se incluye ningún dato ni clave del equipo de desarrollo.
"""

import os
import shutil
import struct
import subprocess
import sys

import PyInstaller.__main__

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BITS = struct.calcsize("P") * 8
ARCH = "win64" if BITS == 64 else "win32"
APP_NAME = "SistemaDPRH"

DIST_DIR = os.path.join(ROOT_DIR, "dist", ARCH)
WORK_DIR = os.path.join(ROOT_DIR, "build", ARCH)


def buscar_iscc():
    """Compilador de Inno Setup: variable ISCC, PATH o rutas de instalación habituales."""
    candidatos = [
        os.environ.get("ISCC", ""),
        shutil.which("ISCC") or "",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    return next((c for c in candidatos if c and os.path.exists(c)), None)


NOMBRES_PROHIBIDOS = {
    "ca.key", "ca.crt", "server.key", "server.crt", "rootca.pem", "rootca-key.pem",
    "id_rsa", "id_ed25519", "id_ecdsa", "recursos_hidricos.db",
}
CARPETAS_PROHIBIDAS = {"certs", ".ssh", ".ssh_deploy", "backups_gdrive_sync"}
EXTENSIONES_PROHIBIDAS = {".key", ".pfx", ".p12"}


def buscar_datos_sensibles(app_dir):
    """Lista los archivos del paquete que parecen claves, certificados del equipo o datos reales."""
    hallados = []
    for carpeta, subdirs, archivos in os.walk(app_dir):
        for d in subdirs:
            if d.lower() in CARPETAS_PROHIBIDAS:
                hallados.append(os.path.join(carpeta, d))
        for nombre in archivos:
            ruta = os.path.join(carpeta, nombre)
            if nombre.lower() in NOMBRES_PROHIBIDOS or os.path.splitext(nombre)[1].lower() in EXTENSIONES_PROHIBIDAS:
                hallados.append(ruta)
                continue
            # Cualquier clave privada en formato PEM/OpenSSH, sea cual sea el nombre del archivo
            if os.path.getsize(ruta) <= 2 * 1024 * 1024:
                with open(ruta, "rb") as f:
                    if b"PRIVATE KEY-----" in f.read():
                        hallados.append(ruta)
    return hallados


def main():
    print(f"[Build] Python {sys.version.split()[0]} ({BITS} bits) -> {ARCH}")

    PyInstaller.__main__.run([
        os.path.join(ROOT_DIR, "backend", "gui_launcher.py"),
        "--name", APP_NAME,
        "--noconfirm",
        "--clean",
        "--windowed",
        # Las librerías quedan en _internal\ (junto al .exe sólo se ve SistemaDPRH.exe).
        # Los datos (base, certificados, backups) van a %LOCALAPPDATA%\SistemaDPRH: ver backend/paths.py
        "--icon", os.path.join(ROOT_DIR, "frontend", "img", "favicon.ico"),
        "--add-data", f"{os.path.join(ROOT_DIR, 'frontend')}{os.pathsep}frontend",
        "--paths", ROOT_DIR,
        # uvicorn importa la app por texto ("backend.main:app")
        "--hidden-import", "backend.main",
        "--collect-submodules", "backend",
        "--collect-submodules", "uvicorn",
        "--collect-all", "xhtml2pdf",
        "--collect-all", "reportlab",
        "--collect-data", "pycloudflared",
        "--distpath", DIST_DIR,
        "--workpath", WORK_DIR,
        "--specpath", WORK_DIR,
    ])

    app_dir = os.path.join(DIST_DIR, APP_NAME)
    # Las claves y certificados se generan en cada PC al primer arranque: nunca deben viajar en el paquete
    sensibles = buscar_datos_sensibles(app_dir)
    if sensibles:
        print("[Build] ERROR: el paquete contiene claves o datos del equipo de desarrollo:")
        for ruta in sensibles:
            print(f"   - {os.path.relpath(ruta, app_dir)}")
        sys.exit(1)
    print("[Build] Verificado: el paquete no contiene claves privadas, certificados ni datos.")

    zip_base = os.path.join(ROOT_DIR, "dist", f"{APP_NAME}_{ARCH}")
    shutil.make_archive(zip_base, "zip", DIST_DIR, APP_NAME)
    print(f"[Build] Ejecutable: {os.path.join(app_dir, APP_NAME + '.exe')}")
    print(f"[Build] Paquete:    {zip_base}.zip")

    if ARCH == "win64":
        iscc = buscar_iscc()
        if iscc:
            subprocess.run([iscc, os.path.join(ROOT_DIR, "scripts", "instalador_win64.iss")], check=True)
            print(f"[Build] Instalador: {os.path.join(ROOT_DIR, 'dist', 'SistemaDPRH_Instalador_win64.exe')}")
            # Junto al instalador, para entregarlo a quienes lo instalan solo de prueba
            bat_borrar = "Borrar todo Sistema DPRH.bat"
            shutil.copy2(os.path.join(ROOT_DIR, "scripts", bat_borrar), os.path.join(ROOT_DIR, "dist", bat_borrar))
            print(f"[Build] Borrado:    {os.path.join(ROOT_DIR, 'dist', bat_borrar)}")
        else:
            print("[Build] Inno Setup 6 no encontrado: no se generó el instalador (defina la variable ISCC con la ruta de ISCC.exe).")


if __name__ == "__main__":
    main()
