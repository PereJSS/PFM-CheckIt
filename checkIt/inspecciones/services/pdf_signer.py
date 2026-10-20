# Firmar un PDF con una firma digital X.509 (PAdES, CMS) usando pyHanko
# garantiza la inmutabilidad legal del documento firmado.

import logging
import os

from asn1crypto import tsp
from pyhanko import stamp
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import signers, fields as sign_fields
from pyhanko.sign.timestamps import TimeStamper
from pyhanko_certvalidator._asyncio_compat import to_thread

from . import tsa

logger = logging.getLogger(__name__)

VALORES_SI = ('1', 'true', 'yes', 'si', 'sí')


class TimeStamperCheckIt(TimeStamper):
    """Cliente TSA de pyHanko que reutiliza el transporte de services/tsa.py
    (URL, basic auth, mTLS y timeout del perfil). pyHanko comprueba el nonce y
    el hash sellado de la respuesta."""

    def __init__(self, perfil=tsa.PERFIL_MANIFIESTO):
        super().__init__()
        self.perfil = perfil

    async def async_request_tsa_response(self, req):
        contenido = await to_thread(lambda: tsa.enviar_peticion(req.dump(), self.perfil))
        return tsp.TimeStampResp.load(contenido)


def _cargar_firmante():
    # Sello de empresa en PKCS#12 (.p12/.pfx) si se configura; si no, PEM clave + certificado
    pkcs12_path = os.getenv('PDF_SIGNING_PKCS12_PATH')
    if pkcs12_path:
        password = os.getenv('PDF_SIGNING_PKCS12_PASSWORD', '')
        signer = signers.SimpleSigner.load_pkcs12(
            pkcs12_path, passphrase=password.encode() if password else None,
        )
        if signer is None:
            raise RuntimeError(
                'No se pudo cargar el firmante PDF. Revisa PDF_SIGNING_PKCS12_PATH y PDF_SIGNING_PKCS12_PASSWORD.'
            )
        return signer

    key_path = os.getenv('PDF_SIGNING_KEY_PATH', 'certs/signing.key')
    cert_path = os.getenv('PDF_SIGNING_CERT_PATH', 'certs/signing.crt')
    signer = signers.SimpleSigner.load(key_path, cert_path)
    if signer is None:
        raise RuntimeError(
            'No se pudo cargar el firmante PDF. Revisa PDF_SIGNING_KEY_PATH y PDF_SIGNING_CERT_PATH.'
        )
    return signer


def sello_tiempo_activo():
    return os.getenv('PDF_FIRMA_SELLO_TIEMPO', 'true').strip().lower() in VALORES_SI


def _firmar(input_path, output_path, signer, timestamper):
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
                # PAdES: con sello de tiempo queda en nivel B-T; sin él, B-B
                subfilter=sign_fields.SigSeedSubFilter.PADES,
            ),
            signer=signer,
            timestamper=timestamper,
            stamp_style=stamp_style,
            new_field_spec=field_spec,
        )

        with open(output_path, 'wb') as doc_salida:
            pdf_signer_obj.sign_pdf(writer, output=doc_salida)

    return output_path


def firmar_pdf_reclamacion(input_path, output_path):
    """Toma un PDF generado y le aplica una firma digital PAdES con apariencia
    visible. Si PDF_FIRMA_SELLO_TIEMPO está activo, la firma lleva un sello de
    tiempo de la TSA del perfil manifiesto (PAdES-B-T); si la TSA no responde,
    se firma sin él para no bloquear el informe."""
    signer = _cargar_firmante()

    if sello_tiempo_activo():
        try:
            return _firmar(input_path, output_path, signer, TimeStamperCheckIt())
        except (OSError, ValueError, tsa.TSAError) as exc:
            # OSError cubre los errores de red de requests y TimestampRequestError de pyHanko
            logger.warning(
                'Firma del PDF sin sello de tiempo: la TSA no respondió (%s: %s)', type(exc).__name__, exc,
            )

    return _firmar(input_path, output_path, signer, None)
