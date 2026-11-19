import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone as dt_timezone

from rest_framework import viewsets
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
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
from .models import ESTANCIA_MAX, ChecklistInspeccion, Evidencia, PuntoInspeccion, motivo_descripcion_no_valida
from .services.checklist import faltas_para_completar, instanciar_checklist, plantilla_efectiva, resumen
from propiedades.models import PlantillaChecklist
from django.http import HttpResponse
from django.db import transaction
from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from typing import cast
from users.permissions import es_admin
from core.textos import motivo_texto_corto_no_valido, normalizar_texto_corto


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


def _es_operario_de(usuario, administrador):
    """Un operario solo se asigna a las inspecciones del administrador que lo dio de alta."""
    return _is_operario_user(usuario) and usuario.administrador_id == administrador.pk


# Datos de antes del 5 nov: un operario de otro cliente (o sin administrador) sigue viendo la
# inspección. El administrador no la modifica hasta reasignarla a uno propio o quitarlo.
ERROR_OPERARIO_AJENO = "El operario asignado no es uno de tus operarios: reasígnala o quítalo para editarla."


def _operario_ajeno(operario, administrador):
    return operario is not None and not _es_operario_de(operario, administrador)


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


def _estancia(valor):
    """Estancia que declara el operario (opcional). Devuelve (texto o None, error)."""
    if valor is None:
        return None, None
    if not isinstance(valor, str):
        return None, "estancia debe ser texto."
    texto = normalizar_texto_corto(valor)
    motivo = motivo_texto_corto_no_valido(texto, ESTANCIA_MAX, 'estancia')
    if motivo:
        return None, motivo
    return texto or None, None


def _descripcion(valor):
    """Descripción libre de la foto (opcional). Devuelve (texto, error); sin texto, ''."""
    if valor is None:
        return '', None
    if not isinstance(valor, str):
        return '', "descripcion debe ser texto."
    # NFC: «ñ» compuesta cuenta como un carácter, como la ve el operario
    texto = unicodedata.normalize('NFC', valor).replace('\r\n', '\n').replace('\r', '\n').strip()
    motivo = motivo_descripcion_no_valida(texto)
    if motivo:
        return '', motivo
    return texto, None


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

class _ChecklistIncompleto(Exception):
    def __init__(self, faltas):
        self.faltas = faltas


def _respuesta_faltas(faltas):
    return Response(
        {"error": "Faltan puntos del checklist por completar.", "faltas": faltas},
        status=status.HTTP_409_CONFLICT,
    )


def _nombre_usuario(usuario):
    if usuario is None:
        return None
    return usuario.get_full_name() or usuario.username


def _datos_punto(punto, evidencias=None):
    if evidencias is None:
        evidencias = [ev.pk for ev in punto.evidencias.all()]
    return {
        "id": punto.pk,
        "texto": punto.texto,
        "requiere_foto": punto.requiere_foto,
        "resultado": punto.resultado,
        "nota": punto.nota,
        "marcado_en": punto.marcado_en,
        "marcado_por_nombre": _nombre_usuario(punto.marcado_por),
        "evidencias": evidencias,
    }


def _datos_checklist(inspeccion):
    checklist = (
        ChecklistInspeccion.objects.filter(inspeccion=inspeccion)
        .prefetch_related(Prefetch(
            'estancias__puntos',
            queryset=PuntoInspeccion.objects.select_related('marcado_por').prefetch_related(
                Prefetch('evidencias', queryset=Evidencia.objects.order_by('pk').only('pk', 'punto_id')),
            ),
        ))
        .first()
    )
    if checklist is None:
        return {"tiene_checklist": False}
    return {
        "tiene_checklist": True,
        "plantilla_nombre": checklist.plantilla_nombre,
        "plantilla_version": checklist.plantilla_version,
        "estancias": [
            {"id": e.pk, "nombre": e.nombre, "puntos": [_datos_punto(p) for p in e.puntos.all()]}
            for e in checklist.estancias.all()
        ],
        "resumen": resumen(inspeccion.pk),
    }


_ID_DIGITOS = re.compile(r'^[0-9]{1,19}$')
_ID_MAX = 2 ** 63 - 1


