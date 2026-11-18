"""Healthcheck im Container (compose.yaml): fragt /gesund der laufenden App ab – Exit-Code 0 = gesund.

HTTPS mit selbstsigniertem Zertifikat (TLS=an, Standard) bzw. HTTP hinter einem Reverse Proxy (TLS=aus).
"""
import os
import ssl
import sys
import urllib.request


def main() -> int:
    tls = os.environ.get("TLS", "an") != "aus"
    url = f"{'https' if tls else 'http'}://127.0.0.1:8000/gesund"
    kontext = ssl._create_unverified_context() if tls else None  # eigenes, selbstsigniertes Zertifikat
    try:
        with urllib.request.urlopen(url, timeout=5, context=kontext) as antwort:
            return 0 if antwort.status == 200 else 1
    except Exception as e:  # noqa: BLE001
        print(f"nicht gesund: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
