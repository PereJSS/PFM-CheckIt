from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PropiedadViewSet

router = DefaultRouter()
router.register(r'propiedades', PropiedadViewSet, basename='propiedad')

urlpatterns = [
    path('', include(router.urls)),
]