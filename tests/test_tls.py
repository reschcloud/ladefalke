"""HTTPS ohne Reverse Proxy: selbstsigniertes Zertifikat wird einmal erzeugt und danach wiederverwendet."""
import ipaddress
import ssl

from cryptography import x509

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
