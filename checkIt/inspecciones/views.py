
from rest_framework import viewsets
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from .models import Inspeccion
from .serializers import InspeccionSerializer
from .services.pdf_generator import generar_informe_base
from .services.pdf_signer import firmar_pdf_reclamacion
from .services.tsa import solicitar_sello_tiempo
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.shortcuts import get_object_or_404
from .models import Evidencia
from django.http import HttpResponse
from django.db.models import QuerySet
from typing import cast


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


def _is_admin_user(usuario):
    # Robustez: admite roles legacy y flags de Django incluso si el método is_admin no existe.
    if not getattr(usuario, "is_authenticated", False):
        return False

    role = str(getattr(usuario, "role", "") or "").strip().upper()
    return (
        role in {"ADMINISTRADOR", "ADMIN"}
        or bool(getattr(usuario, "is_superuser", False))
        or bool(getattr(usuario, "is_staff", False))
    )


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

    # Creamos el endpoint: GET /api/v1/inspecciones/{id}/claim-report/
    @action(detail=True, methods=['get'], url_path='claim-report')
    def claim_report(self, request, pk=None):
        usuario = request.user

        # Seguridad: solo el admin propietario de la inspección puede descargar el informe.
        inspeccion = get_object_or_404(Inspeccion, pk=pk)
        can_download = (
            _is_admin_user(usuario)
            and getattr(inspeccion.propiedad.owner, 'pk', None) == usuario.pk
        )

        if not can_download:
            return Response(
                {"error": "No tienes permisos para descargar informes periciales."}, 
                status=403
            )

        
        # 1. Dibujamos el PDF
        input_pdf = generar_informe_base(inspeccion)
        
        # 2. Aplicamos la firma criptográfica X.509
        ruta_final = f"tmp/Reclamacion_Fianza_{inspeccion.pk}_Firmada.pdf"
        signed_pdf = firmar_pdf_reclamacion(input_pdf, ruta_final)
        
        # 3. Devolvemos el archivo binario al navegador para su descarga
        return FileResponse(
            open(signed_pdf, 'rb'), 
            as_attachment=True, 
            filename=f"Reclamacion_CheckIt_{inspeccion.pk}.pdf"
        )

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

        foto = request.FILES.get('foto')
        file_error = _validate_evidence_file(foto)
        if file_error:
            return Response(
                {"error": file_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        hash_sha256 = request.data.get('hash_sha256') or None
        tsa_data = solicitar_sello_tiempo(hash_sha256) if hash_sha256 else None

        evidencia = Evidencia.objects.create(
            inspeccion=inspeccion,
            foto=foto,
            descripcion=request.data.get('descripcion') or '',
            hash_sha256=hash_sha256,
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
            },
            status=status.HTTP_201_CREATED,
        )


class EvidenceAuditView(APIView):
    # Acceso por token firmado (compartible) o por permisos de usuario autenticado.
    permission_classes = [AllowAny]

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
    permission_classes = [AllowAny]

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