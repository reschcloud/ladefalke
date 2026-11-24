"""HTTPS ohne Reverse Proxy: selbstsigniertes Zertifikat wird einmal erzeugt und danach wiederverwendet."""
import ipaddress
import ssl

import pytest
from cryptography import x509

from app import tls
from app.tls import sicherstellen


def test_zertifikat_erzeugen_und_behalten(tmp_path):
    cert, key = sicherstellen(tmp_path / "tls", "ladefalke.lan 192.168.1.20")
    z = x509.load_pem_x509_certificate(cert.read_bytes())
    san = z.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert {"localhost", "ladefalke.lan"} <= set(san.get_values_for_type(x509.DNSName))
    assert ipaddress.ip_address("192.168.1.20") in san.get_values_for_type(x509.IPAddress)
    assert (key.stat().st_mode & 0o777) == 0o600
    ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(cert, key)  # passt zusammen
    vorher = cert.read_bytes()
    assert sicherstellen(tmp_path / "tls")[0].read_bytes() == vorher  # bleibt erhalten (Browser-Ausnahme)


def test_eigenes_zertifikat_wird_verwendet(tmp_path):
    (tmp_path / "cert.pem").write_text("eigenes")
    (tmp_path / "key.pem").write_text("eigener")
    cert, key = sicherstellen(tmp_path)
    assert cert.read_text() == "eigenes" and key.read_text() == "eigener"


@pytest.mark.parametrize("name, gueltig", [
    ("192.168.1.20", True), ("::1", True), ("server.firma.lan", True), ("macminiserver", True),
    ("192.168.1.300", False), ("192.168.1.", False), ("sërver", False), ("https://10.0.0.5", False),
    ("server.lan:8000", False), ("-server", False),
])
def test_gueltiger_name(name, gueltig):
    assert tls.gueltiger_name(name) is gueltig


def test_vertippte_namen_werden_uebersprungen(tmp_path, capsys):
    cert, _ = tls.sicherstellen(tmp_path, "sërver 192.168.1.300 server.lan")
    zertifikat = x509.load_pem_x509_certificate(cert.read_bytes())
    namen = zertifikat.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "server.lan" in namen.get_values_for_type(x509.DNSName)
    assert "sërver" in capsys.readouterr().err