def _id_entero(valor):
    """Un id entero positivo que cabe en un bigint, o None (True, 1.5, '1e3', 10**30…)."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, str) and _ID_DIGITOS.match(valor.strip()):
        valor = int(valor.strip())
    if not isinstance(valor, int) or not 0 < valor <= _ID_MAX:
        return None
    return valor


def _plantilla_propia(valor, usuario):
    """(plantilla, error) para el campo opcional `plantilla` de una petición del admin."""
    pk = _id_entero(valor)
    if pk is None:
        return None, "plantilla debe ser el id de una de tus plantillas."
    plantilla = PlantillaChecklist.objects.filter(pk=pk, owner=usuario).first()
    if plantilla is None:
        return None, "La plantilla no existe."
    return plantilla, None


class InspeccionViewSet(viewsets.ModelViewSet):
    queryset: QuerySet[Inspeccion] = cast(QuerySet[Inspeccion], Inspeccion.objects.all())
    serializer_class = InspeccionSerializer
    # Solo lo fija la acción que lo necesita (paquete-probatorio); sin él, ScopedRateThrottle no limita
    throttle_scope = None

    # Filtramos las inspecciones según el rol del usuario
    def get_queryset(self):  # type: ignore[override]
        usuario = self.request.user
        if _is_admin_user(usuario):
            qs = Inspeccion.objects.filter(propiedad__owner=usuario)
        else:
            qs = Inspeccion.objects.filter(operario=usuario)
        # Los permisos miran propiedad.owner: sin consultas extra por petición
        qs = qs.select_related('propiedad__owner')
        if getattr(self, 'action', None) == 'list':
            # El resumen del checklist y lo anidado, sin una consulta por fila. Solo en el
            # listado: un GROUP BY no admite el FOR UPDATE de otras acciones
            R = PuntoInspeccion.Resultado
            qs = (
                qs.select_related('propiedad', 'operario')
                .prefetch_related('evidencias')
                .annotate(
                    ck_tiene=Exists(ChecklistInspeccion.objects.filter(inspeccion=OuterRef('pk'))),
                    ck_puntos=Count('puntos_checklist'),
                    ck_ok=Count('puntos_checklist', filter=Q(puntos_checklist__resultado=R.OK)),
                    ck_danos=Count('puntos_checklist', filter=Q(puntos_checklist__resultado=R.DANO)),
                    ck_no_aplica=Count('puntos_checklist', filter=Q(puntos_checklist__resultado=R.NO_APLICA)),
                )
            )
        return qs

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

        # Uno dado de baja no puede entrar a hacerla
        if not operario or not _es_operario_de(operario, usuario) or not operario.is_active:
            return Response(
                {"error": "La inspección debe asignarse a uno de tus operarios en activo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Otra plantilla propia solo para esta inspección (opcional)
        plantilla = None
        if request.data.get('plantilla') not in (None, ''):
            plantilla, error = _plantilla_propia(request.data.get('plantilla'), usuario)
            if error:
                return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                self.perform_create(serializer)
                instanciar_checklist(serializer.instance, plantilla)
        except DjangoValidationError as e:
            # Plantilla guardada sin validar (shell, datos viejos): no se crea nada
            usada = plantilla or plantilla_efectiva(propiedad)
            return Response(
                {"error": f"La plantilla «{getattr(usada, 'nombre', '?')}» no es válida: {'; '.join(e.messages)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
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

            if 'operario' in serializer.validated_data:
                # Asignar uno ajeno (o reenviar el ajeno que ya tenía): no da acceso a nadie más
                nuevo_operario = serializer.validated_data['operario']
                if _operario_ajeno(nuevo_operario, usuario):
                    return Response(
                        {"error": "El usuario asignado debe ser uno de tus operarios."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                # Reenviar el que ya tenía (aunque esté de baja) no asigna nada nuevo
                if nuevo_operario and nuevo_operario.pk != inspeccion.operario_id and not nuevo_operario.is_active:
                    return Response(
                        {"error": "Ese operario está dado de baja: asígnala a uno en activo."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            elif _operario_ajeno(inspeccion.operario, usuario):
                return Response({"error": ERROR_OPERARIO_AJENO}, status=status.HTTP_400_BAD_REQUEST)

            try:
                self.perform_update(serializer)
            except _ChecklistIncompleto as e:
                return _respuesta_faltas(e.faltas)
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
        try:
            self.perform_update(serializer)
        except _ChecklistIncompleto as e:
            return _respuesta_faltas(e.faltas)
        return Response(serializer.data)

    def perform_update(self, serializer):
        # El UPDATE y lo que hacen sus señales (congelar el manifiesto, el outbox de
        # webhooks) se confirman juntos: si el proceso muere entre ambos, no se pierde nada
        with transaction.atomic():
            if serializer.validated_data.get('estado') == Inspeccion.Estado.COMPLETADA:
                # Mismo bloqueo que la subida de fotos y el marcado de puntos: nadie cambia
                # el checklist entre la comprobación y el congelado. El estado se relee
                # bloqueado: la instancia se leyó antes
                actual = Inspeccion.objects.select_for_update().values_list('estado', flat=True).get(
                    pk=serializer.instance.pk,
                )
                if actual != Inspeccion.Estado.COMPLETADA:
                    faltas = faltas_para_completar(serializer.instance.pk)
                    if faltas:
                        raise _ChecklistIncompleto(faltas)
            super().perform_update(serializer)

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

    @extend_schema(
        summary='Descargar el PDF firmado (lo genera en segundo plano si no está listo)',
        responses={
            (200, 'application/pdf'): OpenApiTypes.BINARY,
            202: inline_serializer('PdfEnGeneracion', {
                'task_id': drf_serializers.CharField(required=False, help_text='Solo si esta petición inició la generación'),
                'estado': drf_serializers.ChoiceField(choices=Inspeccion.PdfEstado.choices),
                'mensaje': drf_serializers.CharField(),
            }),
            403: OpenApiResponse(description='Solo el administrador propietario'),
            404: OpenApiResponse(description='La inspección no existe'),
            503: OpenApiResponse(description='No se pudo encolar la generación (broker no disponible)'),
        },
        description='Con 202, consultar `claim-report-status` hasta `pdf_listo` y volver a pedir el PDF.',
    )
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

    @extend_schema(
        summary='Estado de la generación del PDF firmado',
        responses={
            200: inline_serializer('EstadoPdf', {
                'estado': drf_serializers.ChoiceField(choices=Inspeccion.PdfEstado.choices),
                'pdf_listo': drf_serializers.BooleanField(),
            }),
            403: OpenApiResponse(description='Solo el administrador propietario'),
            404: OpenApiResponse(description='La inspección no existe'),
        },
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
        # El operario asignado sí sube fotos (igual que puede completarla); el admin, solo tras reasignarla
        if inspeccion.operario_id != usuario.pk and _operario_ajeno(inspeccion.operario, usuario):
            return Response({"error": ERROR_OPERARIO_AJENO}, status=status.HTTP_400_BAD_REQUEST)

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
        estancia, error = _estancia(request.data.get('estancia'))
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        # Punto del checklist que documenta la foto (se resuelve con la inspección bloqueada:
        # reemplazar el checklist a la vez no deja la foto apuntando a un punto borrado)
        punto_pedido = request.data.get('punto')
        quiere_punto = punto_pedido not in (None, '')
        punto, aviso = None, None
        descripcion, error = _descripcion(request.data.get('descripcion'))
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
            if quiere_punto:
                punto_pk = _id_entero(punto_pedido)
                if punto_pk is not None:
                    punto = PuntoInspeccion.objects.select_related('estancia').filter(
                        pk=punto_pk, inspeccion=inspeccion,
                    ).first()
                if punto is None:
                    # Sin 400: el móvil apartaría la foto, que es la prueba. Se guarda sin enlazar
                    aviso = "El punto indicado no es de esta inspección: la foto se guardó sin enlazar a ningún punto."
                else:
                    estancia = punto.estancia.nombre
            evidencia = Evidencia.objects.create(
                inspeccion=inspeccion,
                foto=foto,
                descripcion=descripcion,
                estancia=estancia,
                punto=punto,
                hash_sha256=hash_sha256,
                capturada_en=capturada_en,
                tsa_timestamp=tsa_data.get('timestamp') if tsa_data else None,
                tsa_token=tsa_data.get('token') if tsa_data else None,
            )

        datos = {
            "id": evidencia.pk,
            "inspeccion": inspeccion.pk,
            "descripcion": evidencia.descripcion,
            "estancia": evidencia.estancia,
            "punto": evidencia.punto_id,
            "hash_sha256": evidencia.hash_sha256,
            "tsa_applied": bool(evidencia.tsa_token),
            "tsa_timestamp": evidencia.tsa_timestamp,
            "audit_token": _build_audit_token(evidencia.pk),
            "foto": evidencia.foto.url if evidencia.foto else None,
            "fecha_captura": evidencia.fecha_captura,
            "capturada_en": evidencia.capturada_en,
        }
        if aviso:
            datos["aviso"] = aviso
            logger.warning(
                "Foto %s de la inspección %s guardada sin punto: punto pedido %r no válido (usuario %s)",
                evidencia.pk, inspeccion.pk, str(punto_pedido)[:40], usuario.pk,
            )
        return Response(datos, status=status.HTTP_201_CREATED)


    def _puede_escribir(self, inspeccion, usuario):
        """Operario asignado o administrador dueño (como en la subida de fotos)."""
        return inspeccion.operario_id == usuario.pk or (
            _is_admin_user(usuario) and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk
        )

    @extend_schema(
        tags=['checklists'],
        summary='Checklist de la inspección (GET) o aplicarle una plantilla (POST, administrador)',
        request=inline_serializer('AplicarChecklist', {
            'plantilla': drf_serializers.IntegerField(required=False, help_text='Sin ella, la de la propiedad o la por defecto'),
            'reemplazar': drf_serializers.BooleanField(required=False, help_text='Solo si no hay puntos marcados ni fotos enlazadas'),
        }),
        responses={
            200: OpenApiResponse(description='Estancias y puntos, o {"tiene_checklist": false}'),
            201: OpenApiResponse(description='Checklist aplicado'),
            400: OpenApiResponse(description='Plantilla no válida o la propiedad no tiene'),
            403: OpenApiResponse(description='Solo el administrador dueño aplica plantillas'),
            404: OpenApiResponse(description='La inspección no existe o no es tuya'),
            409: OpenApiResponse(description='Completada, ya tiene checklist o ya hay marcas'),
        },
    )
    @action(detail=True, methods=['get', 'post'], url_path='checklist')
    def checklist(self, request, pk=None):
        inspeccion = self.get_object()
        if request.method == 'GET':
            return Response(_datos_checklist(inspeccion))

        usuario = request.user
        if not (_is_admin_user(usuario) and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk):
            return Response({"error": "Solo el administrador dueño aplica plantillas."}, status=status.HTTP_403_FORBIDDEN)
        if request.data.get('plantilla') not in (None, ''):
            plantilla, error = _plantilla_propia(request.data.get('plantilla'), usuario)
            if error:
                return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        else:
            plantilla = plantilla_efectiva(inspeccion.propiedad)
            if plantilla is None:
                return Response(
                    {"error": "La propiedad no tiene plantilla de checklist y no hay una por defecto."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        reemplazar = request.data.get('reemplazar', False)
        if not isinstance(reemplazar, bool):
            return Response({"error": "reemplazar debe ser true o false."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            estado = Inspeccion.objects.select_for_update().values_list('estado', flat=True).get(pk=inspeccion.pk)
            if estado == Inspeccion.Estado.COMPLETADA:
                return Response({"error": "La inspección ya está completada."}, status=status.HTTP_409_CONFLICT)
            existente = ChecklistInspeccion.objects.filter(inspeccion=inspeccion).first()
            if existente is not None:
                if not reemplazar:
                    return Response({"error": "La inspección ya tiene checklist."}, status=status.HTTP_409_CONFLICT)
                usados = PuntoInspeccion.objects.filter(inspeccion=inspeccion).filter(
                    Q(resultado__isnull=False) | Q(evidencias__isnull=False)
                ).exists()
                if usados:
                    return Response(
                        {"error": "Ya hay puntos marcados o con fotos: no se puede reemplazar el checklist."},
                        status=status.HTTP_409_CONFLICT,
                    )
                existente.delete()
            instanciar_checklist(inspeccion, plantilla)
        return Response(_datos_checklist(inspeccion), status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=['checklists'],
        summary='Marcar un punto del checklist (OK, DANO, NO_APLICA o null para desmarcar)',
        parameters=[OpenApiParameter('punto_id', OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=inline_serializer('MarcarPunto', {
            'resultado': drf_serializers.ChoiceField(choices=PuntoInspeccion.Resultado.choices, allow_null=True, required=False),
            'nota': drf_serializers.CharField(required=False, allow_blank=True),
            'marcado_en_dispositivo': drf_serializers.DateTimeField(
                required=False, help_text='Hora del móvil: si es anterior a la marca guardada, no se aplica'),
        }),
        responses={
            200: OpenApiResponse(description='{"aplicado": bool, "punto": {...}}; aplicado=false si había una marca más reciente'),
            400: OpenApiResponse(description='Datos no válidos'),
            403: OpenApiResponse(description='Sin permisos'),
            404: OpenApiResponse(description='La inspección o el punto no existen o no son tuyos'),
            409: OpenApiResponse(description='La inspección está completada'),
        },
    )
    @action(detail=True, methods=['patch'], url_path=r'checklist/puntos/(?P<punto_id>\d+)')
    def marcar_punto(self, request, pk=None, punto_id=None):
        inspeccion = self.get_object()
        punto_id = _id_entero(punto_id)
        if punto_id is None:
            return Response({"error": "El punto no existe en esta inspección."}, status=status.HTTP_404_NOT_FOUND)
        usuario = request.user
        if not self._puede_escribir(inspeccion, usuario):
            return Response({"error": "No tienes permisos para marcar puntos de esta inspección."},
                            status=status.HTTP_403_FORBIDDEN)
        if inspeccion.operario_id != usuario.pk and _operario_ajeno(inspeccion.operario, usuario):
            return Response({"error": ERROR_OPERARIO_AJENO}, status=status.HTTP_400_BAD_REQUEST)

        cambios = {}
        if 'resultado' in request.data:
            resultado = request.data.get('resultado')
            if resultado is not None and resultado not in PuntoInspeccion.Resultado.values:
                return Response({"error": "resultado debe ser OK, DANO, NO_APLICA o null."},
                                status=status.HTTP_400_BAD_REQUEST)
            cambios['resultado'] = resultado
        if 'nota' in request.data:
            nota, error = _descripcion(request.data.get('nota'))
            if error:
                return Response({"error": error.replace('descripcion', 'nota', 1)}, status=status.HTTP_400_BAD_REQUEST)
            cambios['nota'] = nota
        if not cambios:
            return Response({"error": "Indica resultado o nota."}, status=status.HTTP_400_BAD_REQUEST)
        dispositivo, error = _capturada_en(request.data.get('marcado_en_dispositivo'))
        if error:
            return Response({"error": error.replace('capturada_en', 'marcado_en_dispositivo', 1)},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # Primero la inspección, como la subida y el completado: sin interbloqueos
            estado = Inspeccion.objects.select_for_update().values_list('estado', flat=True).get(pk=inspeccion.pk)
            if estado == Inspeccion.Estado.COMPLETADA:
                return Response({"error": "La inspección ya está completada; el checklist no se puede cambiar."},
                                status=status.HTTP_409_CONFLICT)
            punto = (
                PuntoInspeccion.objects.select_for_update()
                .filter(pk=punto_id, inspeccion=inspeccion).first()
            )
            if punto is None:
                return Response({"error": "El punto no existe en esta inspección."}, status=status.HTTP_404_NOT_FOUND)
            # Cola sin conexión: una marca más antigua que llega tarde no pisa la vigente.
            # No es un error: el móvil la da por sincronizada
            if dispositivo and punto.marcado_en_dispositivo and dispositivo < punto.marcado_en_dispositivo:
                return Response({"aplicado": False, "motivo": "marca_mas_reciente", "punto": _datos_punto(punto)})
            for campo, valor in cambios.items():
                setattr(punto, campo, valor)
            punto.marcado_en = timezone.now()
            punto.marcado_por = usuario
            # Sin hora del móvil (p. ej. el admin) se conserva la anterior: si no, una marca
            # vieja de la cola sin conexión pisaría luego la vigente
            if dispositivo:
                punto.marcado_en_dispositivo = dispositivo
            punto.save(update_fields=[*cambios, 'marcado_en', 'marcado_por', 'marcado_en_dispositivo'])
        return Response({"aplicado": True, "punto": _datos_punto(punto)})


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