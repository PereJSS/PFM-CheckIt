from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
import hashlib
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import datetime_from_epoch
from . import invitaciones
from .models import Invitacion, RotacionRefresh, User

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


class _RefreshSinListaNegra(RefreshToken):
    """Verifica firma, caducidad y tipo, pero no la lista negra: solo para saber de quién es un
    refresh ya anulado. Nunca para emitir tokens."""

    def check_blacklist(self):
        return None


def _reutilizacion_de_crudo(crudo):
    """Un refresh rechazado: si tiene firma válida y está anulado, es una reutilización. Con firma,
    caducidad o tipo no válidos es un rechazo normal: un token falsificado no echa a nadie."""
    try:
        token = _RefreshSinListaNegra(crudo)
    except TokenError:
        return
    _tratar_reutilizacion(token.get(jwt_settings.JTI_CLAIM), token.get(jwt_settings.USER_ID_CLAIM))


def _tratar_reutilizacion(jti, user_id):
    """Un refresh ya anulado ha vuelto. Si su sucesor también se ha rotado, dos clientes tienen la
    misma cadena (posible robo) y se anulan todos los refresh vigentes del usuario. Si el sucesor
    no se llegó a usar (respuesta perdida con mala red, carrera entre pestañas) o el refresh se
    cerró con logout, solo se rechaza. Nunca lanza: la respuesta debe seguir siendo un 401."""
    if jti is None or user_id is None:
        return
    try:
        if not RotacionRefresh.objects.filter(padre__jti=jti, hijo__rotacion__isnull=False).exists():
            # Sin el token: solo su jti
            logger.warning('Refresh token reutilizado sin que su cadena siguiera: user_id=%s jti=%s', user_id, jti)
            return
        anulados = _anular_todos(user_id)
    except Exception:
        logger.exception('No se pudo tratar la reutilización de un refresh: user_id=%s jti=%s', user_id, jti)
        return
    logger.error(
        'Refresh token reutilizado con su cadena ya avanzada, posible robo: user_id=%s jti=%s; '
        'anulados %s refresh vigentes del usuario', user_id, jti, anulados,
    )


def _anular_todos(user_id):
    """Anula los refresh vigentes del usuario. Fuera de transacción a propósito: se guarda aunque
    la respuesta sea 401. Repite el barrido porque una rotación en curso puede crear un refresh
    entre la lectura y el alta. Devuelve cuántos ha anulado."""
    total = 0
    for _ in range(3):
        vigentes = list(OutstandingToken.objects.filter(
            **{f'user__{jwt_settings.USER_ID_FIELD}': user_id},
            expires_at__gt=timezone.now(), blacklistedtoken__isnull=True,
        ))
        if not vigentes:
            break
        BlacklistedToken.objects.bulk_create([BlacklistedToken(token=t) for t in vigentes], ignore_conflicts=True)
        total += len(vigentes)
    return total


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
        help_text=(
            'Refresh token. En la respuesta, el refresh nuevo: el enviado queda anulado. Si vuelve un '
            'refresh ya rotado cuyo sucesor también se rotó, se anulan todos los refresh del usuario.'
        ),
    )

    def validate(self, attrs):
        try:
            token = RefreshToken(attrs['refresh'])  # firma, caducidad y lista negra
        except TokenError:
            _reutilizacion_de_crudo(attrs['refresh'])
            raise
        user_id = token.get(jwt_settings.USER_ID_CLAIM)
        user = User.objects.filter(**{jwt_settings.USER_ID_FIELD: user_id, 'is_active': True}).first()
        if user is None:
            raise InvalidToken('El usuario no existe o está dado de baja.')

        jti_usado = token[jwt_settings.JTI_CLAIM]
        # Todo o nada: si algo falla tras anular el refresh usado, la anulación se deshace y el
        # cliente puede reintentar con el mismo (un 500 no le cierra la sesión)
        acceso = None
        with transaction.atomic():
            padre = _registrar(token, user)
            _, anotado = BlacklistedToken.objects.get_or_create(token=padre)
            if anotado:
                acceso = str(token.access_token)
                token.set_jti()
                token.set_exp()
                token.set_iat()
                RotacionRefresh.objects.create(padre=padre, hijo=_registrar(token, user))
        if not anotado:
            # Otra petición lo anuló tras nuestra comprobación de la lista negra (carrera). Se trata
            # fuera del atomic: dentro, el raise desharía la anulación
            _tratar_reutilizacion(jti_usado, user_id)
            raise InvalidToken('El refresh token ya se ha usado.')
        return {'access': acceso, 'refresh': str(token)}


# El mismo texto para una invitación falsa, caducada o ya usada: no revela cuál de las tres es
INVITACION_NO_VALIDA = 'Invitación no válida o caducada.'


class _CampoInvitacion(serializers.Serializer):
    invitacion = serializers.CharField(write_only=True, max_length=100)
    # La invitación vigente, tras validar
    invitacion_vigente: Invitacion | None = None

    def to_internal_value(self, data):
        # La invitación va antes que el resto de campos: sin una vigente, la respuesta es siempre
        # la misma (si no, el 400 del alta diría qué usernames existen)
        token = data.get('invitacion') if hasattr(data, 'get') else None
        invitacion = invitaciones.vigente(token)
        if invitacion is None:
            logger.warning('Invitación no válida, caducada o ya usada presentada')
            raise serializers.ValidationError({'invitacion': [INVITACION_NO_VALIDA]})
        self.invitacion_vigente = invitacion
        return super().to_internal_value(data)


class ValidarInvitacionSerializer(_CampoInvitacion):
    """Comprueba una invitación antes de enseñar el formulario de alta. No la gasta."""


class AdminRegisterSerializer(_CampoInvitacion, serializers.ModelSerializer):
    """Alta de un administrador (cliente) con una invitación de un solo uso."""
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('invitacion', 'username', 'email', 'first_name', 'last_name', 'password', 'password2')

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({'password2': 'Las contraseñas no coinciden.'})
        nominativa = self.invitacion_vigente.email
        if nominativa and (attrs.get('email') or '').strip().lower() != nominativa:
            raise serializers.ValidationError({'email': 'Usa el email al que se envió la invitación.'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('invitacion')
        validated_data.pop('password2')
        pk = self.invitacion_vigente.pk
        try:
            with transaction.atomic():
                # Gasto condicional: de dos altas a la vez con la misma invitación, solo una
                # actualiza la fila. Si crear el usuario falla, la transacción la devuelve a libre
                gastada = invitaciones.libres().filter(pk=pk).update(usada_en=timezone.now())
                if not gastada:
                    logger.warning('Invitación %s gastada o caducada mientras se rellenaba el alta', pk)
                    raise serializers.ValidationError({'invitacion': [INVITACION_NO_VALIDA]})
                user = User.objects.create_user(**validated_data, role=User.Role.ADMIN)
                Invitacion.objects.filter(pk=pk).update(usada_por=user)
        except IntegrityError:
            # Dos altas a la vez con el mismo username: la segunda no ve el de la primera al
            # validar. User.email no es único, así que solo puede ser el username
            logger.info('Alta con invitación %s: username ocupado por un alta simultánea', pk)
            raise serializers.ValidationError(
                {'username': ['Ya existe un usuario con ese nombre.']},
            ) from None
        logger.info('Invitación %s usada: alta del administrador user_id=%s', pk, user.pk)
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
