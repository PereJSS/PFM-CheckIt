import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from users.permissions import IsAdmin

from .authentication import ApiKeyAuthentication
from .models import ApiKey, EntregaWebhook, WebhookDestino, WebhookEvent
from .salientes import encolar_entrega, reabrir_entrega
from .serializers import (
    ApiKeySerializer,
    EntregaWebhookSerializer,
    PmsWebhookSerializer,
    WebhookDestinoSerializer,
    WebhookEventListSerializer,
)
from .services import marcar_error, procesar_evento, reabrir_para_reproceso

logger = logging.getLogger(__name__)


def _encolar(evento):
    from .tasks import procesar_evento_pms

    try:
        # retry=False: si Redis no responde fallamos rápido en vez de bloquear la
        # petición del PMS con los reintentos de publicación de kombu
        procesar_evento_pms.apply_async(args=[evento.pk], retry=False)
        return
    except Exception as exc:
        logger.warning("Broker Celery no disponible (%s); procesando evento %s en línea", exc, evento.pk)

    # Sin broker procesamos en línea: crear una inspección es barato y así el
    # evento no se queda atascado en RECIBIDO.
    try:
        procesar_evento(evento.pk)
    except Exception as exc:
        logger.exception("Error procesando en línea el evento %s", evento.pk)
        marcar_error(evento.pk, f"Error procesando en línea: {exc}")


class PmsWebhookView(APIView):
    """Recibe eventos de un PMS externo. Autenticación exclusiva por API Key."""
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'webhooks'

    def post(self, request):
        serializer = PmsWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        evento, creado = WebhookEvent.objects.get_or_create(
            owner=request.user,
            proveedor=datos['provider'],
            event_id=datos['event_id'],
            defaults={
                'api_key': request.auth,
                'tipo': datos['event_type'],
                'payload': {'data': datos['data']},
            },
        )

        # Un reenvío de un evento ya resuelto (PROCESADO/IGNORADO) no se repite.
        # Si quedó en RECIBIDO (worker caído) o ERROR (p. ej. external_id sin enlazar),
        # el reenvío lo vuelve a encolar; procesar_evento es idempotente.
        if not creado and not reabrir_para_reproceso(evento):
            return Response(
                {"id": evento.pk, "event_id": evento.event_id, "estado": evento.estado, "duplicado": True},
                status=status.HTTP_200_OK,
            )

        _encolar(evento)
        evento.refresh_from_db(fields=['estado'])
        return Response(
            {"id": evento.pk, "event_id": evento.event_id, "estado": evento.estado, "duplicado": not creado},
            status=status.HTTP_202_ACCEPTED,
        )


class ApiKeyListCreateView(APIView):
    """Gestión de API Keys del admin autenticado (JWT)."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        claves = ApiKey.objects.filter(owner=request.user).order_by('-creada')
        return Response(ApiKeySerializer(claves, many=True).data)

    def post(self, request):
        serializer = ApiKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        api_key, clave = ApiKey.generar(request.user, serializer.validated_data['nombre'])
        # La clave en claro solo se devuelve aquí; no se puede recuperar después
        return Response({**ApiKeySerializer(api_key).data, "clave": clave}, status=status.HTTP_201_CREATED)


class ApiKeyRevokeView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def delete(self, request, pk):
        # 404 (no 403) si la clave es de otro tenant, para no permitir enumeración
        api_key = get_object_or_404(ApiKey, pk=pk, owner=request.user)
        api_key.revocada = True
        api_key.save(update_fields=['revocada'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WebhookEventListView(APIView):
    """Últimos eventos recibidos, para que el admin depure su integración."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        eventos = WebhookEvent.objects.filter(owner=request.user).order_by('-recibido')[:50]
        return Response(WebhookEventListSerializer(eventos, many=True).data)


class WebhookDestinoListCreateView(APIView):
    """Destinos de webhooks salientes (CheckIt -> PMS) del admin autenticado."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        destinos = WebhookDestino.objects.filter(owner=request.user).order_by('-creado')
        return Response(WebhookDestinoSerializer(destinos, many=True).data)

    def post(self, request):
        serializer = WebhookDestinoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        destino = serializer.save(owner=request.user)
        # El secreto de firma solo se devuelve aquí; no se puede recuperar después
        return Response(
            {**WebhookDestinoSerializer(destino).data, "secreto": destino.secreto},
            status=status.HTTP_201_CREATED,
        )


class WebhookDestinoDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def _destino(self, request, pk):
        # 404 (no 403) si es de otro tenant, para no permitir enumeración
        return get_object_or_404(WebhookDestino, pk=pk, owner=request.user)

    def get(self, request, pk):
        return Response(WebhookDestinoSerializer(self._destino(request, pk)).data)

    def patch(self, request, pk):
        serializer = WebhookDestinoSerializer(self._destino(request, pk), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def put(self, request, pk):
        return self.patch(request, pk)

    def delete(self, request, pk):
        self._destino(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EntregaWebhookListView(APIView):
    """Últimas entregas salientes, filtrables por ?destino= y ?estado=."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        entregas = EntregaWebhook.objects.filter(destino__owner=request.user).select_related('destino')
        destino = request.query_params.get('destino')
        if destino:
            if not destino.isdigit():
                return Response({"destino": "Debe ser un id numérico."}, status=status.HTTP_400_BAD_REQUEST)
            entregas = entregas.filter(destino_id=int(destino))
        estado = request.query_params.get('estado')
        if estado:
            if estado not in EntregaWebhook.Estado.values:
                return Response({"estado": "Estado no válido."}, status=status.HTTP_400_BAD_REQUEST)
            entregas = entregas.filter(estado=estado)
        entregas = entregas.order_by('-creada', '-id')[:50]
        return Response(EntregaWebhookSerializer(entregas, many=True).data)


class EntregaWebhookReintentarView(APIView):
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        entrega = get_object_or_404(EntregaWebhook, pk=pk, destino__owner=request.user)
        if not reabrir_entrega(entrega):
            return Response(
                {"error": "Solo se pueden reintentar entregas fallidas."},
                status=status.HTTP_409_CONFLICT,
            )
        transaction.on_commit(lambda: encolar_entrega(entrega.pk))
        return Response(
            {"id": entrega.pk, "estado": EntregaWebhook.Estado.PENDIENTE},
            status=status.HTTP_202_ACCEPTED,
        )
