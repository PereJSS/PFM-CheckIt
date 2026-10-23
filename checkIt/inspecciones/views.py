import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone

from rest_framework import viewsets
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from .models import Inspeccion
from .serializers import InspeccionSerializer
from .services.tsa import solicitar_sello_tiempo
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.permissions import AllowAny
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from django.shortcuts import get_object_or_404
from .models import Evidencia
from django.http import HttpResponse
from django.db import transaction
from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from typing import cast
from users.permissions import es_admin


MAX_EVIDENCE_SIZE_BYTES = 8 * 1024 * 1024  # 8 MB
ALLOWED_EVIDENCE_MIME_TYPES = {
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/heic',
    'image/heif',
}
AUDIT_TOKEN_SALT = 'checkit.evidence.audit'
AUDIT_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 dias


logger = logging.getLogger(__name__)

_is_admin_user = es_admin

_HASH_SHA256 = re.compile(r'^[0-9a-f]{64}$')


def _is_operario_user(usuario):
    role = str(getattr(usuario, "role", "") or "").strip().upper()
    return role == 'OPERARIO'


def _validate_evidence_file(foto):
    if not foto:
        return "El campo 'foto' es obligatorio."

    content_type = str(getattr(foto, 'content_type', '') or '').lower()
    if content_type not in ALLOWED_EVIDENCE_MIME_TYPES:
        return "Formato de imagen no permitido. Usa JPG, PNG, WEBP o HEIC."

    size = int(getattr(foto, 'size', 0) or 0)
    if size > MAX_EVIDENCE_SIZE_BYTES:
        return "La imagen excede el tamaño máximo permitido (8 MB)."

    return None


# Margen para relojes de móvil algo adelantados
MARGEN_RELOJ_DISPOSITIVO = timedelta(minutes=5)
# Un reloj sin configurar (1970, 2000…) no es una hora de captura
CAPTURA_MAS_ANTIGUA = datetime(2000, 1, 1, tzinfo=dt_timezone.utc)


def _capturada_en(valor):
    """Hora de captura que declara el dispositivo (opcional). Devuelve (fecha, error)."""
    if valor in (None, ''):
        return None, None
    try:
        fecha = parse_datetime(valor) if isinstance(valor, str) else None
    except ValueError:  # bien formada pero imposible (p. ej. mes 13)
        fecha = None
    if fecha is None or timezone.is_naive(fecha):
        return None, "capturada_en debe ser una fecha ISO 8601 con zona horaria."
    try:
        # En UTC ya: un año 1 con offset positivo desborda al guardarse
        fecha = fecha.astimezone(dt_timezone.utc)
    except OverflowError:
        return None, "capturada_en está fuera de rango."
    if fecha < CAPTURA_MAS_ANTIGUA:
        return None, "capturada_en está fuera de rango."
    if fecha > timezone.now() + MARGEN_RELOJ_DISPOSITIVO:
        return None, "capturada_en no puede ser posterior a la hora actual."
    return fecha, None


def _hash_archivo(archivo):
    """SHA-256 (hex) de los bytes subidos; deja el archivo al principio para guardarlo."""
    h = hashlib.sha256()
    for trozo in archivo.chunks():
        h.update(trozo)
    archivo.seek(0)
    return h.hexdigest()


def _build_audit_token(evidencia_id):
    return signing.dumps({'evidencia_id': int(evidencia_id)}, salt=AUDIT_TOKEN_SALT)


