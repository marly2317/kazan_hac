from django.urls import path

from apps.autofill.presentation import views

app_name = "autofill"

urlpatterns = [
    path("analyze/", views.AnalyzeView.as_view(), name="analyze"),
]
