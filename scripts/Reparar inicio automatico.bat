@echo off
rem Sistema DPRH - vuelve a activar el inicio automatico con Windows
rem (registro HKCU\Run + tarea programada al iniciar sesion). No requiere Administrador.
title Sistema DPRH - Reparar inicio automatico
start "" /wait "%~dp0SistemaDPRH.exe" --activar-arranque
schtasks /Query /TN "SistemaDPRH - Inicio automatico" >nul 2>&1
if errorlevel 1 (
  echo No se pudo crear la tarea programada: queda activo el inicio por registro de Windows.
) else (
  echo Listo: el Sistema DPRH arrancara solo cada vez que se inicie sesion en Windows.
)
pause
