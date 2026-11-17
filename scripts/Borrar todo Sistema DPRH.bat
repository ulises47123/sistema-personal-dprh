@echo off
rem Sistema DPRH - BORRAR TODO (instalaciones de prueba, p. ej. estudiantes)
rem Desinstala el programa y borra todo lo que crea en la PC: datos, copias de seguridad,
rem inicio automatico, certificado de seguridad y dominio recursoshidricos.local.
rem Pide escribir BORRAR para confirmar. Lo que requiere Administrador lo pide con la ventana de Windows.
title Borrar todo - Sistema DPRH
rem La desinstalacion borra la carpeta del programa: este archivo se ejecuta desde una copia en TEMP
if /i "%~dp0"=="%TEMP%\" goto borrar
copy /y "%~f0" "%TEMP%\Borrar_todo_SistemaDPRH.bat" >nul
"%TEMP%\Borrar_todo_SistemaDPRH.bat"
:borrar
rem Sin heredar modulos de PowerShell 7 (si se lanza desde pwsh): PowerShell 5.1 usa los suyos
set PSModulePath=
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = [IO.File]::ReadAllText('%~f0'); Invoke-Expression $t.Substring($t.IndexOf('#' + 'POWERSHELL#') + 12)"
echo.
pause
exit /b
#POWERSHELL#
$ErrorActionPreference = 'SilentlyContinue'
function Paso($texto) { Write-Host ''; Write-Host "== $texto" -ForegroundColor Cyan }

if (-not $env:LOCALAPPDATA) { Write-Host 'No se encontro la carpeta LOCALAPPDATA: no se borra nada.' -ForegroundColor Red; exit 1 }
$app   = Join-Path $env:LOCALAPPDATA 'Programs\SistemaDPRH'
$datos = Join-Path $env:LOCALAPPDATA 'SistemaDPRH'
$escritorios = @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:USERPROFILE 'Desktop')) | Select-Object -Unique
$tarea = 'SistemaDPRH - Inicio automatico'
$hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
# Certificados por .NET (no depende del modulo de PowerShell de los certificados)
function Get-CasDPRH([string]$ubicacion) {
    $almacen = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', $ubicacion)
    $almacen.Open('ReadOnly')
    $cas = @($almacen.Certificates | Where-Object { $_.Subject -like '*O=Recursos Hidricos CA*' })
    $almacen.Close()
    return ,$cas
}
# Procesos del programa (SistemaDPRH.exe, cloudflared-windows-amd64.exe, ...)
function Stop-ProcesosDPRH { Get-Process | Where-Object { $_.Path -and ($_.Path -like "$app\*" -or $_.Path -like "$datos\*") } | Stop-Process -Force }
# Borra una carpeta reintentando unos segundos (el desinstalador o un proceso pueden tenerla abierta)
function Remove-Carpeta([string]$ruta) {
    for ($i = 0; $i -lt 15 -and (Test-Path $ruta); $i++) {
        Stop-ProcesosDPRH
        Remove-Item $ruta -Recurse -Force
        if (Test-Path $ruta) { Start-Sleep -Seconds 1 }
    }
}

Write-Host ''
Write-Host '  BORRAR TODO - Sistema DPRH (instalacion de prueba)' -ForegroundColor Yellow
Write-Host '  ------------------------------------------------'
Write-Host '  Se desinstala el programa y se borra de ESTA PC:'
Write-Host '   - la base de datos, documentos cargados, certificados y registros'
Write-Host '   - las copias de seguridad (Documentos y Google Drive)'
Write-Host '   - el inicio automatico con Windows'
Write-Host '   - el certificado de seguridad y el dominio recursoshidricos.local'
Write-Host ''
Write-Host '  ATENCION: no se puede deshacer.' -ForegroundColor Red
$respuesta = Read-Host '  Escriba BORRAR y presione Enter para continuar'
if ($respuesta -ne 'BORRAR') { Write-Host ''; Write-Host 'Cancelado: no se borro nada.'; exit 0 }

Paso '1/8  Cerrando el programa'
Get-Process SistemaDPRH | Stop-Process -Force
Stop-ProcesosDPRH
Start-Sleep -Seconds 2

Paso '2/8  Desinstalando el programa'
$desinstalador = Join-Path $app 'unins000.exe'
if (Test-Path $desinstalador) {
    Start-Process $desinstalador -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait
    # El desinstalador termina en segundo plano: se espera a que borre sus archivos
    for ($i = 0; $i -lt 60 -and (Test-Path $desinstalador); $i++) { Start-Sleep -Seconds 1 }
}
Remove-Carpeta $app
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B0B7E1C-3E55-4C3F-9C1B-5D7A2E4F9A31}_is1' -Recurse -Force
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'Sistema DPRH') -Recurse -Force
foreach ($e in $escritorios) { Remove-Item (Join-Path $e 'Sistema DPRH.lnk') -Force }

