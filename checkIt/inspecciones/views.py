
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.http import FileResponse
from .models import Inspeccion
from .serializers import InspeccionSerializer
from .services.pdf_generator import generar_informe_base
from .services.pdf_signer import firmar_pdf_reclamacion

class InspeccionViewSet(viewsets.ModelViewSet):
    queryset = Inspeccion.objects.all()
    serializer_class = InspeccionSerializer

    # Filtramos las inspecciones según el rol del usuario
    def get_queryset(self):
        usuario = self.request.user
        if usuario.es_administrador():
            return Inspeccion.objects.all()
        return Inspeccion.objects.filter(operario=usuario)

    # Creamos el endpoint: GET /api/v1/inspecciones/{id}/claim-report/
    @action(detail=True, methods=['get'], url_path='claim-report')
    def claim_report(self, request, pk=None):
        usuario = request.user
        
        # Seguridad: Solo los Property Managers (Admin) pueden descargar PDFs
        if not usuario.es_administrador():
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
            filename=f"Reclamacion_Ckeckii_{inspeccion.id}.pdf"
        )