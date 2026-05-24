# Este script genera un par de clave privada y certificado público para firmar digitalmente los PDFs de las inspecciones.

import os
import datetime
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes

def generar():
    os.makedirs('certs', exist_ok=True)
    
    # 1. Generar Clave Privada
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open("certs/signing.key", "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # 2. Generar Certificado Público
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"CheckIt Dev Local CA"),
    ])
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=365)
    ).sign(key, hashes.SHA256())

    with open("certs/signing.crt", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        
    print("¡Éxito! Clave privada y certificado generados en backend/certs/")

if __name__ == "__main__":
    generar()