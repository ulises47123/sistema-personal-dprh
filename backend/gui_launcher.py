"""
backend/gui_launcher.py
Panel de Control y Lanzador Ligero en Tkinter para el Sistema DPRH.
Reemplaza la aplicación pesada de Electron y permite gestionar el Servidor Web, Red LAN, Túnel Cloudflare, GitHub y Backups.
"""

import os
import sys
import time
import socket
import threading
import webbrowser
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.request

# Agregar directorio raíz al PATH
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.paths import DATA_DIR, FRONTEND_DIR

# En el ejecutable sin consola (PyInstaller --windowed) stdout/stderr son None:
# se redirigen a un log para que print() y el logging de uvicorn no fallen.
if sys.stdout is None or sys.stderr is None:
    _log = open(os.path.join(DATA_DIR, "servidor.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

# En el ejecutable sin consola, cada programa externo (git, ssh-keygen, cloudflared...)
# abriría su propia ventana negra: se lanzan ocultos salvo que se pida otra cosa.
if os.name == "nt" and getattr(sys, "frozen", False):
    _popen_init_original = subprocess.Popen.__init__

    def _popen_sin_ventana(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        _popen_init_original(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_sin_ventana

from backend import ssl_generator
from backend.ssl_generator import ensure_ssl_certificates, obtener_ip_lan
from backend.services import auto_readme_sync, gdrive_service, github_sync_service
from backend import autoarranque

URL_LOCAL = "https://localhost:8000"
# Puerto local (sólo 127.0.0.1) con el que una segunda ejecución le pide a la primera que muestre el panel
PUERTO_INSTANCIA = 8765
# Marca de que ya se activó el inicio automático por defecto (el usuario puede desactivarlo luego)
MARCA_AUTOARRANQUE = os.path.join(DATA_DIR, "autoarranque_configurado.flag")


def url_tunel_activo():
    """URL del túnel Cloudflare activo, lo haya iniciado el panel o el servidor web."""
    if auto_readme_sync._tunnel_url_actual and auto_readme_sync._proceso_tunel and auto_readme_sync._proceso_tunel.poll() is None:
        return auto_readme_sync._tunnel_url_actual
    main_mod = sys.modules.get("backend.main")
    estado = getattr(main_mod, "CLOUDFLARE_TUNNEL_STATE", None) if main_mod else None
    if estado and estado.get("url") and estado.get("proceso") and estado["proceso"].poll() is None:
        return estado["url"]
    return None

class ServidorDPRHApp:
    def __init__(self, root, is_background=False, socket_instancia=None):
        self.root = root
        self.is_background = is_background
        self.server_thread = None
        self.tunnel_thread = None
        self.tunnel_proc = None
        self.tunnel_url = None
        self.server_running = True
        self.error_servidor = None
        self.pedido_mostrar = threading.Event()
        self.estado_drive = None
        self.estado_drive_mostrado = None

        # Configuración de Ventana
        self.root.title("Gestión de Personal - Servidor Central DPRH")
        self.root.geometry("680x720")
        self.root.minsize(620, 660)
        self.root.configure(bg="#f8fafc")

        # Configurar icono si existe
        logo_ico = os.path.join(FRONTEND_DIR, "img", "favicon.ico")
        if os.path.exists(logo_ico):
            try:
                self.root.iconbitmap(logo_ico)
            except Exception:
                pass

        # Certificados HTTPS antes de levantar el servidor (así el panel puede revisar la CA enseguida)
        self.rutas_ssl = None
        try:
            self.rutas_ssl = ensure_ssl_certificates()
        except Exception as e:
            print(f"[SSL Error] No se pudieron preparar los certificados: {e!r}")

        # Iniciar Servidor FastAPI en segundo plano
        self.iniciar_servidor_uvicorn()

        # Construir Interfaz
        self.crear_estilos()
        self.crear_widgets()

        # Iniciar monitores en segundo plano
        self.actualizar_estado_lan()
        self.actualizar_estado_certificado()
        self.actualizar_estado_github()
        self.monitorear_estado_tunel()
        self.monitorear_servidor()
        self.monitorear_drive()

        if socket_instancia is not None:
            threading.Thread(target=self.escuchar_otras_instancias, args=(socket_instancia,), daemon=True, name="InstanciaUnica").start()

        # Si se pidió inicio en segundo plano, ocultar ventana
        if self.is_background:
            self.root.withdraw()

        # Si Windows todavía no confía en el certificado, se explica y se instala (también al iniciar oculto)
        self.root.after(1000, self.verificar_certificado_inicial)

        # Manejo de cierre seguro
        self.root.protocol("WM_DELETE_WINDOW", self.on_cerrar_aplicacion)

    def crear_estilos(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#f8fafc", borderwidth=0)
        style.configure("TNotebook.Tab", background="#e2e8f0", foreground="#1e293b", padding=[14, 8], font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#0284c7")], foreground=[("selected", "#ffffff")])

    def crear_widgets(self):
        # ── Header ──
        header_frame = tk.Frame(self.root, bg="#0b69a3", height=80)
        header_frame.pack(fill=tk.X, side=tk.TOP)

        lbl_titulo = tk.Label(
            header_frame, 
            text="Dirección Provincial de Recursos Hídricos", 
            font=("Segoe UI", 13, "bold"), 
            fg="#ffffff", 
            bg="#0b69a3"
        )
        lbl_titulo.pack(anchor="w", padx=20, pady=(12, 2))

        lbl_sub = tk.Label(
            header_frame, 
            text="Panel de Administración del Servidor y Control de Red", 
            font=("Segoe UI", 9), 
            fg="#bae6fd", 
            bg="#0b69a3"
        )
        lbl_sub.pack(anchor="w", padx=20, pady=(0, 12))

        # ── Botón Principal: Abrir en Navegador ──
        btn_box = tk.Frame(self.root, bg="#f8fafc")
        btn_box.pack(fill=tk.X, padx=20, pady=(15, 10))

        btn_abrir = tk.Button(
            btn_box,
            text="🌐  ABRIR SISTEMA EN EL NAVEGADOR WEB",
            font=("Segoe UI", 11, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            activebackground="#0369a1",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=16,
            pady=10,
            cursor="hand2",
            command=self.abrir_navegador_local
        )
        btn_abrir.pack(fill=tk.X)

        # ── Notebook (Pestañas) ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=(5, 15))

        # Pestaña 1: Red y Acceso Remoto
        tab_red = tk.Frame(self.notebook, bg="#ffffff", padx=15, pady=15)
        self.notebook.add(tab_red, text="🌐 Red & Acceso Remoto")
        self.crear_tab_red(tab_red)

        # Pestaña 2: GitHub y Portal
        tab_portal = tk.Frame(self.notebook, bg="#ffffff", padx=15, pady=15)
        self.notebook.add(tab_portal, text="🔑 GitHub & Portal")
        self.crear_tab_portal(tab_portal)

        # Pestaña 3: Ajustes & Backups
        tab_ajustes = tk.Frame(self.notebook, bg="#ffffff", padx=15, pady=15)
        self.notebook.add(tab_ajustes, text="⚙️ Ajustes & Backups")
        self.crear_tab_ajustes(tab_ajustes)

        # Pestaña 4: Más Información
        tab_info = tk.Frame(self.notebook, bg="#ffffff", padx=15, pady=15)
        self.notebook.add(tab_info, text="ℹ️ Más Información")
        self.crear_tab_info(tab_info)

        # ── Footer / Barra de Estado ──
        footer = tk.Frame(self.root, bg="#f1f5f9", height=32)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        self.lbl_status = tk.Label(
            footer,
            text="● Servidor Activo (HTTPS Puerto 8000) | 32/64-bit Compatible",
            font=("Segoe UI", 8, "bold"),
            fg="#16a34a",
            bg="#f1f5f9"
        )
        self.lbl_status.pack(side=tk.LEFT, padx=15, pady=6)

    def crear_tab_red(self, parent):
        # Grupo LAN
        lbl_lan_title = tk.Label(parent, text="Direcciones de Conexión Local (LAN):", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_lan_title.pack(anchor="w", pady=(0, 4))

        self.txt_lan_info = tk.Label(
            parent,
            text="• Localhost: https://localhost:8000\n• Dominio Oficial: https://recursoshidricos.local:8000\n• IP Red: Detectando...",
            font=("Consolas", 9),
            bg="#f8fafc",
            fg="#334155",
            justify=tk.LEFT,
            relief=tk.SOLID,
            borderwidth=1,
            padx=10,
            pady=8
        )
        self.txt_lan_info.pack(fill=tk.X, pady=(0, 12))

        # Grupo Certificado de Seguridad
        lbl_cert_title = tk.Label(parent, text="Certificado de Seguridad (HTTPS):", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_cert_title.pack(anchor="w", pady=(0, 2))

        self.lbl_cert_estado = tk.Label(parent, text="Verificando certificado...", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#64748b", justify=tk.LEFT, wraplength=580)
        self.lbl_cert_estado.pack(anchor="w")

        self.lbl_dominio_estado = tk.Label(parent, text="", font=("Segoe UI", 9), bg="#ffffff", fg="#64748b", justify=tk.LEFT, wraplength=580)
        self.lbl_dominio_estado.pack(anchor="w", pady=(0, 6))

        cert_row = tk.Frame(parent, bg="#ffffff")
        cert_row.pack(fill=tk.X, pady=(0, 15))

        btn_instalar_cert = tk.Button(
            cert_row,
            text="🔒 Instalar Certificado",
            font=("Segoe UI", 9, "bold"),
            bg="#16a34a",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.instalar_certificado
        )
        btn_instalar_cert.pack(side=tk.LEFT, padx=(0, 8))

        btn_dominio_admin = tk.Button(
            cert_row,
            text="🛡️ Dominio .local (Administrador)",
            font=("Segoe UI", 9, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.configurar_dominio_admin
        )
        btn_dominio_admin.pack(side=tk.LEFT, padx=(0, 8))

        btn_exportar_cert = tk.Button(
            cert_row,
            text="📤 Exportar para otras PCs",
            font=("Segoe UI", 9),
            bg="#e2e8f0",
            fg="#1e293b",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.exportar_certificado
        )
        btn_exportar_cert.pack(side=tk.LEFT)

        # Grupo Túnel Remoto
        lbl_tunel_title = tk.Label(parent, text="Acceso Remoto por Internet (Cloudflare):", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_tunel_title.pack(anchor="w", pady=(0, 4))

        self.lbl_tunel_status = tk.Label(
            parent,
            text="Estado: Consultando o iniciando túnel seguro...",
            font=("Segoe UI", 9, "bold"),
            bg="#ffffff",
            fg="#0284c7"
        )
        self.lbl_tunel_status.pack(anchor="w", pady=(0, 4))

        self.ent_tunel_url = tk.Entry(parent, font=("Consolas", 9), bg="#f8fafc", relief=tk.SOLID, borderwidth=1)
        self.ent_tunel_url.pack(fill=tk.X, pady=(0, 10))

        btn_row = tk.Frame(parent, bg="#ffffff")
        btn_row.pack(fill=tk.X)

        self.btn_iniciar_tunel = tk.Button(
            btn_row,
            text="Iniciar Túnel",
            font=("Segoe UI", 9, "bold"),
            bg="#16a34a",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.accion_iniciar_tunel
        )
        self.btn_iniciar_tunel.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_detener_tunel = tk.Button(
            btn_row,
            text="Detener Túnel",
            font=("Segoe UI", 9, "bold"),
            bg="#dc2626",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.accion_detener_tunel
        )
        self.btn_detener_tunel.pack(side=tk.LEFT, padx=(0, 8))

        btn_copiar_url = tk.Button(
            btn_row,
            text="Copiar URL Remota",
            font=("Segoe UI", 9),
            bg="#e2e8f0",
            fg="#1e293b",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.copiar_url_remota
        )
        btn_copiar_url.pack(side=tk.LEFT)

    def crear_tab_portal(self, parent):
        # Grupo URL del Repositorio GitHub
        lbl_repo_title = tk.Label(parent, text="URL del Repositorio GitHub:", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_repo_title.pack(anchor="w", pady=(0, 2))

        repo_row = tk.Frame(parent, bg="#ffffff")
        repo_row.pack(fill=tk.X, pady=(0, 10))

        self.ent_repo_url = tk.Entry(repo_row, font=("Consolas", 8), bg="#f8fafc", relief=tk.SOLID, borderwidth=1)
        self.ent_repo_url.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_guardar_repo = tk.Button(
            repo_row,
            text="💾 Guardar URL",
            font=("Segoe UI", 8, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self.guardar_url_repositorio
        )
        btn_guardar_repo.pack(side=tk.RIGHT)

        # Grupo Señal GitHub
        lbl_gh_title = tk.Label(parent, text="Conexión con GitHub y Deploy Key:", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_gh_title.pack(anchor="w", pady=(0, 4))

        self.lbl_gh_signal = tk.Label(
            parent,
            text="Estado: Verificando señal de conexión...",
            font=("Segoe UI", 9, "bold"),
            bg="#ffffff",
            fg="#64748b"
        )
        self.lbl_gh_signal.pack(anchor="w", pady=(0, 6))

        btn_test_gh = tk.Button(
            parent,
            text="🔍 Probar Conexión con GitHub",
            font=("Segoe UI", 9, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.probar_conexion_github
        )
        btn_test_gh.pack(anchor="w", pady=(0, 10))

        # Llave Pública SSH
        lbl_key_title = tk.Label(parent, text="Llave Pública SSH (Deploy Key):", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#334155")
        lbl_key_title.pack(anchor="w", pady=(0, 2))

        self.ent_pub_key = tk.Entry(parent, font=("Consolas", 8), bg="#f8fafc", relief=tk.SOLID, borderwidth=1)
        self.ent_pub_key.pack(fill=tk.X, pady=(0, 6))

        key_btn_row = tk.Frame(parent, bg="#ffffff")
        key_btn_row.pack(fill=tk.X, pady=(0, 12))

        btn_copiar_key = tk.Button(
            key_btn_row,
            text="📋 Copiar Llave Pública",
            font=("Segoe UI", 8, "bold"),
            bg="#e2e8f0",
            fg="#1e293b",
            relief=tk.FLAT,
            padx=8,
            pady=4,
            cursor="hand2",
            command=self.copiar_llave_ssh
        )
        btn_copiar_key.pack(side=tk.LEFT, padx=(0, 6))

        btn_reset_key = tk.Button(
            key_btn_row,
            text="🔄 Restablecer / Generar Nueva Llave",
            font=("Segoe UI", 8, "bold"),
            bg="#f59e0b",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=8,
            pady=4,
            cursor="hand2",
            command=self.resetear_llave_ssh
        )
        btn_reset_key.pack(side=tk.LEFT)

        # Publicación Automática
        lbl_auto_title = tk.Label(parent, text="Publicación Automática del Portal (gh-pages):", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_auto_title.pack(anchor="w", pady=(0, 4))

        self.btn_forzar_sync = tk.Button(
            parent,
            text="🚀 Forzar Sincronización Inmediata a GitHub Pages",
            font=("Segoe UI", 9, "bold"),
            bg="#94a3b8",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=10,
            pady=6,
            state=tk.DISABLED,
            cursor="arrow",
            command=self.forzar_sincronizacion_portal
        )
        self.btn_forzar_sync.pack(anchor="w")

    def crear_tab_ajustes(self, parent):
        # Auto-inicio con Windows
        lbl_boot_title = tk.Label(parent, text="Comportamiento del Sistema:", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_boot_title.pack(anchor="w", pady=(0, 6))

        self.var_autoboot = tk.BooleanVar(value=self.verificar_autoboot())
        chk_autoboot = tk.Checkbutton(
            parent,
            text="Iniciar automáticamente con Windows en segundo plano (--hidden)",
            variable=self.var_autoboot,
            font=("Segoe UI", 9),
            bg="#ffffff",
            activebackground="#ffffff",
            command=self.cambiar_autoboot
        )
        chk_autoboot.pack(anchor="w", pady=(0, 15))

        # Copias de Seguridad
        lbl_bkp_title = tk.Label(parent, text="Copias de Seguridad (Base de Datos):", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_bkp_title.pack(anchor="w", pady=(0, 4))

        lbl_bkp_desc = tk.Label(
            parent,
            text="El sistema guarda automáticamente una copia verificada cada 5 horas en:\n• Documentos\\Copia de Seguridad\\ (las 3 copias más recientes)\n• Google Drive (Mi unidad\\Copia de Seguridad DPRH), si está instalado",
            font=("Segoe UI", 9),
            bg="#f8fafc",
            fg="#475569",
            justify=tk.LEFT,
            relief=tk.SOLID,
            borderwidth=1,
            padx=10,
            pady=8
        )
        lbl_bkp_desc.pack(fill=tk.X, pady=(0, 10))

        btn_crear_bkp = tk.Button(
            parent,
            text="💾  Crear Copia de Seguridad Ahora",
            font=("Segoe UI", 9, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            cursor="hand2",
            command=self.crear_backup_ahora
        )
        btn_crear_bkp.pack(anchor="w", pady=(0, 12))

        # Copia en la nube: Google Drive para escritorio
        lbl_drive_title = tk.Label(parent, text="Copia en la nube (Google Drive):", font=("Segoe UI", 10, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_drive_title.pack(anchor="w", pady=(0, 2))

        self.lbl_drive_estado = tk.Label(
            parent,
            text="Buscando Google Drive para escritorio...",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#64748b",
            justify=tk.LEFT,
            wraplength=560
        )
        self.lbl_drive_estado.pack(anchor="w", pady=(0, 2))

        # Hipervínculo a la descarga oficial (se oculta cuando Drive ya está funcionando)
        self.lbl_drive_link = tk.Label(
            parent,
            text="👉 Descargar Google Drive para escritorio (gratis)",
            font=("Segoe UI", 9, "bold", "underline"),
            bg="#ffffff",
            fg="#0284c7",
            cursor="hand2"
        )
        self.lbl_drive_link.bind("<Button-1>", lambda _e: webbrowser.open(gdrive_service.URL_DESCARGA_DRIVE))
        self.lbl_drive_link.pack(anchor="w")

    def crear_tab_info(self, parent):
        # Crear Canvas con Scrollbar para permitir desplazamiento si el contenido es largo
        canvas = tk.Canvas(parent, bg="#ffffff", highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scroll_content = tk.Frame(canvas, bg="#ffffff")

        scroll_content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scroll_content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ── Sección 1: Acerca del Proyecto ──
        lbl_info_title = tk.Label(scroll_content, text="ℹ️ Proyecto de Código Abierto", font=("Segoe UI", 11, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_info_title.pack(anchor="w", pady=(0, 4))

        lbl_info_desc = tk.Label(
            scroll_content,
            text="Este software es un proyecto de código abierto desarrollado para la gestión de personal.\nSi encontrás algún error, querés proponer mejoras o colaborar, podés abrir un issue o enviar un pull request.",
            font=("Segoe UI", 9),
            bg="#f8fafc",
            fg="#334155",
            justify=tk.LEFT,
            relief=tk.SOLID,
            borderwidth=1,
            padx=10,
            pady=8
        )
        lbl_info_desc.pack(fill=tk.X, pady=(0, 12))

        # ── Sección 2: Créditos y Agradecimientos ──
        lbl_cred_title = tk.Label(scroll_content, text="👨‍💻 Créditos y Agradecimientos", font=("Segoe UI", 11, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_cred_title.pack(anchor="w", pady=(0, 6))

        # Desarrollador Principal
        lbl_dev_title = tk.Label(scroll_content, text="• Desarrollador Principal:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#0b69a3")
        lbl_dev_title.pack(anchor="w", pady=(0, 2))

        dev_box = tk.Frame(scroll_content, bg="#ffffff")
        dev_box.pack(anchor="w", padx=12, pady=(0, 8))

        lbl_dev_name = tk.Label(dev_box, text="Ulises  |  GitHub: @ulises47123  |  Contacto: cajulian213@gmail.com", font=("Segoe UI", 9), bg="#ffffff", fg="#334155")
        lbl_dev_name.pack(side=tk.LEFT)

        btn_gh_profile = tk.Button(
            dev_box,
            text="🌐 Abrir GitHub",
            font=("Segoe UI", 8, "bold"),
            bg="#24292e",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=6,
            pady=2,
            cursor="hand2",
            command=lambda: webbrowser.open("https://github.com/ulises47123")
        )
        btn_gh_profile.pack(side=tk.LEFT, padx=(8, 0))

        # QA / Pruebas
        lbl_qa_title = tk.Label(scroll_content, text="• Pruebas y Control de Calidad (QA):", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#0b69a3")
        lbl_qa_title.pack(anchor="w", pady=(0, 2))

        lbl_qa_desc = tk.Label(scroll_content, text="   Candela Geronimo – Pruebas de funcionamiento, detección y reporte de errores.", font=("Segoe UI", 9), bg="#ffffff", fg="#334155")
        lbl_qa_desc.pack(anchor="w", pady=(0, 8))

        # Consultores de Datos
        lbl_data_title = tk.Label(scroll_content, text="• Consultores de Datos:", font=("Segoe UI", 9, "bold"), bg="#ffffff", fg="#0b69a3")
        lbl_data_title.pack(anchor="w", pady=(0, 2))

        lbl_data_desc = tk.Label(scroll_content, text="   Asesoramiento en estructura, validación y gestión de datos.", font=("Segoe UI", 9), bg="#ffffff", fg="#334155")
        lbl_data_desc.pack(anchor="w", pady=(0, 14))

        # ── Sección 3: Apoyo al Proyecto (Cafecito) ──
        lbl_cafecito_title = tk.Label(scroll_content, text="☕ Apoyo al Proyecto", font=("Segoe UI", 11, "bold"), bg="#ffffff", fg="#0f172a")
        lbl_cafecito_title.pack(anchor="w", pady=(0, 4))

        lbl_cafecito_desc = tk.Label(
            scroll_content,
            text="Si el proyecto te resulta útil y querés colaborar con el desarrollo continuo, podés invitarme un café:",
            font=("Segoe UI", 9),
            bg="#ffffff",
            fg="#334155"
        )
        lbl_cafecito_desc.pack(anchor="w", pady=(0, 6))

        btn_cafecito = tk.Button(
            scroll_content,
            text="☕ Invitame un café en cafecito.app (ulisesjc)",
            font=("Segoe UI", 10, "bold"),
            bg="#d97706",
            fg="#ffffff",
            activebackground="#b45309",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=14,
            pady=8,
            cursor="hand2",
            command=lambda: webbrowser.open("https://cafecito.app/ulisesjc")
        )
        btn_cafecito.pack(anchor="w", pady=(0, 10))

    def iniciar_servidor_uvicorn(self):
        def _run():
            try:
                import uvicorn
                key_path, crt_path, _ = self.rutas_ssl or ensure_ssl_certificates()
                uvicorn.run(
                    "backend.main:app",
                    host="0.0.0.0",
                    port=8000,
                    ssl_keyfile=key_path,
                    ssl_certfile=crt_path,
                    reload=False,
                    log_level="warning"
                )
            except BaseException as e:
                # uvicorn termina con SystemExit si el puerto 8000 está ocupado
                self.error_servidor = str(e) or "puerto 8000 ocupado"
                print(f"[Error Uvicorn] {e!r}")

        self.server_thread = threading.Thread(target=_run, daemon=True, name="FastAPIServerThread")
        self.server_thread.start()

    def monitorear_servidor(self):
        """Muestra en la barra de estado si el servidor web dejó de funcionar y atiende pedidos de mostrar el panel."""
        if self.error_servidor:
            self.lbl_status.config(text=f"● Servidor detenido ({self.error_servidor}): ¿otro programa usa el puerto 8000?", fg="#dc2626")
        if self.pedido_mostrar.is_set():
            self.pedido_mostrar.clear()
            self.mostrar_ventana()
        if self.estado_drive is not None and self.estado_drive is not self.estado_drive_mostrado:
            self.mostrar_estado_drive(self.estado_drive)
        self.root.after(500, self.monitorear_servidor)

    def monitorear_drive(self):
        """Revisa cada minuto si Google Drive para escritorio está disponible (p. ej. recién instalado)."""
        def _consultar():
            try:
                self.estado_drive = gdrive_service.estado_drive_escritorio()
            except Exception:
                pass

        threading.Thread(target=_consultar, daemon=True, name="DetectarGoogleDrive").start()
        self.root.after(60000, self.monitorear_drive)

    def mostrar_estado_drive(self, estado):
        self.estado_drive_mostrado = estado
        if estado.get("activo"):
            color = "#16a34a"
        elif estado.get("instalado"):
            color = "#d97706"
        else:
            color = "#dc2626"
        self.lbl_drive_estado.config(text=estado.get("mensaje", ""), fg=color)
        if estado.get("activo"):
            self.lbl_drive_link.pack_forget()
        elif not self.lbl_drive_link.winfo_ismapped():
            self.lbl_drive_link.pack(anchor="w", after=self.lbl_drive_estado)

    def escuchar_otras_instancias(self, servidor):
        while True:
            try:
                conn, _ = servidor.accept()
                conn.close()
                self.pedido_mostrar.set()
            except OSError:
                return

    def mostrar_ventana(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def actualizar_estado_lan(self):
        lan_ip = obtener_ip_lan()
        texto = f"• Localhost: https://localhost:8000\n• Dominio Oficial LAN: https://recursoshidricos.local:8000\n• IP Local Directa: https://{lan_ip}:8000"
        self.txt_lan_info.config(text=texto)

    def actualizar_estado_certificado(self):
        if ssl_generator.ca_instalada_en_windows():
            self.lbl_cert_estado.config(text="● Certificado de confianza instalado: el navegador muestra el sistema como seguro.", fg="#16a34a")
        else:
            self.lbl_cert_estado.config(text="○ Certificado NO instalado: el navegador mostrará \"No seguro\". Presione 'Instalar Certificado'.", fg="#dc2626")
        if ssl_generator.dominio_local_configurado():
            self.lbl_dominio_estado.config(text="● Dominio recursoshidricos.local configurado en esta PC.", fg="#16a34a")
        else:
            self.lbl_dominio_estado.config(text="○ Dominio recursoshidricos.local sin configurar (opcional, requiere Administrador). Use https://localhost:8000", fg="#d97706")

    def verificar_certificado_inicial(self):
        """Si Windows no confía en la CA, se explica y se instala (mostrando el panel si estaba oculto)."""
        if ssl_generator.ca_instalada_en_windows():
            return
        if self.root.state() == "withdrawn":
            # Inicio automático en segundo plano: sin el certificado el navegador muestra "No seguro",
            # así que se muestra el panel para instalarlo
            self.mostrar_ventana()
        self.instalar_certificado()

    def instalar_certificado(self):
        if ssl_generator.ca_instalada_en_windows():
            self.actualizar_estado_certificado()
            messagebox.showinfo("Certificado de Seguridad", "El certificado ya está instalado: el navegador muestra el sistema como seguro.")
            return
        messagebox.showinfo(
            "Certificado de Seguridad",
            "Para que el navegador muestre el sistema como SEGURO hay que instalar el certificado del sistema.\n\n"
            "Windows va a mostrar una \"Advertencia de seguridad\" preguntando si desea instalar el certificado "
            "\"Sistema DPRH Root CA\".\n\nEn esa ventana presione «Sí»."
        )

        def _instalar():
            ok = ssl_generator.instalar_ca_en_windows()
            self.root.after(0, lambda: self._resultado_instalacion_certificado(ok))

        threading.Thread(target=_instalar, daemon=True, name="InstalarCertificado").start()

    def _resultado_instalacion_certificado(self, ok):
        self.actualizar_estado_certificado()
        if ok:
            messagebox.showinfo("Certificado Instalado", "Certificado instalado correctamente.\n\nCierre el navegador por completo y vuelva a abrirlo para ver el sitio como seguro.")
        else:
            messagebox.showwarning(
                "Certificado No Instalado",
                "El certificado no quedó instalado (¿se presionó «No» en la advertencia de Windows?).\n\n"
                "Puede reintentar con 'Instalar Certificado', o usar 'Dominio .local (Administrador)', que lo instala para todos los usuarios de esta PC."
            )

    def configurar_dominio_admin(self):
        if not messagebox.askyesno(
            "Configurar como Administrador",
            "Esto pide permiso de Administrador de Windows y:\n\n"
            "• Instala el certificado para TODOS los usuarios de esta PC.\n"
            "• Habilita la dirección https://recursoshidricos.local:8000 en esta PC.\n\n"
            "¿Continuar?"
        ):
            return

        def _configurar():
            res = ssl_generator.configurar_como_administrador()
            self.root.after(0, lambda: self._resultado_configuracion_admin(res))

        threading.Thread(target=_configurar, daemon=True, name="ConfigurarAdmin").start()

    def _resultado_configuracion_admin(self, res):
        self.actualizar_estado_certificado()
        if res.get("success"):
            messagebox.showinfo("Configuración Completa", "Certificado y dominio configurados.\n\nCierre el navegador y entre a https://recursoshidricos.local:8000")
        elif res.get("error"):
            messagebox.showwarning("Sin Cambios", res["error"])
        else:
            faltantes = []
            if not res.get("ca_instalada"):
                faltantes.append("• el certificado")
            if not res.get("dominio_configurado"):
                faltantes.append("• el dominio recursoshidricos.local (archivo hosts)")
            messagebox.showwarning("Configuración Incompleta", "No se pudo configurar:\n" + "\n".join(faltantes))

    def exportar_certificado(self):
        try:
            carpeta = ssl_generator.exportar_para_otras_pcs()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar el certificado: {e}")
            return
        try:
            os.startfile(carpeta)
        except Exception:
            pass
        lan_ip = obtener_ip_lan()
        messagebox.showinfo(
            "Certificado Exportado",
            f"Se creó la carpeta:\n{carpeta}\n\n"
            "Cópiela (pendrive o carpeta compartida) a cada PC que entra al sistema y ejecute "
            "'Instalar certificado DPRH.bat' en ella.\n\n"
            f"Esas PCs entran luego a https://{lan_ip}:8000\n\n"
            "(Solo contiene el certificado público: las claves del servidor no salen de esta PC.)"
        )

    def monitorear_estado_tunel(self):
        """Monitorea periódicamente si el túnel Cloudflare o la URL están activos y actualiza los campos de la interfaz."""
        try:
            url_actual = url_tunel_activo()
            if url_actual:
                if self.ent_tunel_url.get().strip() != url_actual:
                    self.ent_tunel_url.delete(0, tk.END)
                    self.ent_tunel_url.insert(0, url_actual)
                self.lbl_tunel_status.config(text="● Túnel Activo en Internet", fg="#16a34a")
            else:
                if not self.ent_tunel_url.get().strip():
                    self.lbl_tunel_status.config(text="○ Túnel Inactivo / Presione 'Iniciar Túnel'", fg="#64748b")
        except Exception:
            pass

        # Repetir la verificación cada 3 segundos en el bucle principal de Tkinter
        self.root.after(3000, self.monitorear_estado_tunel)

    def actualizar_estado_github(self):
        try:
            from backend.services import git_manager_service
            # Cargar URL actual del repositorio
            repo_url = git_manager_service.obtener_repo_url()
            self.ent_repo_url.delete(0, tk.END)
            self.ent_repo_url.insert(0, repo_url)

            # Cargar llave pública
            pub_key = auto_readme_sync.obtener_llave_publica()
            self.ent_pub_key.delete(0, tk.END)
            self.ent_pub_key.insert(0, pub_key)

            if auto_readme_sync.tiene_senal_verde():
                self.lbl_gh_signal.config(text="● Señal Verde: Conexión establecida con éxito.", fg="#16a34a")
                self.btn_forzar_sync.config(state=tk.NORMAL, bg="#0b69a3", cursor="hand2")
            else:
                self.lbl_gh_signal.config(text="○ Sin Señal: Agregue la llave SSH como Deploy Key en GitHub.", fg="#dc2626")
                self.btn_forzar_sync.config(state=tk.DISABLED, bg="#94a3b8", cursor="arrow")
        except Exception:
            pass

    def guardar_url_repositorio(self):
        url = self.ent_repo_url.get().strip()
        from backend.services import git_manager_service
        res = git_manager_service.guardar_repo_url(url)
        if res.get("success"):
            self.actualizar_estado_github()
            messagebox.showinfo("Repositorio Guardado", "URL del repositorio actualizada correctamente.\n\nPor favor presione 'Probar Conexión con GitHub' para validar la nueva URL.")
        else:
            messagebox.showerror("Error", res.get("error", "No se pudo guardar la URL del repositorio."))

    def abrir_navegador_local(self):
        # localhost funciona en cualquier equipo; el dominio .local depende del archivo hosts
        webbrowser.open(URL_LOCAL)

    def accion_iniciar_tunel(self):
        def _iniciar():
            self.lbl_tunel_status.config(text="Iniciando túnel Cloudflare...", fg="#0284c7")
            try:
                url = auto_readme_sync.iniciar_tunel_cloudflare()
                self.tunnel_url = url
                self.ent_tunel_url.delete(0, tk.END)
                self.ent_tunel_url.insert(0, url)
                self.lbl_tunel_status.config(text=f"● Túnel Activo en Internet", fg="#16a34a")
            except Exception as e:
                self.lbl_tunel_status.config(text=f"Error al iniciar túnel: {e}", fg="#dc2626")

        threading.Thread(target=_iniciar, daemon=True).start()

    def accion_detener_tunel(self):
        # Detener el túnel tanto si lo inició el panel como el servidor web
        auto_readme_sync._limpiar_proceso_tunel()
        main_mod = sys.modules.get("backend.main")
        if main_mod is not None:
            main_mod.detener_proceso_tunel()
        threading.Thread(target=auto_readme_sync.marcar_servidor_apagado, daemon=True).start()
        self.ent_tunel_url.delete(0, tk.END)
        self.lbl_tunel_status.config(text="○ Túnel Detenido / Fuera de Línea", fg="#dc2626")

    def copiar_url_remota(self):
        url = self.ent_tunel_url.get().strip()
        if url:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            messagebox.showinfo("Copiado", "Enlace público copiado al portapapeles.")

    def copiar_llave_ssh(self):
        key = self.ent_pub_key.get().strip()
        if key:
            self.root.clipboard_clear()
            self.root.clipboard_append(key)
            messagebox.showinfo("Copiado Exitoso", "La llave pública SSH ha sido copiada al portapapeles.")

    def resetear_llave_ssh(self):
        if messagebox.askyesno("Confirmar Restablecimiento", "¿Está seguro de que desea generar una nueva llave SSH?\n\nAl restablecerla, deberá copiar la nueva llave pública y actualizarla en GitHub (Deploy Keys) para mantener la sincronización activa."):
            try:
                from backend.services import git_manager_service
                res = git_manager_service.resetear_deploy_key()
                if res.get("success"):
                    nueva_key = res.get("clave_publica", "")
                    self.ent_pub_key.delete(0, tk.END)
                    self.ent_pub_key.insert(0, nueva_key)
                    self.actualizar_estado_github()
                    messagebox.showinfo(
                        "Llave SSH Restablecida",
                        "Se ha generado exitosamente una nueva llave Deploy Key (ed25519).\n\nPor favor copie la nueva llave pública y agréguela a GitHub."
                    )
                else:
                    messagebox.showerror("Error al Restablecer", f"No se pudo generar la nueva llave: {res.get('error')}")
            except Exception as e:
                messagebox.showerror("Error de Llave", f"Ocurrió un error al restablecer la llave: {e}")

    def probar_conexion_github(self):
        def _probar():
            res = auto_readme_sync.probar_conexion()
            self.actualizar_estado_github()
            if res.get("autenticado") or res.get("success"):
                messagebox.showinfo("GitHub OK", "¡Conexión SSH autorizada y activa con GitHub!")
            else:
                messagebox.showwarning("Sin Señal", f"No se pudo autenticar con GitHub.\nPor favor copie la llave pública y agréguela como Deploy Key con permiso de escritura.")

        threading.Thread(target=_probar, daemon=True).start()

    def forzar_sincronizacion_portal(self):
        def _sync():
            res = auto_readme_sync.sincronizacion_automatica()
            if res.get("success"):
                messagebox.showinfo("Sincronización Exitosa", f"Portal web actualizado correctamente en GitHub Pages.\nURL Túnel: {res.get('tunnel_url')}")
            else:
                messagebox.showwarning("Aviso", f"No se pudo sincronizar: {res.get('mensaje')}")

        threading.Thread(target=_sync, daemon=True).start()

    def verificar_autoboot(self) -> bool:
        return autoarranque.esta_activo()

    def cambiar_autoboot(self):
        if os.name != 'nt':
            return
        try:
            autoarranque.configurar(self.var_autoboot.get())
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cambiar inicio con Windows: {e}")

    def crear_backup_ahora(self):
        try:
            res = gdrive_service.realizar_backup_gdrive(usuario="PanelTkinter")
            destinos = [res.get("carpeta"), res.get("carpeta_documentos"), res.get("carpeta_drive")]
            lineas = "\n".join(f"• {d}" for d in dict.fromkeys(d for d in destinos if d))
            aviso_drive = "" if res.get("carpeta_drive") else "\n\n(Sin Google Drive para escritorio: la copia quedó sólo en esta PC.)"
            messagebox.showinfo(
                "Copia de Seguridad Creada",
                f"Respaldo generado exitosamente:\n\n• Archivo: {res.get('backup_creado')}\n\nGuardado en:\n{lineas}{aviso_drive}"
            )
        except Exception as e:
            messagebox.showerror("Error Backup", f"No se pudo generar la copia de seguridad: {e}")

    def on_cerrar_aplicacion(self):
        respuesta = messagebox.askyesnocancel(
            "Salir",
            "¿Desea APAGAR el Servidor Central DPRH?\n\n"
            "• Sí: apagar (el sistema dejará de estar disponible en red e internet).\n"
            "• No: ocultar esta ventana y seguir funcionando en segundo plano\n"
            "   (vuelva a abrir el programa para ver este panel)."
        )
        if respuesta is None:
            return
        if respuesta is False:
            self.root.withdraw()
            return
        try:
            auto_readme_sync.marcar_servidor_apagado()
        except Exception:
            pass
        self.root.destroy()
        sys.exit(0)

def tomar_instancia_unica(avisar=True):
    """
    Reserva el puerto local de control. Si ya está tomado, el sistema ya está en ejecución:
    se le avisa para que muestre su panel (salvo avisar=False) y se devuelve None.
    """
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        servidor.bind(("127.0.0.1", PUERTO_INSTANCIA))
        servidor.listen(5)
        return servidor
    except OSError:
        servidor.close()
    if not avisar:
        return None
    try:
        with socket.create_connection(("127.0.0.1", PUERTO_INSTANCIA), timeout=2):
            pass
    except OSError:
        pass
    return None

def activar_autoarranque_inicial():
    """En el ejecutable, la primera vez se activa el inicio automático con Windows."""
    if not getattr(sys, "frozen", False) or os.path.exists(MARCA_AUTOARRANQUE):
        return
    try:
        autoarranque.configurar(True)
        with open(MARCA_AUTOARRANQUE, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception as e:
        print(f"[Autoarranque] No se pudo activar el inicio automático: {e}")

def preparar_tras_instalacion():
    """
    Lo ejecuta el instalador (--registrar-arranque): deja configurado el inicio automático e instala
    el certificado de confianza mientras el usuario está atento a la instalación.
    """
    if os.path.exists(MARCA_AUTOARRANQUE):
        autoarranque.reparar()
    else:
        activar_autoarranque_inicial()
    try:
        ensure_ssl_certificates()
        if not ssl_generator.ca_instalada_en_windows():
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            messagebox.showinfo(
                "Certificado de Seguridad",
                "Para que el navegador muestre el sistema como SEGURO se va a instalar su certificado.\n\n"
                "Windows va a mostrar una \"Advertencia de seguridad\" preguntando si desea instalar el certificado "
                "\"Sistema DPRH Root CA\".\n\nEn esa ventana presione «Sí».",
                parent=root
            )
            ssl_generator.instalar_ca_en_windows()
            root.destroy()
    except Exception as e:
        print(f"[Instalación] No se pudo preparar el certificado: {e!r}")

def main():
    if "--activar-arranque" in sys.argv:
        # "Reparar inicio automatico.bat": fuerza el inicio automático (registro + tarea programada)
        autoarranque.configurar(True)
        with open(MARCA_AUTOARRANQUE, "w", encoding="utf-8") as f:
            f.write("1")
        return
    if "--registrar-arranque" in sys.argv:
        preparar_tras_instalacion()
        return

    is_bg = "--hidden" in sys.argv or "--background" in sys.argv
    # Al iniciar sesión pueden lanzarlo tanto el registro como la tarea programada: la segunda
    # ejecución en segundo plano se cierra sin mostrar el panel de la primera
    socket_instancia = tomar_instancia_unica(avisar=not is_bg)
    if socket_instancia is None:
        # Ya hay un servidor funcionando: esa instancia muestra su panel
        return
    activar_autoarranque_inicial()
    # Restaura el inicio automático si algo lo quitó (registro o tarea programada)
    threading.Thread(target=autoarranque.reparar, daemon=True, name="RepararAutoarranque").start()
    root = tk.Tk()
    ServidorDPRHApp(root, is_background=is_bg, socket_instancia=socket_instancia)
    root.mainloop()

if __name__ == "__main__":
    main()
