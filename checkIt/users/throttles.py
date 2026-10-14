from rest_framework.throttling import SimpleRateThrottle


class LoginUsuarioThrottle(SimpleRateThrottle):
    """
    Límite de intentos de login por username, independiente de la IP: frena la
    fuerza bruta contra una cuenta aunque el atacante rote de IP.
    """
    scope = 'auth_usuario'

    def get_cache_key(self, request, view):
        username = request.data.get('username') if hasattr(request.data, 'get') else None
        if not isinstance(username, str) or not username.strip():
            return None
        ident = username.strip().lower()
        return self.cache_format % {'scope': self.scope, 'ident': ident}
