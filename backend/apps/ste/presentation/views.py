"""Эндпоинты подбора КТРУ под ``/api/v1/ste/`` (архитектура §6.4)."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ste.application.pipeline import PipelineError
from apps.ste.presentation import serializers as s
from config import container
from core.presentation.permissions import IsAuthenticatedUser
from core.presentation.schema import error_responses

_RESULT = OpenApiResponse(response=s.ResultSerializer, description="Состояние подбора КТРУ.")


def _error(error: PipelineError) -> Response:
    return Response(
        {"error": {"code": "ste_bad_request", "message": str(error), "details": {}}},
        status=status.HTTP_400_BAD_REQUEST,
    )


class _SteView(APIView):
    permission_classes = [IsAuthenticatedUser]
    request_serializer: type[s.serializers.Serializer]

    def _payload(self, request: Request) -> dict:
        payload = self.request_serializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return payload.validated_data


class PreviewView(APIView):
    """Похожие семейства по мере набора: поиск без LLM, десятки миллисекунд."""

    permission_classes = [IsAuthenticatedUser]

    @extend_schema(
        tags=["ste"],
        summary="Предпросмотр: похожие семейства КТРУ",
        parameters=[s.PreviewRequestSerializer],
        responses={200: OpenApiResponse(description="Топ-5 семейств"), **error_responses(400, 403)},
    )
    def get(self, request: Request) -> Response:
        payload = s.PreviewRequestSerializer(data=request.query_params)
        payload.is_valid(raise_exception=True)
        return Response(container.ste_pipeline().preview(payload.validated_data["q"]))


class AnalyzeView(_SteView):
    request_serializer = s.AnalyzeRequestSerializer

    @extend_schema(
        tags=["ste"],
        summary="Разобрать описание товара и подобрать код КТРУ",
        request=s.AnalyzeRequestSerializer,
        responses={200: _RESULT, **error_responses(400, 403)},
    )
    def post(self, request: Request) -> Response:
        data = self._payload(request)
        return Response(
            container.ste_pipeline().analyze(data["text"], data.get("session_id") or None)
        )


class AnswerView(_SteView):
    request_serializer = s.AnswerRequestSerializer

    @extend_schema(
        tags=["ste"],
        summary="Ответ на уточняющий вопрос кнопкой",
        request=s.AnswerRequestSerializer,
        responses={200: _RESULT, **error_responses(400, 403)},
    )
    def post(self, request: Request) -> Response:
        data = self._payload(request)
        try:
            return Response(
                container.ste_pipeline().answer(data["session_id"], data["char_key"], data["value"])
            )
        except PipelineError as error:
            return _error(error)


class AnswerTextView(_SteView):
    request_serializer = s.AnswerTextRequestSerializer

    @extend_schema(
        tags=["ste"],
        summary="Ответ на уточняющий вопрос свободным текстом",
        request=s.AnswerTextRequestSerializer,
        responses={200: _RESULT, **error_responses(400, 403)},
    )
    def post(self, request: Request) -> Response:
        data = self._payload(request)
        try:
            return Response(
                container.ste_pipeline().answer_text(
                    data["session_id"], data["char_key"], data["text"]
                )
            )
        except PipelineError as error:
            return _error(error)


class ChooseFamilyView(_SteView):
    request_serializer = s.ChooseFamilyRequestSerializer

    @extend_schema(
        tags=["ste"],
        summary="Выбрать тип товара (семейство КТРУ)",
        request=s.ChooseFamilyRequestSerializer,
        responses={200: _RESULT, **error_responses(400, 403)},
    )
    def post(self, request: Request) -> Response:
        data = self._payload(request)
        try:
            return Response(
                container.ste_pipeline().choose_family(data["session_id"], data["family_id"])
            )
        except PipelineError as error:
            return _error(error)


class ChooseLeafView(_SteView):
    request_serializer = s.ChooseLeafRequestSerializer

    @extend_schema(
        tags=["ste"],
        summary="Выбрать конечную позицию КТРУ из оставшихся",
        request=s.ChooseLeafRequestSerializer,
        responses={200: _RESULT, **error_responses(400, 403)},
    )
    def post(self, request: Request) -> Response:
        data = self._payload(request)
        try:
            return Response(container.ste_pipeline().choose_leaf(data["session_id"], data["code"]))
        except PipelineError as error:
            return _error(error)


class EditView(_SteView):
    request_serializer = s.AnswerRequestSerializer

    @extend_schema(
        tags=["ste"],
        summary="Исправить значение характеристики вручную",
        request=s.AnswerRequestSerializer,
        responses={200: _RESULT, **error_responses(400, 403)},
    )
    def post(self, request: Request) -> Response:
        data = self._payload(request)
        try:
            return Response(
                container.ste_pipeline().edit(data["session_id"], data["char_key"], data["value"])
            )
        except PipelineError as error:
            return _error(error)
