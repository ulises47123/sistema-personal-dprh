# Sistema DPRH Jujuy — Gestión de Personal

Sistema integral de gestión de personal para la Dirección Provincial de Recursos Hídricos de Jujuy (DPRH Jujuy).

---

## 🚀 Arquitectura del Sistema y Seguridad

- **Panel de Control de Escritorio**: `backend/gui_launcher.py` (Tkinter). Levanta el servidor y gestiona red, túnel, GitHub y backups. La interfaz web (`frontend/`) se abre en el navegador.
- **Backend API**: FastAPI (Python) ejecutándose en puerto `8000` con SQLite/PostgreSQL.
- **Acceso Remoto / Túnel**: Cloudflare Tunnel (`cloudflared`) enviando tráfico de lectura/escritura seguro.
- **Sincronización Automática**: Módulo de sincronización automática de estado y documentación (`docs/index.html`) publicado vía GitHub Pages / Git SSH.
- **Seguridad**:
  - Claves SSH sin passphrase (`.ssh_deploy/id_ed25519`) configuradas como Deploy Keys en GitHub con permiso de escritura.
  - Variable de entorno `GIT_SSH_COMMAND` apuntando a la clave privada del sistema sin requerir interacción por consola.
  - Subprocesos gestionados en segundo plano sin bloquiar el hilo principal.

---

## 🖥️ Instalación para el usuario final (recomendado)

1. Ejecutar **`SistemaDPRH_Instalador_win64.exe`** (no pide permisos de Administrador ni requiere Python ni Git).
2. Al terminar, el sistema se abre solo y queda **configurado para iniciar automáticamente con Windows** (en segundo plano).
3. Para ver el panel otra vez, abrir *Sistema DPRH* desde el Escritorio o el menú Inicio. Si ya está en ejecución, se muestra el panel existente (no se abre un segundo servidor).
4. El inicio automático se puede desactivar desde el panel: pestaña *Ajustes & Backups*.

- Programa: `%LOCALAPPDATA%\Programs\SistemaDPRH\`
- Datos (base de datos, certificados, backups, legajos, `servidor.log`): `%LOCALAPPDATA%\SistemaDPRH\`. Desinstalar **no** borra los datos.
- **Git no es necesario.** Si no está instalado, la publicación del enlace en GitHub Pages se omite y el resto del sistema funciona igual.
- La primera vez Windows puede preguntar si se instala el certificado local (responder *Sí*, para que el navegador confíe en `https://localhost:8000`) y si se permite el acceso en el Firewall (necesario para que otras PCs de la red accedan).

### Compilar el instalador (64 bits)

```cmd
python -m pip install -r requirements.txt pyinstaller
set ISCC=C:\ruta\a\Inno Setup 6\ISCC.exe
python scripts\build_exe.py
```

Genera `dist\SistemaDPRH_Instalador_win64.exe` (lo único que hay que entregar) y la versión portable `dist\SistemaDPRH_win64.zip`.

---

## ⚙️ Arranque Automático ejecutando desde el código fuente (`iniciar_sistema.bat`)


Para garantizar que el sistema inicie automáticamente al encender la PC o iniciar sesión en Windows:

### 1. Activar Arranque Automático
Ejecutar el script de registro con permisos de Administrador:
```cmd
scripts\registrar_arranque.bat
```
Este script utiliza `schtasks` para registrar la tarea programada `DPRH_Jujuy_ArranqueAutomatico` con nivel de privilegio `/RL HIGHEST` y activador `/SC ONLOGON`, vinculada a `iniciar_sistema.bat`.

### 2. Desactivar Arranque Automático
Si desea remover el arranque automático del sistema al iniciar sesión:
```cmd
scripts\desregistrar_arranque.bat
```
Este script elimina la tarea programada `DPRH_Jujuy_ArranqueAutomatico` de Windows de forma limpia e idempotente.

---

## 🧪 Verificación y Tests

Para ejecutar la suite completa de pruebas automatizadas:
```cmd
.\venv\Scripts\python -m pytest tests/ -v
```