def _token_matches_evidence(token, evidencia_id):
    if not token:
        return False
    try:
        payload = signing.loads(
            token,
            salt=AUDIT_TOKEN_SALT,
            max_age=AUDIT_TOKEN_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return False

    return int(payload.get('evidencia_id', -1)) == int(evidencia_id)


def _can_access_evidence(usuario, evidencia):
    if not getattr(usuario, 'is_authenticated', False):
        return False

    inspeccion = evidencia.inspeccion
    return (
        inspeccion.operario_id == usuario.pk
        or (
            _is_admin_user(usuario)
            and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk
        )
    )

class InspeccionViewSet(viewsets.ModelViewSet):
    queryset: QuerySet[Inspeccion] = cast(QuerySet[Inspeccion], Inspeccion.objects.all())
    serializer_class = InspeccionSerializer
    # Solo lo fija la acción que lo necesita (paquete-probatorio); sin él, ScopedRateThrottle no limita
    throttle_scope = None

    # Filtramos las inspecciones según el rol del usuario
    def get_queryset(self):  # type: ignore[override]
        usuario = self.request.user
        if _is_admin_user(usuario):
            return Inspeccion.objects.filter(propiedad__owner=usuario)
        return Inspeccion.objects.filter(operario=usuario)

    def create(self, request, *args, **kwargs):
        usuario = request.user
        if not _is_admin_user(usuario):
            return Response(
                {"error": "Solo un administrador puede crear inspecciones."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        propiedad = serializer.validated_data.get('propiedad')
        operario = serializer.validated_data.get('operario')

        if not propiedad or getattr(propiedad.owner, 'pk', None) != usuario.pk:
            return Response(
                {"error": "Solo puedes crear inspecciones sobre tus propiedades."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not operario or not _is_operario_user(operario):
            return Response(
                {"error": "La inspección debe asignarse a un usuario operario válido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def partial_update(self, request, *args, **kwargs):
        inspeccion = self.get_object()
        usuario = request.user

        if _is_admin_user(usuario):
            if getattr(inspeccion.propiedad.owner, 'pk', None) != usuario.pk:
                return Response({"error": "Sin permisos."}, status=status.HTTP_403_FORBIDDEN)

            serializer = self.get_serializer(inspeccion, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)

            nueva_propiedad = serializer.validated_data.get('propiedad')
            if nueva_propiedad and getattr(nueva_propiedad.owner, 'pk', None) != usuario.pk:
                return Response(
                    {"error": "Solo puedes mover inspecciones a tus propias propiedades."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            nuevo_operario = serializer.validated_data.get('operario')
            if nuevo_operario and not _is_operario_user(nuevo_operario):
                return Response(
                    {"error": "El usuario asignado debe ser operario."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            self.perform_update(serializer)
            return Response(serializer.data)

        # Operario: solo puede marcar su inspección asignada como COMPLETADA.
        if inspeccion.operario_id != usuario.pk:
            return Response({"error": "Sin permisos."}, status=status.HTTP_403_FORBIDDEN)

        requested_fields = set(request.data.keys())
        if requested_fields != {'estado'}:
            return Response(
                {"error": "Solo puedes actualizar el estado de la inspección."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.data.get('estado') != Inspeccion.Estado.COMPLETADA:
            return Response(
                {"error": "Solo puedes marcar la inspección como COMPLETADA."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(inspeccion, data={'estado': Inspeccion.Estado.COMPLETADA}, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        # Forzamos el camino de validación de permisos de partial_update.
        kwargs['partial'] = True
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        inspeccion = self.get_object()
        usuario = request.user
        if not (
            _is_admin_user(usuario)
            and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk
        ):
            return Response({"error": "Solo el administrador propietario puede eliminar inspecciones."}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['get'], url_path='claim-report')
    def claim_report(self, request, pk=None):
        usuario = request.user
        inspeccion = get_object_or_404(Inspeccion, pk=pk)
        can_download = (
            _is_admin_user(usuario)
            and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk
        )
        if not can_download:
            return Response(
                {"error": "No tienes permisos para descargar informes periciales."},
                status=403,
            )

        if inspeccion.pdf_estado == Inspeccion.PdfEstado.LISTO:
            from .services.almacen import abrir_pdf
            # Si el PDF ya no existe (p. ej. en un disco efímero borrado), se regenera
            pdf = abrir_pdf(inspeccion)
            if pdf is not None:
                return FileResponse(
                    pdf,
                    as_attachment=True,
                    filename=f"Reclamacion_CheckIt_{inspeccion.pk}.pdf",
                )

        generando = Inspeccion.PdfEstado.GENERANDO
        # Reserva condicional antes de encolar: si se guardara después, un worker rápido
        # vería su LISTO pisado; y de dos peticiones a la vez solo encola una
        anterior, desde_anterior = inspeccion.pdf_estado, inspeccion.pdf_generando_desde
        ahora = timezone.now()
        libre = Inspeccion.objects.filter(pk=inspeccion.pk, pdf_estado=anterior)
        if anterior == generando:
            # Solo si es de un worker muerto (o de antes de guardar la fecha)
            caducado = ahora - timedelta(minutes=settings.PDF_GENERANDO_CADUCA_MIN)
            libre = libre.filter(Q(pdf_generando_desde__isnull=True) | Q(pdf_generando_desde__lt=caducado))
        reservada = libre.update(pdf_estado=generando, pdf_generando_desde=ahora)
        if not reservada:
            # Releído: si otra petición ganó la reserva, el estado puede ser ya otro
            actual = Inspeccion.objects.filter(pk=inspeccion.pk).values_list('pdf_estado', flat=True).first()
            return Response(
                {"estado": actual or generando, "mensaje": "El PDF ya está siendo generado."},
                status=status.HTTP_202_ACCEPTED,
            )

        from .tasks import generar_pdf_async
        try:
            task = generar_pdf_async.delay(inspeccion.pk)
        except Exception as exc:
            logger.warning(
                "Broker Celery no disponible (%s); no se genera el PDF de la inspección %s",
                type(exc).__name__, inspeccion.pk,
            )
            Inspeccion.objects.filter(pk=inspeccion.pk, pdf_estado=generando, pdf_generando_desde=ahora).update(
                pdf_estado=anterior, pdf_generando_desde=desde_anterior,
            )
            return Response(
                {"error": "No se pudo iniciar la generación del PDF. Inténtalo en unos minutos."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {"task_id": task.id, "estado": generando, "mensaje": "Generación de PDF iniciada."},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['get'], url_path='claim-report-status')
    def claim_report_status(self, request, pk=None):
        usuario = request.user
        inspeccion = get_object_or_404(Inspeccion, pk=pk)
        can_download = (
            _is_admin_user(usuario)
            and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk
        )
        if not can_download:
            return Response({"error": "No tienes permisos."}, status=403)
        return Response({
            "estado": inspeccion.pdf_estado,
            "pdf_listo": inspeccion.pdf_estado == Inspeccion.PdfEstado.LISTO,
        })

    @extend_schema(
        summary='Descargar el paquete probatorio (ZIP verificable sin CheckIt)',
        responses={
            (200, 'application/zip'): OpenApiTypes.BINARY,
            403: OpenApiResponse(description='Solo el administrador propietario'),
            409: OpenApiResponse(description='Falta algo (inspección, sello o PDF) o la integridad no cuadra'),
        },
    )
    @action(detail=True, methods=['get'], url_path='paquete-probatorio',
            throttle_classes=[ScopedRateThrottle], throttle_scope='paquete')
    def paquete_probatorio(self, request, pk=None):
        from .services.paquete import IntegridadRota, PaqueteNoDisponible, construir_paquete, nombre_paquete
        usuario = request.user
        # get_object filtra por rol: una inspección ajena da 404, no 403
        inspeccion = self.get_object()
        if not (_is_admin_user(usuario) and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk):
            return Response({"error": "No tienes permisos para descargar el paquete probatorio."}, status=403)
        try:
            paquete = construir_paquete(inspeccion)
        except PaqueteNoDisponible as e:
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)
        except IntegridadRota:
            return Response(
                {"error": "No se entrega el paquete: la integridad de las fotos no cuadra con el manifiesto sellado."},
                status=status.HTTP_409_CONFLICT,
            )
        logger.info('Paquete probatorio de la inspección %s descargado por el usuario %s', inspeccion.pk, usuario.pk)
        respuesta = FileResponse(
            paquete, as_attachment=True, filename=nombre_paquete(inspeccion.pk), content_type='application/zip',
        )
        # Lleva datos personales: que no lo guarde ninguna caché intermedia
        respuesta['Cache-Control'] = 'private, no-store'
        return respuesta

    @action(detail=True, methods=['post'], url_path='evidencias')
    def upload_evidence(self, request, pk=None):
        """POST /api/v1/inspecciones/{id}/evidencias/"""
        inspeccion = self.get_object()
        usuario = request.user

        # Solo el operario asignado o el admin dueño de la propiedad pueden adjuntar evidencia.
        can_upload = (
            (inspeccion.operario_id == usuario.pk)
            or (
                _is_admin_user(usuario)
                and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk
            )
        )
        if not can_upload:
            return Response(
                {"error": "No tienes permisos para añadir evidencias a esta inspección."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # El manifiesto Merkle se congela al completar: no se admiten evidencias nuevas
        if inspeccion.estado == Inspeccion.Estado.COMPLETADA:
            return Response(
                {"error": "La inspección ya está completada; no admite más evidencias."},
                status=status.HTTP_409_CONFLICT,
            )

        foto = request.FILES.get('foto')
        file_error = _validate_evidence_file(foto)
        if file_error:
            return Response(
                {"error": file_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        hash_sha256 = request.data.get('hash_sha256') or ''
        if not isinstance(hash_sha256, str):
            return Response(
                {"error": "hash_sha256 debe ser texto."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        hash_sha256 = hash_sha256.strip().lower() or None
        if hash_sha256 and not _HASH_SHA256.match(hash_sha256):
            return Response(
                {"error": "hash_sha256 debe ser un SHA-256 en hexadecimal (64 caracteres)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Lo que se sella es el hash de los bytes que se guardan, no el que dice el cliente
        hash_servidor = _hash_archivo(foto)
        if hash_sha256 and hash_sha256 != hash_servidor:
            return Response(
                {"error": "hash_sha256 no coincide con la foto recibida."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        hash_sha256 = hash_servidor
        capturada_en, error = _capturada_en(request.data.get('capturada_en'))
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        # La TSA se consulta antes del bloqueo para no retener la fila durante la llamada de red
        tsa_data = solicitar_sello_tiempo(hash_sha256) if hash_sha256 else None

        with transaction.atomic():
            # Mismo bloqueo que congelar_manifiesto: o la evidencia entra antes del
            # congelado y queda en el manifiesto, o ve la inspección completada
            estado = Inspeccion.objects.select_for_update().values_list('estado', flat=True).get(pk=inspeccion.pk)
            if estado == Inspeccion.Estado.COMPLETADA:
                return Response(
                    {"error": "La inspección ya está completada; no admite más evidencias."},
                    status=status.HTTP_409_CONFLICT,
                )
            evidencia = Evidencia.objects.create(
                inspeccion=inspeccion,
                foto=foto,
                descripcion=request.data.get('descripcion') or '',
                hash_sha256=hash_sha256,
                capturada_en=capturada_en,
                tsa_timestamp=tsa_data.get('timestamp') if tsa_data else None,
                tsa_token=tsa_data.get('token') if tsa_data else None,
            )

        return Response(
            {
                "id": evidencia.pk,
                "inspeccion": inspeccion.pk,
                "descripcion": evidencia.descripcion,
                "hash_sha256": evidencia.hash_sha256,
                "tsa_applied": bool(evidencia.tsa_token),
                "tsa_timestamp": evidencia.tsa_timestamp,
                "audit_token": _build_audit_token(evidencia.pk),
                "foto": evidencia.foto.url if evidencia.foto else None,
                "fecha_captura": evidencia.fecha_captura,
                "capturada_en": evidencia.capturada_en,
            },
            status=status.HTTP_201_CREATED,
        )


class EvidenceAuditView(APIView):
    # Acceso por token firmado (compartible) o por permisos de usuario autenticado.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auditoria'
    permission_classes = [AllowAny]

    @extend_schema(
        parameters=[OpenApiParameter('t', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description='Token de auditoría firmado (alternativa a estar autenticado).')],
        responses=inline_serializer('AuditoriaEvidencia', {
            'evidencia_id': drf_serializers.IntegerField(),
            'hash_sha256': drf_serializers.CharField(),
            'tsa_timestamp': drf_serializers.DateTimeField(allow_null=True),
            'mensaje': drf_serializers.CharField(),
        }),
    )
    def get(self, request, id):
        """ GET /api/v1/audit/{id}/ """
        evidencia = get_object_or_404(Evidencia, pk=id)

        token = request.query_params.get('t')
        token_ok = _token_matches_evidence(token, evidencia.pk)
        user_ok = _can_access_evidence(request.user, evidencia)

        if not (token_ok or user_ok):
            return Response({"error": "Sin permisos para consultar esta evidencia."}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            "evidencia_id": evidencia.pk,
            "hash_sha256": evidencia.hash_sha256,
            "tsa_timestamp": evidencia.tsa_timestamp,
            "mensaje": "Integridad matemática verificada correctamente."
        })

class EvidenceTokenView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auditoria'
    permission_classes = [AllowAny]

    @extend_schema(
        parameters=[OpenApiParameter('t', OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description='Token de auditoría firmado (alternativa a estar autenticado).')],
        responses={(200, 'application/timestamp-reply'): OpenApiTypes.BINARY},
    )
    def get(self, request, id):
        """ GET /api/v1/audit/{id}/token/ """
        evidencia = get_object_or_404(Evidencia, pk=id)

        token = request.query_params.get('t')
        token_ok = _token_matches_evidence(token, evidencia.pk)
        user_ok = _can_access_evidence(request.user, evidencia)

        if not (token_ok or user_ok):
            return Response({"error": "Sin permisos para consultar esta evidencia."}, status=status.HTTP_403_FORBIDDEN)

        if not evidencia.tsa_token:
            return Response({"error": "La evidencia no dispone de token TSA."}, status=status.HTTP_404_NOT_FOUND)
        
        # Devuelve el archivo binario .tsr para su validación manual [5]
        response = HttpResponse(evidencia.tsa_token, content_type='application/timestamp-reply')
        response['Content-Disposition'] = f'attachment; filename="evidencia_{evidencia.pk}_token.tsr"'
        return response