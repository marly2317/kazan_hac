"""Эндпоинт подсказок под ``/api/v1/autofill/suggest/``."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.autofill.application.use_cases.suggest_terms import SuggestInput
from apps.autofill.presentation.suggest_serializers import (
    SuggestionSerializer,
    SuggestRequestSerializer,
)
from config import container
from core.presentation.permissions import IsAuthenticatedUser
from core.presentation.schema import error_responses


class SuggestView(APIView):
    """Продолжения для набираемого описания.

    Отдельный эндпоинт от разбора, а не поле в его ответе: подсказка нужна на каждый символ,
    а разбор — на законченную мысль. Связать их значило бы либо гонять разбор по справочнику
    на каждое нажатие, либо показывать подсказки с задержкой в треть секунды.
    """

    permission_classes = [IsAuthenticatedUser]

    @extend_schema(
        tags=["autofill"],
        summary="Подсказать продолжение по словарю КТРУ",
        operation_id="autofill_suggest",
        parameters=[SuggestRequestSerializer],
        responses={
            200: OpenApiResponse(
                response=SuggestionSerializer(many=True),
                description="Слова справочника, подходящие к набранному.",
            ),
            **error_responses(400, 403),
        },
    )
    def get(self, request: Request) -> Response:
        payload = SuggestRequestSerializer(data=request.query_params)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        found = container.suggest_terms().execute(
            SuggestInput(text=data["text"], limit=data["limit"])
        )
        return Response(
            {"suggestions": SuggestionSerializer.many_from(found)},
            status=status.HTTP_200_OK,
        )
