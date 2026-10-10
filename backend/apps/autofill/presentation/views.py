"""Autofill endpoints under ``/api/v1/autofill/``."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.autofill.application.use_cases.analyze_description import AnalyzeDescriptionInput
from apps.autofill.presentation.serializers import (
    AnalysisSerializer,
    AnalyzeRequestSerializer,
)
from config import container
from core.presentation.permissions import IsAuthenticatedUser
from core.presentation.schema import error_responses


class AnalyzeView(APIView):
    """Analyse a free-text description: unify characteristics and narrow the КТРУ code.

    A ``GET`` with the description in the query string on purpose — the call is a read, it is
    safe to repeat as the user types, and it stays cacheable and linkable.
    """

    permission_classes = [IsAuthenticatedUser]

    @extend_schema(
        tags=["autofill"],
        summary="Разобрать описание товара и подобрать КТРУ",
        operation_id="autofill_analyze",
        parameters=[AnalyzeRequestSerializer],
        responses={
            200: OpenApiResponse(
                response=AnalysisSerializer,
                description="Характеристики, найденные ошибки и ход сужения кодов КТРУ.",
            ),
            **error_responses(400, 403),
        },
    )
    def get(self, request: Request) -> Response:
        payload = AnalyzeRequestSerializer(data=request.query_params)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        analysis = container.analyze_description().execute(
            AnalyzeDescriptionInput(
                description=data["description"], correct_typos=data["correct"]
            )
        )

        # Заметка тем, кто ведёт справочник: какие слова разбор не смог объяснить найденной
        # позицией. В ответ не попадает ничего — наблюдение адресовано не пользователю, и
        # сообщать ему «возможно, в КТРУ чего-то не хватает» значило бы перекладывать на него
        # чужую задачу. Смотреть накопленное — в админке.
        if data["settled"]:
            gaps = container.record_reference_gaps()
            if gaps is not None:
                gaps.execute(analysis)

        return Response(
            AnalysisSerializer.from_analysis(analysis), status=status.HTTP_200_OK
        )
