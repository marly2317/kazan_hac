"""Catalogue endpoints under ``/api/v1/catalog/``."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.application.dto import SearchProductsInput
from apps.catalog.presentation.serializers import (
    SearchQuerySerializer,
    SearchResponseSerializer,
)
from config import container
from core.presentation.permissions import IsAuthenticatedUser
from core.presentation.schema import error_responses


class ProductSearchView(APIView):
    """Search products, correcting the query first.

    The response always reports what the query *became*, so the SPA can put the corrected text
    in the search box and show the "corrected from …" notice underneath without a second call.
    """

    permission_classes = [IsAuthenticatedUser]

    @extend_schema(
        tags=["catalog"],
        summary="Поиск товаров",
        operation_id="catalog_search",
        parameters=[SearchQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=SearchResponseSerializer,
                description="Найденные товары и сведения об исправлении запроса.",
            ),
            **error_responses(400, 403),
        },
    )
    def get(self, request: Request) -> Response:
        params = SearchQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        data = params.validated_data

        result = container.search_products().execute(
            SearchProductsInput(
                query=data["q"],
                limit=data["limit"],
                offset=data["offset"],
                correct_typos=data["correct"],
            )
        )
        return Response(
            SearchResponseSerializer.from_result(result), status=status.HTTP_200_OK
        )
