"""
URL configuration for checkit project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
import integraciones.schema  # noqa: F401  (registra la extensión y anotaciones OpenAPI)
import core.schema  # noqa: F401
from users.views import ThrottledTokenObtainPairView, MeView, OperariosView, OperarioDetailView, RegisterAdminView

urlpatterns = [
    
    path('admin/', admin.site.urls),
    
    # Registro público de administrador
    path('api/v1/auth/register/', RegisterAdminView.as_view(), name='register'),

    # Rutas de Autenticación (Login y Refresco de Tokens)
    path('api/v1/auth/login/', ThrottledTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/auth/me/', MeView.as_view(), name='me'),

    # Gestión de operarios (solo admins)
    path('api/v1/usuarios/operarios/', OperariosView.as_view(), name='operarios'),
    path('api/v1/usuarios/operarios/<int:pk>/', OperarioDetailView.as_view(), name='operario-detail'),
    
    # Documentación OpenAPI pública (la consumen los integradores de PMS)
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/v1/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # apps
    path('api/v1/', include('propiedades.urls')),
    path('api/v1/', include('inspecciones.urls')),
    path('api/v1/', include('integraciones.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)