Paso '3/8  Quitando el inicio automatico con Windows'
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'RecursosHidricosDPRH' -Force
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'GestionPersonalRecursosHidricos' -Force
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run' -Name 'RecursosHidricosDPRH' -Force
schtasks /Delete /TN $tarea /F 2>$null | Out-Null

Paso '4/8  Borrando los datos del sistema'
Remove-Carpeta $datos

Paso '5/8  Borrando las copias de seguridad'
$copias = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Copia de Seguridad'
Get-ChildItem $copias -Filter 'recursos_hidricos_backup_*' | Remove-Item -Force
if ((Test-Path $copias) -and -not (Get-ChildItem $copias -Force)) { Remove-Item $copias -Force }
# Google Drive para escritorio: unidad virtual (G:) o modo espejo dentro del perfil
$raices = @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Root }) + @($env:USERPROFILE)
foreach ($raiz in $raices) {
    foreach ($miUnidad in 'Mi unidad', 'My Drive', 'Meu Drive', 'Mon Drive', 'Meine Ablage', 'Il mio Drive') {
        $carpeta = Join-Path $raiz "$miUnidad\Copia de Seguridad DPRH"
        if (Test-Path $carpeta) { Remove-Item $carpeta -Recurse -Force; Write-Host "   Borrado: $carpeta" }
    }
}

Paso '6/8  Borrando carpetas creadas en el Escritorio'
foreach ($e in $escritorios) {
    foreach ($nombre in 'Certificado Sistema DPRH', 'recursoshidricos-acceso-web') {
        Remove-Item (Join-Path $e $nombre) -Recurse -Force
    }
}

Paso '7/8  Quitando el certificado de seguridad (usuario)'
$casUsuario = Get-CasDPRH 'CurrentUser'
if ($casUsuario.Count -gt 0) {
    Write-Host '   Windows va a preguntar si desea ELIMINAR el certificado: presione SI.' -ForegroundColor Yellow
    foreach ($ca in $casUsuario) { certutil -user -delstore Root $ca.Thumbprint | Out-Null }
}

Paso '8/8  Certificado del equipo y dominio recursoshidricos.local'
$casEquipo = Get-CasDPRH 'LocalMachine'
$conDominio = Select-String -Path $hosts -Pattern 'recursoshidricos\.local' -Quiet
if ($casEquipo.Count -gt 0 -or $conDominio) {
    Write-Host '   Hace falta permiso de Administrador: en la ventana de Windows presione SI.' -ForegroundColor Yellow
    $comandoAdmin = @'
$almacen = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
$almacen.Open('ReadOnly')
$almacen.Certificates | Where-Object { $_.Subject -like '*O=Recursos Hidricos CA*' } | ForEach-Object { certutil -delstore Root $_.Thumbprint | Out-Null }
$almacen.Close()
$h = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$lineas = [IO.File]::ReadAllLines($h, [Text.Encoding]::Default)
$quedan = @($lineas | Where-Object { $_ -notmatch 'recursoshidricos\.local' -and $_ -notmatch '^# Recursos Hidricos Sistema Local' })
if ($quedan.Count -ne $lineas.Count) { [IO.File]::WriteAllLines($h, [string[]]$quedan, [Text.Encoding]::Default); ipconfig /flushdns | Out-Null }
'@
    $codificado = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($comandoAdmin))
    try {
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $codificado -ErrorAction Stop
    } catch {
        Write-Host '   No se otorgo el permiso de Administrador: el certificado del equipo y el dominio quedan.' -ForegroundColor Red
    }
}

# Verificacion final
Write-Host ''
Write-Host '== Resultado' -ForegroundColor Cyan
$pendientes = @()
if (Test-Path $app) { $pendientes += "Carpeta del programa: $app" }
if (Test-Path $datos) { $pendientes += "Datos del sistema: $datos" }
if ((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run').RecursosHidricosDPRH) { $pendientes += 'Inicio automatico (registro)' }
schtasks /Query /TN $tarea 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $pendientes += 'Inicio automatico (tarea programada)' }
if ((Get-CasDPRH 'CurrentUser').Count -gt 0) { $pendientes += 'Certificado de seguridad (usuario)' }
if ((Get-CasDPRH 'LocalMachine').Count -gt 0) { $pendientes += 'Certificado de seguridad (equipo)' }
if (Select-String -Path $hosts -Pattern 'recursoshidricos\.local' -Quiet) { $pendientes += 'Dominio recursoshidricos.local (archivo hosts)' }

if ($pendientes.Count -eq 0) {
    Write-Host '   Listo: el Sistema DPRH se borro por completo de esta PC.' -ForegroundColor Green
} else {
    Write-Host '   No se pudo borrar:' -ForegroundColor Red
    $pendientes | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    Write-Host '   Vuelva a ejecutar este archivo (y acepte las ventanas de Windows con SI).'
}
