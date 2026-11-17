"""
Certificados HTTPS: CA propia + certificado de servidor, verificación real de la confianza en Windows,
exportación para otras PCs y que el paquete compilado no lleve claves.
"""
import importlib.util
import os
import subprocess
import sys

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from fastapi.testclient import TestClient

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend import ssl_generator


@pytest.fixture
def certs(tmp_path, monkeypatch):
    """Redirige los certificados a una carpeta temporal y nunca toca el almacén de Windows."""
    rutas = {
        "CERTS_DIR": str(tmp_path),
        "CA_CRT_PATH": str(tmp_path / "ca.crt"),
        "CA_KEY_PATH": str(tmp_path / "ca.key"),
        "SERVER_CRT_PATH": str(tmp_path / "server.crt"),
        "SERVER_KEY_PATH": str(tmp_path / "server.key"),
    }
    for nombre, valor in rutas.items():
        monkeypatch.setattr(ssl_generator, nombre, valor)
    monkeypatch.setattr(ssl_generator, "obtener_ip_lan", lambda: "10.20.30.40")

    def _sin_certutil(*_a, **_k):
        raise AssertionError("no se debe ejecutar certutil/powershell en este test")

    monkeypatch.setattr(ssl_generator.subprocess, "run", _sin_certutil)
    return rutas


def _cargar(ruta):
    with open(ruta, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def test_genera_ca_y_servidor_firmado_por_ella(certs):
    ssl_generator.ensure_ssl_certificates()

    ca = _cargar(certs["CA_CRT_PATH"])
    server = _cargar(certs["SERVER_CRT_PATH"])
    with open(certs["CA_KEY_PATH"], "rb") as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None)

    assert ca.public_key().public_numbers() == ca_key.public_key().public_numbers()
    server.verify_directly_issued_by(ca)

    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
    assert not server.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    eku = server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku

    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {"localhost", "recursoshidricos.local"} <= set(san.get_values_for_type(x509.DNSName))
    assert {"127.0.0.1", "10.20.30.40"} <= {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}


def test_reutiliza_certificados_vigentes(certs):
    ssl_generator.ensure_ssl_certificates()
    serie_ca = _cargar(certs["CA_CRT_PATH"]).serial_number
    serie_server = _cargar(certs["SERVER_CRT_PATH"]).serial_number

    ssl_generator.ensure_ssl_certificates()
    assert _cargar(certs["CA_CRT_PATH"]).serial_number == serie_ca
    assert _cargar(certs["SERVER_CRT_PATH"]).serial_number == serie_server


def test_cambio_de_ip_regenera_solo_el_servidor(certs, monkeypatch):
    ssl_generator.ensure_ssl_certificates()
    serie_ca = _cargar(certs["CA_CRT_PATH"]).serial_number

    monkeypatch.setattr(ssl_generator, "obtener_ip_lan", lambda: "10.99.99.99")
    ssl_generator.ensure_ssl_certificates()

    assert _cargar(certs["CA_CRT_PATH"]).serial_number == serie_ca
    san = _cargar(certs["SERVER_CRT_PATH"]).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "10.99.99.99" in {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}


def test_sin_red_al_iniciar_windows_conserva_el_certificado(certs, monkeypatch):
    ssl_generator.ensure_ssl_certificates()
    serie_server = _cargar(certs["SERVER_CRT_PATH"]).serial_number

    monkeypatch.setattr(ssl_generator, "obtener_ip_lan", lambda: "127.0.0.1")
    ssl_generator.ensure_ssl_certificates()
    assert _cargar(certs["SERVER_CRT_PATH"]).serial_number == serie_server


def test_ca_crt_y_ca_key_desparejados_se_reemplazan(certs, tmp_path):
    """Caso de versiones anteriores: ca.crt de mkcert junto a un ca.key de otra CA."""
    otra = tmp_path / "otra"
    otra.mkdir()
    ssl_generator._crear_ca(str(otra / "ca.crt"), str(otra / "ca.key"))
    ssl_generator.ensure_ssl_certificates()
    os.replace(otra / "ca.crt", certs["CA_CRT_PATH"])

    assert not ssl_generator.certificado_vigente(certs["SERVER_CRT_PATH"], certs["SERVER_KEY_PATH"], certs["CA_CRT_PATH"])

    ssl_generator.ensure_ssl_certificates()
    ca = _cargar(certs["CA_CRT_PATH"])
    with open(certs["CA_KEY_PATH"], "rb") as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None)
    assert ca.public_key().public_numbers() == ca_key.public_key().public_numbers()
    _cargar(certs["SERVER_CRT_PATH"]).verify_directly_issued_by(ca)


