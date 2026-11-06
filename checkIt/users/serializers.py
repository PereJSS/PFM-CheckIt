from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User


# SimpleJWT 5.3 refresca sin mirar al usuario: un operario dado de baja (o borrado) seguiría
# obteniendo tokens de acceso durante los 5 días del refresh. Mismo nombre en OpenAPI que el original
@extend_schema_serializer(component_name='TokenRefresh')
class RefrescoUsuarioActivoSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = RefreshToken(attrs['refresh'])
        user_id = token.get(jwt_settings.USER_ID_CLAIM)
        if not User.objects.filter(**{jwt_settings.USER_ID_FIELD: user_id, 'is_active': True}).exists():
            raise InvalidToken('El usuario no existe o está dado de baja.')
        return super().validate(attrs)


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
