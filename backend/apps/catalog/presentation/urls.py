from django.urls import path

from apps.catalog.presentation import views

app_name = "catalog"

urlpatterns = [
    path("products/", views.ProductSearchView.as_view(), name="product-search"),
]
