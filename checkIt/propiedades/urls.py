from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PlantillaChecklistViewSet, PropiedadViewSet

router = DefaultRouter()
router.register(r'propiedades', PropiedadViewSet, basename='propiedad')
router.register(r'plantillas-checklist', PlantillaChecklistViewSet, basename='plantilla-checklist')

urlpatterns = [
    path('', include(router.urls)),
]