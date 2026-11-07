"""Generate self-signed SSL/TLS certificates with SAN for localhost & local IP."""
import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SSL_DIR = os.path.join(BASE_DIR, "data", "ssl")
CERT_FILE = os.path.join(SSL_DIR, "cert.pem")
KEY_FILE = os.path.join(SSL_DIR, "key.pem")


def generate_ssl_certificates(local_ip="192.168.1.2"):
    os.makedirs(SSL_DIR, exist_ok=True)

    # Generate Private Key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    # Subject & Issuer
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ID"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Money Tracker by Mas'ud"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Money Tracker by Mas'ud"),
    ])

    # Subject Alternative Names (SAN) for localhost & local network IP
    san_list = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    try:
        san_list.append(x509.IPAddress(ipaddress.IPv4Address(local_ip)))
    except Exception:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650)) # 10 years validity
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .sign(private_key, hashes.SHA256(), default_backend())
    )

    # Write Private Key
    with open(KEY_FILE, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    # Write Certificate
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"SSL Certificate & Key successfully created in {SSL_DIR}:")
    print(f" - Cert: {CERT_FILE}")
    print(f" - Key:  {KEY_FILE}")
    return CERT_FILE, KEY_FILE


if __name__ == "__main__":
    generate_ssl_certificates()
