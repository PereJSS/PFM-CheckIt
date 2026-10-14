import math
import requests
import rfc3161ng
import logging
from django.conf import settings
from pyasn1.codec.der import encoder as der_encoder

logger = logging.getLogger(__name__)

TSA_TIMEOUT_DEFAULT = 3.0


def _tsa_timeout():
    # Timeout estricto (por conexión y por lectura): si FreeTSA tarda, seguimos
    # en modo degradado (tsa_token=None). Valores no válidos vuelven al default.
    raw = getattr(settings, 'TSA_TIMEOUT', TSA_TIMEOUT_DEFAULT)
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = None
    if timeout is None or not math.isfinite(timeout) or timeout <= 0:
        logger.warning(
            "TSA_TIMEOUT no válido (%r); se usa %ss", raw, TSA_TIMEOUT_DEFAULT
        )
        return TSA_TIMEOUT_DEFAULT
    return timeout

def solicitar_sello_tiempo(evidencia_hash):
    
    #Se comunica con FreeTSA usando el estándar RFC 3161 para obtener 
    #un token inmutable de tiempo basado en el hash de la evidencia.
    
    tsa_url = getattr(settings, 'TSA_URL', 'https://freetsa.org/tsr')
    tsa_timeout = _tsa_timeout()

    try:
        # 1. Construimos la petición RFC3161 con SHA-256.
        # Si llega un hash hexadecimal válido (64 chars), lo usamos como digest real.
        # Si no, hacemos fallback a data bytes para no romper flujo de negocio.
        digest = None
        data = None
        if evidencia_hash:
            hash_txt = str(evidencia_hash).strip()
            if len(hash_txt) == 64:
                try:
                    digest = bytes.fromhex(hash_txt)
                except ValueError:
                    data = hash_txt.encode('utf-8')
            else:
                data = hash_txt.encode('utf-8')

        req = rfc3161ng.make_timestamp_request(
            data=data,
            digest=digest,
            hashname='sha256',
        )

        req_payload = der_encoder.encode(req)

        # 2. Enviamos la petición a FreeTSA
        headers = {'Content-Type': 'application/timestamp-query'}
        response = requests.post(
            tsa_url,
            data=req_payload,
            headers=headers,
            timeout=tsa_timeout,
        )

        if response.status_code != 200:
            logger.warning(
                "TSA estado no exitoso url=%s status=%s body=%s",
                tsa_url,
                response.status_code,
                response.text[:200],
            )
            return None

        # 3. Extraemos el token y la fecha certificada
        tsr = rfc3161ng.decode_timestamp_response(response.content)
        tsa_token = der_encoder.encode(tsr.time_stamp_token)
        timestamp = None
        try:
            timestamp = rfc3161ng.get_timestamp(tsa_token)
        except Exception as parse_err:
            logger.warning("TSA token sin timestamp parseable: %s", parse_err)

        return {
            "token": tsa_token,
            "timestamp": timestamp
        }
    except requests.exceptions.Timeout:
        logger.warning(
            "TSA sin respuesta en %ss url=%s; evidencia sin sello (modo degradado)",
            tsa_timeout,
            tsa_url,
        )
        return None
    except Exception as e:
        logger.exception("Error al contactar con la TSA: %s", e)
        return None