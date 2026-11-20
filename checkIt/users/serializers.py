from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
import hashlib
import logging

from django.db import transaction
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import datetime_from_epoch
from .models import User

logger = logging.getLogger(__name__)


def _huella(token):
    """Lo que se guarda del refresh en OutstandingToken.token: la lista negra solo usa el jti y
    un volcado de la BD no debe dar refresh tokens válidos (la librería guarda el JWT entero)."""
    return 'sha256:' + hashlib.sha256(str(token).encode()).hexdigest()


def _registrar(token, user):
    """Apunta el refresh en OutstandingToken (la purga diaria lo borra al caducar)."""
    return OutstandingToken.objects.get_or_create(
        jti=token[jwt_settings.JTI_CLAIM],
        defaults={'user': user, 'token': _huella(token), 'expires_at': datetime_from_epoch(token['exp'])},
    )[0]


# Login: RefreshToken.for_user apunta el refresh con el JWT entero; se sustituye por su huella
@extend_schema_serializer(component_name='TokenObtainPair')
class LoginSerializer(TokenObtainPairSerializer):
    # La extensión de drf-spectacular para SimpleJWT solo documenta la clase original
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        OutstandingToken.objects.filter(jti=token[jwt_settings.JTI_CLAIM]).update(token=_huella(token))
        return token


# Refresco con rotación. SimpleJWT 5.3 refresca sin mirar al usuario (un operario dado de baja
# seguiría obteniendo tokens) y, con dos refrescos simultáneos del mismo token, ambos pasan
# la lista negra antes de que ninguno lo anote y ambos reciben un refresh válido. Aquí el
# alta en la lista negra decide: solo quien la crea recibe tokens nuevos.
# Mismo nombre en OpenAPI que el original
@extend_schema_serializer(component_name='TokenRefresh')
class RefrescoUsuarioActivoSerializer(TokenRefreshSerializer):
    refresh = serializers.CharField(
        help_text='Refresh token. En la respuesta, el refresh nuevo: el enviado queda anulado.',
    )

    def validate(self, attrs):
        token = RefreshToken(attrs['refresh'])  # firma, caducidad y lista negra
        user_id = token.get(jwt_settings.USER_ID_CLAIM)
        user = User.objects.filter(**{jwt_settings.USER_ID_FIELD: user_id, 'is_active': True}).first()
        if user is None:
            raise InvalidToken('El usuario no existe o está dado de baja.')

        jti_usado = token[jwt_settings.JTI_CLAIM]
        # Todo o nada: si algo falla tras anular el refresh usado, la anulación se deshace y el
        # cliente puede reintentar con el mismo (un 500 no le cierra la sesión)
        with transaction.atomic():
            _, anotado = BlacklistedToken.objects.get_or_create(token=_registrar(token, user))
            if not anotado:
                # Reutilización: otra pestaña o petición ganó la carrera, una respuesta perdida
                # tras rotar o un refresh robado. Sin el token: solo su jti
                logger.warning('Refresh token reutilizado: user_id=%s jti=%s', user_id, jti_usado)
                raise InvalidToken('El refresh token ya se ha usado.')
            acceso = str(token.access_token)
            token.set_jti()
            token.set_exp()
            token.set_iat()
            _registrar(token, user)
        return {'access': acceso, 'refresh': str(token)}


class AdminRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('username', 'email', 'first_name', 'last_name', 'password', 'password2')

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({'password2': 'Las contraseñas no coinciden.'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(
            **validated_data,
            role=User.Role.ADMIN,
        )
        return user


class OperarioCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ('username', 'email', 'first_name', 'last_name', 'password')

    def create(self, validated_data):
        user = User.objects.create_user(
            **validated_data,
            role=User.Role.OPERARIO,
        )
        return user
