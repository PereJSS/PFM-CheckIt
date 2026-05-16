from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InspeccionViewSet


router = DefaultRouter()
router.register(r'inspecciones', InspeccionViewSet)

urlpatterns = [
    path('', include(router.urls)),
]