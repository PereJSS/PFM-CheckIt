# Firmar un PDF con una firma digital X.509 (PKCS#7) usando pyHanko
# garantiza la inmutabilidad legal del documento firmado.

import os

from pyhanko import stamp
from pyhanko.sign import signers, fields as sign_fields
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter


def firmar_pdf_reclamacion(input_path, output_path):
    """Toma un PDF generado y le aplica una firma digital X.509 (PKCS#7)
    con apariencia visual visible, garantizando su inmutabilidad legal."""

    # Rutas a los certificados
    key_path = os.getenv('PDF_SIGNING_KEY_PATH', 'certs/signing.key')
    cert_path = os.getenv('PDF_SIGNING_CERT_PATH', 'certs/signing.crt')

    # Cargamos el certificado y la clave privada
    signer = signers.SimpleSigner.load(key_path, cert_path)

    # Estilo de sello visual: texto con firmante y fecha visible en el PDF
    stamp_style = stamp.TextStampStyle(
        stamp_text=(
            'Firmado digitalmente por CheckIt\n'
            'Algoritmo: RSA + SHA-256\n'
            '%(ts)s'
        ),
        background=None,
    )

    with open(input_path, 'rb') as doc_abierto:
        writer = IncrementalPdfFileWriter(doc_abierto)
        last_page = writer.root['/Pages']['/Count'] - 1

        # Campo de firma en la esquina superior derecha (dentro del margen superior vacío)
        # A4: 595 × 841 pt. Margen superior = 56 pt → usamos y = 790–835
        field_spec = sign_fields.SigFieldSpec(
            sig_field_name='Firma_Pericial_CheckIt',
            on_page=last_page,
            box=(370, 792, 575, 835),
        )

        pdf_signer_obj = signers.PdfSigner(
            signature_meta=signers.PdfSignatureMetadata(
                field_name='Firma_Pericial_CheckIt',
                reason='Informe pericial de reclamación — CheckIt',
                location='España',
            ),
            signer=signer,
            stamp_style=stamp_style,
            new_field_spec=field_spec,
        )

        writer = IncrementalPdfFileWriter(doc_abierto)

        with open(output_path, 'wb') as doc_salida:
            pdf_signer_obj.sign_pdf(writer, output=doc_salida)

    return output_path
