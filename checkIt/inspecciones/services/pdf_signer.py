# Firmar un PDF con una firma digital X.509 (PKCS#7) usando pyHanko
# granantiza la inmutabilidad legal del documento firmado.

import os
from pyhanko.sign import signers
from pyhanko.pdf_utils.reader import PdfFileReader

def firmar_pdf_reclamacion(input_path, output_path):
    #Toma un PDF generado y le aplica una firma digital X.509 (PKCS#7)
    #garantizando su inmutabilidad legal.
    
    
    # Rutas a los certificados
    
    key_path = os.getenv('PDF_SIGNING_KEY_PATH', 'certs/signing.key')
    cert_path = os.getenv('PDF_SIGNING_CERT_PATH', 'certs/signing.crt')

    # Cargamos el certificado y la clave privada
    signer = signers.SimpleSigner.load_pem_private_key(key_path, cert_path)

    with open(input_path, 'rb') as doc_abierto:
        pdf_reader = PdfFileReader(doc_abierto)
        
        with open(output_path, 'wb') as doc_salida:
            # Sellamos el PDF con metadatos de firma
            signers.sign_pdf(
                pdf_reader, 
                signers.PdfSignatureMetadata(field_name='Firma_Pericial_Ckeckii'), 
                signer=signer, 
                out=doc_salida
            )
    return output_path