import os
import requests
import rfc3161ng
from datetime import datetime

def solicitar_sello_tiempo(evidencia_hash):
    
    #Se comunica con FreeTSA usando el estándar RFC 3161 para obtener 
    #un token inmutable de tiempo basado en el hash de la evidencia.
    
    tsa_url = os.environ.get('TSA_URL', 'https://freetsa.org/tsr')
    tsa_timeout = int(os.environ.get('TSA_TIMEOUT', 15))

    try:
        # 1. Creamos la petición criptográfica según el estándar RFC 3161
        hash_bytes = evidencia_hash.encode('utf-8')
        req = rfc3161ng.make_timestamp_request(hash_bytes)

        # 2. Enviamos la petición a FreeTSA
        headers = {'Content-Type': 'application/timestamp-query'}
        response = requests.post(tsa_url, data=req, headers=headers, timeout=tsa_timeout)

        if response.status_code == 200:
            # 3. Extraemos el token y la fecha certificada
            tsa_token = response.content
            timestamp = rfc3161ng.get_timestamp(tsa_token)
            
            return {
                "token": tsa_token,
                "timestamp": timestamp
            }
    except Exception as e:
        print(f"Error al contactar con la TSA: {e}")
        return None