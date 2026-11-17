"""
backend/ssl_generator.py
Certificados HTTPS del servidor local (LAN).

- Se crea UNA autoridad certificadora propia (Root CA) por instalación: certs/ca.crt + certs/ca.key.
  La clave de la CA nunca sale de la carpeta de datos del equipo (%LOCALAPPDATA%\\SistemaDPRH).
- Con esa CA se firma el certificado del servidor (certs/server.crt + certs/server.key), que cubre
  localhost, recursoshidricos.local, el nombre del equipo y su IP de red. Se regenera solo si vence,
  si cambia la IP o si no corresponde a la CA actual.
- Para que el navegador muestre el candado, Windows tiene que confiar en la CA:
  instalar_ca_en_windows() la agrega al almacén "Raíz de confianza" del usuario (Windows pide
  confirmar con "Sí") y ca_instalada_en_windows() verifica que realmente haya quedado instalada.
- configurar_como_administrador() (pide permiso UAC) instala la CA para todos los usuarios del
  equipo y agrega recursoshidricos.local al archivo hosts.
- exportar_para_otras_pcs() arma una carpeta con ca.crt y un .bat para instalarla en las demás PCs.
"""

import os
import ssl
import socket
import datetime
import ipaddress
import subprocess

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    HAS_CRYPTOGRAPHY = True
except Exception as _e_crypto:
    HAS_CRYPTOGRAPHY = False

from backend.paths import DATA_DIR

CERTS_DIR = os.path.join(DATA_DIR, "certs")
CA_CRT_PATH = os.path.join(CERTS_DIR, "ca.crt")
CA_KEY_PATH = os.path.join(CERTS_DIR, "ca.key")
SERVER_CRT_PATH = os.path.join(CERTS_DIR, "server.crt")
SERVER_KEY_PATH = os.path.join(CERTS_DIR, "server.key")

DOMINIO_LOCAL = "recursoshidricos.local"
HOSTS_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")

DIAS_CA = 3650
# 825 días: el máximo que aceptan todos los navegadores para certificados de servidor
DIAS_SERVIDOR = 825

