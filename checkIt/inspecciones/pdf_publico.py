"""
Descarga pública del PDF firmado mediante una URL firmada y con caducidad.
La usa el webhook `pdf.listo`: el PMS no tiene JWT para claim-report.
"""
from datetime import timedelta

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.http import FileResponse, Http404
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Inspeccion
from .services.almacen import abrir_pdf

# Salt propio: un token de auditoría u otro uso no vale para descargar PDFs
PDF_URL_SALT = 'checkit.inspeccion.pdf'


def _signer():
    return TimestampSigner(salt=PDF_URL_SALT)


def caducidad():
    return timedelta(hours=max(int(getattr(settings, 'PDF_URL_CADUCIDAD_HORAS', 72)), 0))


def token_pdf(inspeccion_id):
    return _signer().sign(str(inspeccion_id))


def url_pdf_firmada(inspeccion_id):
    base = getattr(settings, 'BACKEND_PUBLIC_URL', '').rstrip('/')
    return base + reverse('informe-pdf-publico', args=[token_pdf(inspeccion_id)])


class InformePdfPublicoView(APIView):
    """GET /api/v1/informes/<token>/: el PDF firmado de la inspección del token."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auditoria'

    @extend_schema(
        summary='Descargar el PDF firmado con una URL firmada (la del webhook pdf.listo)',
        responses={
            (200, 'application/pdf'): OpenApiTypes.BINARY,
            404: OpenApiResponse(description='Token no válido o PDF no disponible'),
            410: OpenApiResponse(description='URL caducada'),
        },
    )
    def get(self, request, token):
        try:
            inspeccion_id = _signer().unsign(token, max_age=caducidad())
        except SignatureExpired:
            return Response({"error": "El enlace ha caducado."}, status=status.HTTP_410_GONE)
        except BadSignature:
            raise Http404
        inspeccion = Inspeccion.objects.filter(pk=inspeccion_id, pdf_estado=Inspeccion.PdfEstado.LISTO).first()
        pdf = abrir_pdf(inspeccion) if inspeccion is not None else None
        if pdf is None:
            raise Http404
        return FileResponse(
            pdf,
            as_attachment=True,
            filename=f"Reclamacion_CheckIt_{inspeccion.pk}.pdf",
            content_type='application/pdf',
        )
