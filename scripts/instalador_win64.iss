; scripts/instalador_win64.iss
; Instalador de Windows 64 bits del Sistema DPRH (Inno Setup 6).
; Lo compila scripts/build_exe.py después de PyInstaller; requiere dist\win64\SistemaDPRH\.
;
; - No pide permisos de Administrador: se instala para el usuario en %LOCALAPPDATA%\Programs\SistemaDPRH.
; - Crea accesos directos y abre el sistema al terminar. Durante la instalación el programa activa
;   el inicio automático con Windows (registro + tarea programada; se puede desactivar desde el panel)
;   e instala su certificado de confianza para que el navegador muestre el sitio como seguro.
; - "Reparar inicio automatico.bat" (menú Inicio) vuelve a activar el inicio automático.
; - "Borrar todo Sistema DPRH.bat" (menú Inicio) desinstala y borra todo lo que el programa creó en la PC
;   (pensado para instalaciones de prueba, p. ej. estudiantes).
; - Desinstalar NO borra los datos (%LOCALAPPDATA%\SistemaDPRH: base de datos, certificados, backups).

#define AppName "Sistema DPRH - Recursos Hídricos"
#define AppExe "SistemaDPRH.exe"
#define AppVersion "2.0"
#define SourceDir "..\dist\win64\SistemaDPRH"

[Setup]
AppId={{6B0B7E1C-3E55-4C3F-9C1B-5D7A2E4F9A31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Dirección Provincial de Recursos Hídricos - Jujuy
DefaultDirName={localappdata}\Programs\SistemaDPRH
DefaultGroupName=Sistema DPRH
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=SistemaDPRH_Instalador_win64
SetupIconFile=..\frontend\img\favicon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el Escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Reparar inicio automatico.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "Borrar todo Sistema DPRH.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Sistema DPRH"; Filename: "{app}\{#AppExe}"
Name: "{group}\Reparar inicio automático"; Filename: "{app}\Reparar inicio automatico.bat"; WorkingDir: "{app}"
Name: "{group}\Borrar todo (desinstalar y limpiar la PC)"; Filename: "{app}\Borrar todo Sistema DPRH.bat"
Name: "{group}\Desinstalar Sistema DPRH"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Sistema DPRH"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Inicio automático (registro + tarea programada) y certificado de confianza: Windows pide confirmar con "Sí"
Filename: "{app}\{#AppExe}"; Parameters: "--registrar-arranque"; StatusMsg: "Configurando inicio automático y certificado de seguridad..."; Flags: waituntilterminated
Filename: "{app}\{#AppExe}"; Description: "Iniciar el Sistema DPRH ahora"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#AppExe} /F"; Flags: runhidden; RunOnceId: "CerrarSistemaDPRH"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""SistemaDPRH - Inicio automatico"" /F"; Flags: runhidden; RunOnceId: "BorrarTareaInicioSistemaDPRH"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { Una versión anterior en ejecución bloquearía los archivos: se cierra antes de copiar }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#AppExe} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { Quitar el inicio automático con Windows (nombre actual y el de versiones anteriores) }
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'RecursosHidricosDPRH');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'GestionPersonalRecursosHidricos');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run', 'RecursosHidricosDPRH');
    { Si se reinstala, el inicio automático vuelve a activarse por defecto }
    DeleteFile(ExpandConstant('{localappdata}\SistemaDPRH\autoarranque_configurado.flag'));
    DeleteFile(ExpandConstant('{localappdata}\SistemaDPRH\autoarranque_preferencia.txt'));
  end;
end;
