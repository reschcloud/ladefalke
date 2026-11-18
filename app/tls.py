"""HTTPS ohne Reverse Proxy: selbstsigniertes Zertifikat, beim ersten Start erzeugt.

Liegen in TLS_DIR (Standard /data/tls, im Compose ./data/tls) schon `cert.pem` und `key.pem`, werden sie verwendet –
so lässt sich auch ein eigenes Zertifikat hinterlegen. Sonst wird ein selbstsigniertes Zertifikat (EC P-256, 10 Jahre)
für localhost, den Rechnernamen, 127.0.0.1 und die Namen/IPs aus TLS_NAMEN erzeugt. Es bleibt über Neustarts erhalten,
damit die Ausnahme im Browser gültig bleibt. Im Echtbetrieb hinter einem Reverse Proxy: TLS=aus (siehe start.sh).

Aufruf: `python -m app.tls` (gibt die Pfade von Zertifikat und Schlüssel aus).
"""
import ipaddress
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

GUELTIG_TAGE = 3650


def _alt_namen(namen: list[str]) -> list[x509.GeneralName]:
    ergebnis, gesehen = [], set()
    for name in namen:
        name = name.strip()
        if not name or name.lower() in gesehen:
            continue
        gesehen.add(name.lower())
        try:
            ergebnis.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            ergebnis.append(x509.DNSName(name))
    return ergebnis


def erzeugen(cert_pfad: Path, key_pfad: Path, namen: list[str]) -> None:
    """Selbstsigniertes Zertifikat für die angegebenen DNS-Namen und IP-Adressen schreiben."""
    schluessel = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, namen[0]),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ladefalke (selbstsigniert)")])
    jetzt = datetime.now(timezone.utc)
    zertifikat = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(schluessel.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(jetzt - timedelta(minutes=5)).not_valid_after(jetzt + timedelta(days=GUELTIG_TAGE))
        .add_extension(x509.SubjectAlternativeName(_alt_namen(namen)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(schluessel, hashes.SHA256()))
    cert_pfad.parent.mkdir(parents=True, exist_ok=True)
    key_pfad.write_bytes(schluessel.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()))
    key_pfad.chmod(0o600)
    cert_pfad.write_bytes(zertifikat.public_bytes(serialization.Encoding.PEM))


def sicherstellen(verzeichnis: Path, zusaetzliche_namen: str = "") -> tuple[Path, Path]:
    """Pfade zu Zertifikat und Schlüssel; erzeugt beides, wenn eines fehlt."""
    cert, key = verzeichnis / "cert.pem", verzeichnis / "key.pem"
    if not (cert.exists() and key.exists()):
        namen = ["localhost", socket.gethostname(), "127.0.0.1", "::1"] + zusaetzliche_namen.replace(",", " ").split()
        erzeugen(cert, key, namen)
    return cert, key


if __name__ == "__main__":
    cert, key = sicherstellen(Path(os.environ.get("TLS_DIR", "/data/tls")), os.environ.get("TLS_NAMEN", ""))
    print(cert, key)
