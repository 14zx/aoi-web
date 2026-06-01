"""Генерация самоподписанного сертификата для локального HTTPS (разработка).

Создаёт ``certs/key.pem`` и ``certs/cert.pem`` с SAN для localhost, 127.0.0.1,
::1 и текущего IPv4 в LAN (чтобы можно было зайти с телефона по https://192.168.x.x).

Запуск::

    python -m scripts.generate_dev_https_certs
    python -m scripts.generate_dev_https_certs --force   # перезаписать существующие

Браузер покажет предупреждение о недоверенном сертификате — это нормально для dev;
для доверия используйте mkcert или установите корневой CA (не входит в этот скрипт).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from scripts.lan_ip import get_lan_ipv4


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description="Сертификаты dev HTTPS для АОИ-Web")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Пересоздать файлы certs/cert.pem и certs/key.pem",
    )
    args = parser.parse_args()

    root = _repo_root()
    cert_dir = root / "certs"
    key_path = cert_dir / "key.pem"
    cert_path = cert_dir / "cert.pem"

    if key_path.exists() and cert_path.exists() and not args.force:
        print("Файлы certs уже есть. Используйте --force для пересоздания.")
        return 0

    cert_dir.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    san_list = [
        x509.DNSName("localhost"),
        x509.IPAddress(ip_address("127.0.0.1")),
        x509.IPAddress(ip_address("::1")),
    ]
    lan = get_lan_ipv4()
    if lan:
        san_list.append(x509.IPAddress(ip_address(lan)))

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AOI-Web development"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=False,
                crl_sign=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
    )

    cert = builder.sign(key, hashes.SHA256())

    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    key_path.write_bytes(key_pem)
    cert_path.write_bytes(cert_pem)

    print(f"Записано: {key_path}")
    print(f"Записано: {cert_path}")
    if lan:
        print(f"В сертификат добавлен LAN IPv4: {lan} (доступ с телефона: https://{lan}:8000/)")
    else:
        print("LAN IPv4 не определён — в SAN только localhost / loopback.")
    print(
        "В браузере возможно предупреждение о безопасности: для dev это ожидаемо "
        "(Дополнительные сведения / Advanced - Proceed)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
