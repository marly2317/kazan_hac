from django.urls import path

from apps.autofill.presentation import suggest_views, views

app_name = "autofill"

urlpatterns = [
    path("analyze/", views.AnalyzeView.as_view(), name="analyze"),
    path("suggest/", suggest_views.SuggestView.as_view(), name="suggest"),
]
