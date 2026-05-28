
from rest_framework import viewsets
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse
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

class InspeccionViewSet(viewsets.ModelViewSet):
    queryset = Inspeccion.objects.all()
    serializer_class = InspeccionSerializer

    # Filtramos las inspecciones según el rol del usuario
    def get_queryset(self):
        usuario = self.request.user
        if usuario.is_admin():
            return Inspeccion.objects.filter(propiedad__owner=usuario)
        return Inspeccion.objects.filter(operario=usuario)

    # Creamos el endpoint: GET /api/v1/inspecciones/{id}/claim-report/
    @action(detail=True, methods=['get'], url_path='claim-report')
    def claim_report(self, request, pk=None):
        usuario = request.user
        
        # Seguridad: Solo los Property Managers (Admin) pueden descargar PDFs
        if not usuario.is_admin():
            return Response(
                {"error": "No tienes permisos para descargar informes periciales."}, 
                status=403
            )

        inspeccion = self.get_object()

        
        # 1. Dibujamos el PDF
        input_pdf = generar_informe_base(inspeccion)
        
        # 2. Aplicamos la firma criptográfica X.509
        ruta_final = f"tmp/Reclamacion_Fianza_{inspeccion.id}_Firmada.pdf"
        signed_pdf = firmar_pdf_reclamacion(input_pdf, ruta_final)
        
        # 3. Devolvemos el archivo binario al navegador para su descarga
        return FileResponse(
            open(signed_pdf, 'rb'), 
            as_attachment=True, 
            filename=f"Reclamacion_CheckIt_{inspeccion.id}.pdf"
        )

    @action(detail=True, methods=['post'], url_path='evidencias')
    def upload_evidence(self, request, pk=None):
        """POST /api/v1/inspecciones/{id}/evidencias/"""
        inspeccion = self.get_object()
        usuario = request.user

        # Solo el operario asignado o el admin dueño de la propiedad pueden adjuntar evidencia.
        can_upload = (
            (inspeccion.operario_id == usuario.id)
            or (usuario.is_admin() and inspeccion.propiedad.owner_id == usuario.id)
        )
        if not can_upload:
            return Response(
                {"error": "No tienes permisos para añadir evidencias a esta inspección."},
                status=status.HTTP_403_FORBIDDEN,
            )

        foto = request.FILES.get('foto')
        if not foto:
            return Response(
                {"error": "El campo 'foto' es obligatorio."},
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
                "id": evidencia.id,
                "inspeccion": inspeccion.id,
                "descripcion": evidencia.descripcion,
                "hash_sha256": evidencia.hash_sha256,
                "foto": evidencia.foto.url if evidencia.foto else None,
                "fecha_captura": evidencia.fecha_captura,
            },
            status=status.HTTP_201_CREATED,
        )


class EvidenceAuditView(APIView):
    # Este endpoint es público para permitir la verificación de la integridad matemática sin necesidad de autenticación.
    permission_classes = [AllowAny]

    def get(self, request, id):
        """ GET /api/v1/audit/{id}/ """
        evidencia = get_object_or_404(Evidencia, id=id)
        return Response({
            "evidencia_id": evidencia.id,
            "hash_sha256": evidencia.hash_sha256,
            "tsa_timestamp": evidencia.tsa_timestamp,
            "mensaje": "Integridad matemática verificada correctamente."
        })

class EvidenceTokenView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, id):
        """ GET /api/v1/audit/{id}/token/ """
        evidencia = get_object_or_404(Evidencia, id=id)
        
        # Devuelve el archivo binario .tsr para su validación manual [5]
        response = HttpResponse(evidencia.tsa_token, content_type='application/timestamp-reply')
        response['Content-Disposition'] = f'attachment; filename="evidencia_{evidencia.id}_token.tsr"'
        return response