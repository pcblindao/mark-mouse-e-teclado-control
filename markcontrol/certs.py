import ipaddress
import os
import socket
import ssl
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .security import private_ip


def data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        path = Path(base) / "MarkControl"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        if not base.is_absolute():
            base = Path.home() / ".local" / "share"
        path = base / "markcontrol"
    return path


def _addresses(host):
    addresses = {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
    if private_ip(host):
        addresses.add(ipaddress.ip_address(host))
    # Em UDP, connect só consulta a rota local. Nenhum datagrama é enviado.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.connect(("255.255.255.255", 47800))
            ip = sock.getsockname()[0]
            if private_ip(ip):
                addresses.add(ipaddress.ip_address(ip))
        except OSError:
            pass
    return addresses


def _generate(path, host, extra):
    names = [x509.DNSName("localhost")]
    names.extend(x509.IPAddress(ip) for ip in sorted(
        _addresses(host) | set(extra), key=lambda ip: (ip.version, int(ip))
    ))
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Mark Mouse e Teclado Control")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            temp.write(certificate.public_bytes(serialization.Encoding.PEM))
            temp.write(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            temp.close()
            try:
                os.link(temp_path, path)
            except FileExistsError:
                pass  # Outra instância terminou primeiro.
        finally:
            temp.close()
            temp_path.unlink(missing_ok=True)


def _read_certificate(identity):
    pem = identity.read_bytes().split(b"-----BEGIN PRIVATE KEY-----", 1)[0]
    return x509.load_pem_x509_certificate(pem)


def local_fingerprint():
    try:
        cert = _read_certificate(data_dir() / "identity.pem")
    except FileNotFoundError:
        return None
    return cert.fingerprint(hashes.SHA256()).hex(":")


def tls_identity(host, extra):
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = directory / "identity.pem"
    if not identity.exists():
        _generate(identity, host, extra)
    certificate = _read_certificate(identity)
    if certificate.not_valid_after_utc <= datetime.now(timezone.utc):
        raise ValueError("Certificado expirado. Consulte a seção HTTPS do README.")
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    explicit = set(extra)
    if private_ip(host):
        explicit.add(ipaddress.ip_address(host))
    if explicit.difference(names.get_values_for_type(x509.IPAddress)):
        raise ValueError("IP ausente no certificado salvo. Consulte a seção HTTPS do README.")
    public_file = directory / "cert.pem"
    public_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(identity)
    return context, certificate.fingerprint(hashes.SHA256()).hex(":"), public_file
