from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .pdf_publico import InformePdfPublicoView
from .views import InspeccionViewSet, EvidenceAuditView, EvidenceTokenView


router = DefaultRouter()
router.register(r'inspecciones', InspeccionViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('audit/<int:id>/', EvidenceAuditView.as_view(), name='evidence-audit'),
    path('audit/<int:id>/token/', EvidenceTokenView.as_view(), name='evidence-token'),
    path('informes/<str:token>/', InformePdfPublicoView.as_view(), name='informe-pdf-publico'),
]