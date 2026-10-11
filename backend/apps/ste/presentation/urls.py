from django.urls import path

from apps.ste.presentation import views

app_name = "ste"

urlpatterns = [
    path("preview/", views.PreviewView.as_view(), name="preview"),
    path("analyze/", views.AnalyzeView.as_view(), name="analyze"),
    path("answer/", views.AnswerView.as_view(), name="answer"),
    path("answer-text/", views.AnswerTextView.as_view(), name="answer-text"),
    path("choose-family/", views.ChooseFamilyView.as_view(), name="choose-family"),
    path("choose-leaf/", views.ChooseLeafView.as_view(), name="choose-leaf"),
    path("edit/", views.EditView.as_view(), name="edit"),
    path("extra/", views.ExtraView.as_view(), name="extra"),
]