# certutil y powershell sin ventana de consola
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def obtener_ip_lan() -> str:
    """Detecta dinámicamente la IP LAN activa de la máquina."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # Sin red (p. ej. recién iniciado Windows): solo hay acceso local
        return "127.0.0.1"


def _ahora():
    return datetime.datetime.now(datetime.timezone.utc)


def _guardar_clave(clave, ruta):
    with open(ruta, "wb") as f:
        f.write(clave.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))


def _guardar_certificado(cert, ruta):
    with open(ruta, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def _cargar_ca_existente(ca_crt_path, ca_key_path):
    """Devuelve (cert, clave) de la CA si ambos archivos existen, son pareja y la CA sigue vigente."""
    if not (os.path.exists(ca_crt_path) and os.path.exists(ca_key_path)):
        return None
    try:
        with open(ca_crt_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read())
        with open(ca_key_path, "rb") as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None)
        # Versiones anteriores podían dejar el ca.crt de mkcert junto a un ca.key de otra CA
        if ca_cert.public_key().public_numbers() != ca_key.public_key().public_numbers():
            print("[SSL] ca.crt y ca.key no corresponden a la misma CA: se crea una nueva.")
            return None
        if not ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            return None
        if ca_cert.not_valid_after_utc - _ahora() < datetime.timedelta(days=DIAS_SERVIDOR):
            return None
        return ca_cert, ca_key
    except Exception:
        return None


def _crear_ca(ca_crt_path, ca_key_path):
    print("[SSL] Generando Autoridad Certificadora Local (Root CA)...")
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    equipo = socket.gethostname() or "PC"
    ca_subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Jujuy"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Recursos Hidricos CA"),
        # El nombre del equipo distingue la CA de cada instalación en el almacén de Windows
        x509.NameAttribute(NameOID.COMMON_NAME, f"Sistema DPRH Root CA ({equipo})"[:64]),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_ahora() - datetime.timedelta(days=1))
        .not_valid_after(_ahora() + datetime.timedelta(days=DIAS_CA))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _guardar_clave(ca_key, ca_key_path)
    _guardar_certificado(ca_cert, ca_crt_path)
    return ca_cert, ca_key


def obtener_o_crear_ca(ca_crt_path=None, ca_key_path=None):
    ca_crt_path, ca_key_path = ca_crt_path or CA_CRT_PATH, ca_key_path or CA_KEY_PATH
    return _cargar_ca_existente(ca_crt_path, ca_key_path) or _crear_ca(ca_crt_path, ca_key_path)


def _nombres_del_servidor():
    """(dominios, ips) que debe cubrir el certificado del servidor."""
    dominios = [DOMINIO_LOCAL, f"*.{DOMINIO_LOCAL}", "localhost"]
    equipo = (socket.gethostname() or "").strip()
    if equipo and equipo.lower() not in [d.lower() for d in dominios]:
        dominios.append(equipo)

    ips = ["127.0.0.1", "::1", obtener_ip_lan()]
    try:
        for info in socket.getaddrinfo(equipo, None, socket.AF_INET):
            ips.append(info[4][0])
    except Exception:
        pass
    ips_validas = []
    for ip in ips:
        try:
            ip_obj = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if ip_obj not in ips_validas:
            ips_validas.append(ip_obj)
    return dominios, ips_validas


def _crear_certificado_servidor(ca_cert, ca_key, server_crt_path, server_key_path):
    print("[SSL] Generando certificado del servidor...")
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    dominios, ips = _nombres_del_servidor()
    server_subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Jujuy"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Recursos Hidricos"),
        x509.NameAttribute(NameOID.COMMON_NAME, DOMINIO_LOCAL),
    ])
    san = [x509.DNSName(d) for d in dominios] + [x509.IPAddress(ip) for ip in ips]
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_ahora() - datetime.timedelta(days=1))
        .not_valid_after(_ahora() + datetime.timedelta(days=DIAS_SERVIDOR))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _guardar_clave(server_key, server_key_path)
    _guardar_certificado(server_cert, server_crt_path)


def certificado_vigente(server_crt_path: str, server_key_path: str, ca_crt_path: str = None, dias_margen: int = 30) -> bool:
    """True si el certificado del servidor existe, fue firmado por la CA actual, no vence pronto y cubre la IP LAN actual."""
    ca_crt_path = ca_crt_path or CA_CRT_PATH
    if not HAS_CRYPTOGRAPHY or not all(os.path.exists(p) for p in (server_crt_path, server_key_path, ca_crt_path)):
        return False
    try:
        with open(server_crt_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        with open(server_key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        with open(ca_crt_path, "rb") as f:
            ca_cert = x509.load_pem_x509_certificate(f.read())
        if cert.public_key().public_numbers() != key.public_key().public_numbers():
            return False
        cert.verify_directly_issued_by(ca_cert)
        if cert.not_valid_after_utc - _ahora() < datetime.timedelta(days=dias_margen):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips = [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
        ip_lan = obtener_ip_lan()
        # Sin red todavía no se conoce la IP: se conserva el certificado en lugar de rehacerlo sin ella
        return ip_lan == "127.0.0.1" or ip_lan in ips
    except Exception:
        return False


def ensure_ssl_certificates(instalar_ca: bool = False):
    """
    Garantiza que existan la CA local y un certificado de servidor válido firmado por ella.
    Con instalar_ca=True además registra la CA en Windows si todavía no es de confianza
    (el panel lo hace por su cuenta para poder avisarle antes al usuario).
    """
    os.makedirs(CERTS_DIR, exist_ok=True)
    if HAS_CRYPTOGRAPHY:
        ca = _cargar_ca_existente(CA_CRT_PATH, CA_KEY_PATH)
        if ca is None or not certificado_vigente(SERVER_CRT_PATH, SERVER_KEY_PATH, CA_CRT_PATH):
            ca_cert, ca_key = ca or _crear_ca(CA_CRT_PATH, CA_KEY_PATH)
            _crear_certificado_servidor(ca_cert, ca_key, SERVER_CRT_PATH, SERVER_KEY_PATH)
    else:
        print("[SSL] Librería cryptography no disponible: no se pueden generar certificados.")

    if instalar_ca and not ca_instalada_en_windows():
        instalar_ca_en_windows()

    return SERVER_KEY_PATH, SERVER_CRT_PATH, CA_CRT_PATH


# ── Confianza en Windows ──────────────────────────────────────────────

def _ca_der(ca_crt_path):
    with open(ca_crt_path, "r", encoding="ascii") as f:
        return ssl.PEM_cert_to_DER_cert(f.read())


def ca_instalada_en_windows(ca_crt_path: str = None) -> bool:
    """True si la CA está en "Entidades de certificación raíz de confianza" (del usuario o del equipo)."""
    ca_crt_path = ca_crt_path or CA_CRT_PATH
    if os.name != "nt" or not os.path.exists(ca_crt_path):
        return False
    try:
        der = _ca_der(ca_crt_path)
        return any(cert == der for cert, _enc, _uso in ssl.enum_certificates("ROOT"))
    except Exception as e:
        print(f"[SSL Aviso] No se pudo consultar el almacén de certificados de Windows: {e}")
        return False


def instalar_ca_en_windows(ca_crt_path: str = None) -> bool:
    """
    Agrega la CA a la raíz de confianza del usuario actual (no requiere Administrador).
    Windows muestra una "Advertencia de seguridad" que el usuario debe aceptar con "Sí".
    Devuelve True solo si después de eso la CA realmente quedó instalada.
    """
    ca_crt_path = ca_crt_path or CA_CRT_PATH
    if os.name != "nt" or not os.path.exists(ca_crt_path):
        return False
    if ca_instalada_en_windows(ca_crt_path):
        return True
    try:
        res = subprocess.run(["certutil", "-user", "-addstore", "-f", "Root", ca_crt_path],
                             capture_output=True, check=False, creationflags=_SIN_VENTANA)
        if res.returncode != 0:
            print(f"[SSL Aviso] certutil no pudo registrar la Root CA (código {res.returncode}).")
    except Exception as e:
        print(f"[SSL Aviso] No se pudo registrar el certificado en Windows: {e}")
    instalada = ca_instalada_en_windows(ca_crt_path)
    if instalada:
        print("[SSL] Certificado Root CA instalado en el Almacén de Confianza de Windows.")
    return instalada


# ── Dominio recursoshidricos.local y configuración con Administrador ─────────

def dominio_local_configurado(hosts_path: str = None) -> bool:
    """True si el archivo hosts ya resuelve recursoshidricos.local en este equipo."""
    hosts_path = hosts_path or HOSTS_PATH
    try:
        with open(hosts_path, "r", encoding="utf-8", errors="ignore") as f:
            for linea in f:
                partes = linea.split("#", 1)[0].split()
                if len(partes) >= 2 and DOMINIO_LOCAL in [p.lower() for p in partes[1:]]:
                    return True
    except Exception:
        pass
    return False


def _bat_configuracion_admin(ip_dominio: str) -> str:
    """
    Comandos que requieren Administrador: CA en la raíz de confianza del EQUIPO (todos los usuarios)
    y recursoshidricos.local en el archivo hosts. Usan ca.crt de la misma carpeta que el .bat.
    """
    return "\r\n".join([
        "@echo off",
        'certutil -addstore -f Root "%~dp0ca.crt" >nul',
        'set "HOSTS=%SystemRoot%\\System32\\drivers\\etc\\hosts"',
        f'findstr /I /C:" {DOMINIO_LOCAL}" "%HOSTS%" >nul 2>&1 || (',
        "  echo.>>\"%HOSTS%\"",
        f"  echo {ip_dominio} {DOMINIO_LOCAL}>>\"%HOSTS%\"",
        ")",
        "ipconfig /flushdns >nul",
        "",
    ])


def configurar_como_administrador(ca_crt_path: str = None) -> dict:
    """
    Pide permiso de Administrador (UAC) y, con él, instala la CA para todos los usuarios del equipo
    y agrega recursoshidricos.local (127.0.0.1) al archivo hosts.
    """
    ca_crt_path = ca_crt_path or CA_CRT_PATH
    if os.name != "nt":
        return {"success": False, "error": "Solo disponible en Windows."}
    if not os.path.exists(ca_crt_path):
        return {"success": False, "error": "Todavía no se generó el certificado del sistema."}

    bat_path = os.path.join(os.path.dirname(ca_crt_path), "configurar_como_administrador.bat")
    with open(bat_path, "w", encoding="ascii", newline="") as f:
        f.write(_bat_configuracion_admin("127.0.0.1"))

    ruta_ps = bat_path.replace("'", "''")
    comando = f"Start-Process -FilePath '{ruta_ps}' -Verb RunAs -Wait -WindowStyle Hidden"
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                             capture_output=True, check=False, creationflags=_SIN_VENTANA)
    except Exception as e:
        return {"success": False, "error": str(e)}
    if res.returncode != 0:
        return {"success": False, "error": "No se otorgó el permiso de Administrador."}

    ca_ok = ca_instalada_en_windows(ca_crt_path)
    dominio_ok = dominio_local_configurado()
    return {"success": ca_ok and dominio_ok, "ca_instalada": ca_ok, "dominio_configurado": dominio_ok}


# ── Instalación en las otras PCs de la red ─────────────────────────────

def _bat_otras_pcs(ip_servidor: str) -> str:
    """Instalador para las PCs cliente: se eleva solo; sin Administrador instala la CA solo para el usuario."""
    return "\r\n".join([
        "@echo off",
        "title Instalar certificado del Sistema DPRH",
        "net session >nul 2>&1",
        "if %errorlevel% neq 0 (",
        "  echo Windows pedira permiso de Administrador...",
        "  powershell -NoProfile -Command \"Start-Process -FilePath '%~f0' -Verb RunAs\" >nul 2>&1",
        "  if errorlevel 1 goto sin_admin",
        "  exit /b",
        ")",
        'certutil -addstore -f Root "%~dp0ca.crt" >nul',
        "if errorlevel 1 goto error",
        'set "HOSTS=%SystemRoot%\\System32\\drivers\\etc\\hosts"',
        f'findstr /I /C:" {DOMINIO_LOCAL}" "%HOSTS%" >nul 2>&1 || (',
        "  echo.>>\"%HOSTS%\"",
        f"  echo {ip_servidor} {DOMINIO_LOCAL}>>\"%HOSTS%\"",
        ")",
        "ipconfig /flushdns >nul",
        "echo.",
        "echo Listo: certificado instalado para todos los usuarios de esta PC.",
        f"echo Cierre el navegador y entre a https://{DOMINIO_LOCAL}:8000 o https://{ip_servidor}:8000",
        "pause",
        "exit /b",
        ":sin_admin",
        "echo.",
        "echo Sin permiso de Administrador: se instalara solo para este usuario.",
        "echo Windows mostrara una Advertencia de seguridad: presione SI.",
        'certutil -user -addstore -f Root "%~dp0ca.crt" >nul',
        "if errorlevel 1 goto error",
        "echo.",
        f"echo Listo. Cierre el navegador y entre a https://{ip_servidor}:8000",
        "pause",
        "exit /b",
        ":error",
        "echo.",
        "echo No se pudo instalar el certificado.",
        "pause",
        "",
    ])


def carpeta_escritorio() -> str:
    """Escritorio real del usuario (también si está redirigido a OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_DESKTOPDIRECTORY = 0x10
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def exportar_para_otras_pcs(destino_padre: str = None, ca_crt_path: str = None) -> str:
    """
    Crea la carpeta "Certificado Sistema DPRH" (en el Escritorio por defecto) con ca.crt y el .bat
    para instalarlo en las demás PCs. Solo copia el certificado público: nunca las claves.
    """
    ca_crt_path = ca_crt_path or CA_CRT_PATH
    if not os.path.exists(ca_crt_path):
        raise FileNotFoundError("Todavía no se generó el certificado del sistema.")
    carpeta = os.path.join(destino_padre or carpeta_escritorio(), "Certificado Sistema DPRH")
    os.makedirs(carpeta, exist_ok=True)
    with open(ca_crt_path, "rb") as f_in, open(os.path.join(carpeta, "ca.crt"), "wb") as f_out:
        f_out.write(f_in.read())
    with open(os.path.join(carpeta, "Instalar certificado DPRH.bat"), "w", encoding="ascii", newline="") as f:
        f.write(_bat_otras_pcs(obtener_ip_lan()))
    return carpeta


if __name__ == "__main__":
    ensure_ssl_certificates(instalar_ca=True)
