"""Auth endpoints under ``/api/v1/auth/``.

Session cookies, not tokens: the SPA sends the cookie and echoes the CSRF token in a header,
which keeps the credential out of reach of any script on the page. The views hold no business
rules — they validate the payload, call a use case and translate the result. Error translation
happens in the shared exception handler.
"""

from __future__ import annotations

from django.contrib.auth import login
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.application.dto import (
    AuthenticateUserInput,
    GetCurrentUserInput,
    RegisterUserInput,
)
from apps.users.domain.entities import User
from apps.users.presentation.serializers import (
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
)
from config import container
from core.presentation.permissions import IsAuthenticatedUser
from core.presentation.schema import error_responses


def _start_session(request: Request, user: User) -> None:
    """Attach a fresh session to the response.

    ``login()`` cycles the session key, which is what prevents session fixation, so it is always
    called *after* a successful authentication, never before.
    """
    row = container.user_model().objects.get(pk=user.id)
    login(request, row, backend="django.contrib.auth.backends.ModelBackend")


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfTokenView(APIView):
    """Primes the ``csrftoken`` cookie.

    The SPA must read the token before its first unsafe request — including the login POST — so
    this is the one endpoint that intentionally sets a cookie readable by JavaScript.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        summary="Выдать CSRF-токен",
        operation_id="auth_csrf",
        request=None,
        responses={204: OpenApiResponse(description="Cookie `csrftoken` установлена.")},
    )
    def get(self, request: Request) -> Response:
        return Response(status=status.HTTP_204_NO_CONTENT)


#: DRF marks every ``APIView`` as ``csrf_exempt`` and re-checks the token inside
#: ``SessionAuthentication`` — which only runs once a request is *already* authenticated. The
#: unsafe endpoints below are reached anonymously, so without this decorator they would accept a
#: cross-site POST: an attacker could sign a visitor into an account of their choosing (login
#: CSRF) or log them out. The SPA already primes the cookie and echoes the header, so enforcing
#: it costs the client nothing.
csrf_required = method_decorator(csrf_protect, name="dispatch")


@csrf_required
class RegisterView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        summary="Зарегистрироваться",
        operation_id="auth_register",
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(
                response=UserSerializer, description="Пользователь создан, сессия открыта."
            ),
            **error_responses(400, 409),
        },
    )
    def post(self, request: Request) -> Response:
        payload = RegisterSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = container.register_user().execute(RegisterUserInput(**payload.validated_data))
        _start_session(request, user)
        return Response(UserSerializer.from_entity(user), status=status.HTTP_201_CREATED)


@csrf_required
class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        summary="Войти",
        operation_id="auth_login",
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(response=UserSerializer, description="Сессия открыта."),
            **error_responses(400, 403),
        },
    )
    def post(self, request: Request) -> Response:
        payload = LoginSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = container.authenticate_user().execute(
            AuthenticateUserInput(**payload.validated_data)
        )
        _start_session(request, user)
        return Response(UserSerializer.from_entity(user), status=status.HTTP_200_OK)


@csrf_required
class LogoutView(APIView):
    """Idempotent: ``AllowAny`` so a lapsed session still gets its cookie cleared."""

    permission_classes = [AllowAny]

    @extend_schema(
        tags=["auth"],
        summary="Выйти",
        operation_id="auth_logout",
        request=None,
        responses={204: OpenApiResponse(description="Сессия завершена. Идемпотентно.")},
    )
    def post(self, request: Request) -> Response:
        # flush() clears the server-side record as well as the cookie; logout() alone can leave
        # a detached session row behind.
        request.session.flush()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """What the header's account button shows."""

    permission_classes = [IsAuthenticatedUser]

    @extend_schema(
        tags=["auth"],
        summary="Текущий пользователь",
        operation_id="auth_me",
        responses={
            200: OpenApiResponse(response=UserSerializer, description="Профиль текущей сессии."),
            **error_responses(403, 404),
        },
    )
    def get(self, request: Request) -> Response:
        user = container.get_current_user().execute(
            GetCurrentUserInput(user_id=request.user.id)
        )
        return Response(UserSerializer.from_entity(user), status=status.HTTP_200_OK)