def test_instalar_ca_no_informa_exito_si_windows_no_la_tiene(certs, monkeypatch):
    """certutil puede terminar bien y aun así la CA no quedar instalada (p. ej. el usuario presionó "No")."""
    ssl_generator.ensure_ssl_certificates()
    monkeypatch.setattr(ssl_generator.os, "name", "nt")
    monkeypatch.setattr(ssl_generator, "ca_instalada_en_windows", lambda *_a: False)
    monkeypatch.setattr(ssl_generator.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0))

    assert ssl_generator.instalar_ca_en_windows() is False


def test_instalar_ca_no_ejecuta_certutil_si_ya_esta_instalada(certs, monkeypatch):
    ssl_generator.ensure_ssl_certificates()
    monkeypatch.setattr(ssl_generator.os, "name", "nt")
    monkeypatch.setattr(ssl_generator, "ca_instalada_en_windows", lambda *_a: True)

    assert ssl_generator.instalar_ca_en_windows() is True


@pytest.mark.skipif(os.name != "nt", reason="almacén de certificados de Windows")
def test_ca_nueva_no_figura_como_instalada(certs):
    ssl_generator.ensure_ssl_certificates()
    assert ssl_generator.ca_instalada_en_windows(certs["CA_CRT_PATH"]) is False


def test_dominio_local_en_hosts(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("# 127.0.0.1 recursoshidricos.local\n127.0.0.1 localhost\n", encoding="utf-8")
    assert not ssl_generator.dominio_local_configurado(str(hosts))

    hosts.write_text("127.0.0.1\trecursoshidricos.local  # Sistema DPRH\n", encoding="utf-8")
    assert ssl_generator.dominio_local_configurado(str(hosts))


def test_exportar_para_otras_pcs_solo_lleva_el_certificado_publico(certs, tmp_path):
    ssl_generator.ensure_ssl_certificates()
    carpeta = ssl_generator.exportar_para_otras_pcs(str(tmp_path / "escritorio"))

    assert sorted(os.listdir(carpeta)) == ["Instalar certificado DPRH.bat", "ca.crt"]
    for nombre in os.listdir(carpeta):
        with open(os.path.join(carpeta, nombre), "rb") as f:
            assert b"PRIVATE KEY" not in f.read()

    with open(os.path.join(carpeta, "Instalar certificado DPRH.bat"), "rb") as f:
        bat = f.read()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")
    assert b"10.20.30.40 recursoshidricos.local" in bat
    assert b'certutil -addstore -f Root "%~dp0ca.crt"' in bat


def test_bat_de_administrador_instala_ca_y_dominio():
    bat = ssl_generator._bat_configuracion_admin("127.0.0.1")
    assert 'certutil -addstore -f Root "%~dp0ca.crt"' in bat
    assert "echo 127.0.0.1 recursoshidricos.local>>" in bat


def test_endpoint_publico_del_certificado_ca(certs):
    from backend.main import app

    ssl_generator.ensure_ssl_certificates()
    res = TestClient(app).get("/api/certificado/ca.crt")

    assert res.status_code == 200
    assert b"BEGIN CERTIFICATE" in res.content
    assert b"PRIVATE KEY" not in res.content


def _cargar_build_exe():
    spec = importlib.util.spec_from_file_location("build_exe", os.path.join(ROOT_DIR, "scripts", "build_exe.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_build_detecta_claves_en_el_paquete(tmp_path):
    build_exe = _cargar_build_exe()
    app_dir = tmp_path / "SistemaDPRH"
    (app_dir / "_internal").mkdir(parents=True)
    (app_dir / "_internal" / "cacert.pem").write_text("-----BEGIN CERTIFICATE-----\n", encoding="ascii")
    assert build_exe.buscar_datos_sensibles(str(app_dir)) == []

    (app_dir / "_internal" / "algo.dat").write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="ascii")
    (app_dir / "server.key").write_text("x", encoding="ascii")
    hallados = {os.path.basename(p) for p in build_exe.buscar_datos_sensibles(str(app_dir))}
    assert hallados == {"algo.dat", "server.key"}
