import hmac
from datetime import timedelta

from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from users.permissions import es_admin

from .models import API_KEY_PREFIX, ApiKey, hash_api_key

KEYWORD = 'Api-Key'
# ultimo_uso es orientativo: no merece una escritura en cada webhook
ULTIMO_USO_RESOLUCION = timedelta(minutes=1)


class ApiKeyAuthentication(BaseAuthentication):
    """
    Autenticación B2B: `Authorization: Api-Key ck_<prefijo>_<secreto>`.
    Devuelve (admin propietario, ApiKey). Solo se usa en los endpoints de webhooks,
    nunca en DEFAULT_AUTHENTICATION_CLASSES, para que una clave filtrada no dé
    acceso al resto de la API del tenant.
    """

    def authenticate(self, request):
        partes = get_authorization_header(request).split()
        if not partes or partes[0].lower() != KEYWORD.lower().encode():
            return None
        if len(partes) != 2:
            raise exceptions.AuthenticationFailed('Cabecera Api-Key mal formada.')

        try:
            clave = partes[1].decode('utf-8')
        except UnicodeError:
            raise exceptions.AuthenticationFailed('API Key no válida.')

        trozos = clave.split('_', 2)
        if len(trozos) != 3 or trozos[0] != API_KEY_PREFIX:
            raise exceptions.AuthenticationFailed('API Key no válida.')

        api_key = (
            ApiKey.objects.select_related('owner')
            .filter(prefijo=trozos[1], revocada=False)
            .first()
        )
        # Comparación en tiempo constante para no filtrar información por timing
        if api_key is None or not hmac.compare_digest(api_key.hash_sha256, hash_api_key(clave)):
            raise exceptions.AuthenticationFailed('API Key no válida.')

        owner = api_key.owner
        if not owner.is_active or not es_admin(owner):
            raise exceptions.AuthenticationFailed('API Key no válida.')

        ahora = timezone.now()
        if api_key.ultimo_uso is None or ahora - api_key.ultimo_uso >= ULTIMO_USO_RESOLUCION:
            ApiKey.objects.filter(pk=api_key.pk).update(ultimo_uso=ahora)
        return owner, api_key

    def authenticate_header(self, request):
        # Hace que DRF responda 401 (no 403) cuando falta la clave
        return KEYWORD
