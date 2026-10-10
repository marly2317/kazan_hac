"""URL root.

Modules are mounted under ``/api/v1/`` by their presentation ``urls`` module — nothing here
imports a view directly, so adding a module is one line.
"""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

API_V1 = "api/v1/"

urlpatterns = [
    path("admin/", admin.site.urls),
    # Liveness for the Compose healthcheck: no database, so it answers while migrations run.
    path("healthz", lambda _request: HttpResponse("ok", content_type="text/plain")),
    path(f"{API_V1}auth/", include("apps.users.presentation.urls")),
    path(f"{API_V1}catalog/", include("apps.catalog.presentation.urls")),
    path(f"{API_V1}ste/", include("apps.ste.presentation.urls")),
    path(f"{API_V1}schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        f"{API_V1}docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
