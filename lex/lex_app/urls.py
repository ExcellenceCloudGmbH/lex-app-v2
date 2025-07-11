"""lex_app URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.0/topics/http/urls/
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
import os

from django.urls import path, re_path, include

from react.views import serve_react
from . import settings, views
from .ProcessAdminSettings import processAdminSite, adminSite
from lex.lex_app.rest_api.views.authentication.UserAPIView import UserAPIView

url_prefix = (
    os.getenv("DJANGO_BASE_PATH") if os.getenv("DJANGO_BASE_PATH") is not None else ""
)

urlpatterns = [
    path("health", views.HealthCheck.as_view(), name="health_view"),
    path("api/user/", UserAPIView.as_view(), name="api-user"),
    path(url_prefix + "admin/", adminSite.urls),
    path(url_prefix, processAdminSite.urls),
    path("oidc/", include("mozilla_django_oidc.urls")),
    re_path(
        r"^(?P<path>.*)$", serve_react, {"document_root": settings.REACT_APP_BUILD_PATH}
    ),
]
