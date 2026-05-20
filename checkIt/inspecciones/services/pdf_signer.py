# Firmar un PDF con una firma digital X.509 (PKCS#7) usando pyHanko
# granantiza la inmutabilidad legal del documento firmado.

import os
from pyhanko.sign import signers
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

def firmar_pdf_reclamacion(input_path, output_path):
    #Toma un PDF generado y le aplica una firma digital X.509 (PKCS#7)
    #garantizando su inmutabilidad legal.

    # Rutas a los certificados
    key_path = os.getenv('PDF_SIGNING_KEY_PATH', 'certs/signing.key')
    cert_path = os.getenv('PDF_SIGNING_CERT_PATH', 'certs/signing.crt')

    # Cargamos el certificado y la clave privada
    signer = signers.SimpleSigner.load(key_path, cert_path)

    with open(input_path, 'rb') as doc_abierto:
        writer = IncrementalPdfFileWriter(doc_abierto)

        with open(output_path, 'wb') as doc_salida:
            signers.sign_pdf(
                writer,
                signers.PdfSignatureMetadata(field_name='Firma_Pericial_Ckeckii'),
                signer=signer,
                output=doc_salida,
            )

    return output_path
