"""User-domain errors. Messages are the text shown to the end user."""

from core.domain.errors import AccessDeniedError, ConflictError, NotFoundError, ValidationError


class EmailAlreadyTaken(ConflictError):
    code = "email_already_taken"
    message = "Пользователь с таким адресом электронной почты уже зарегистрирован."


class InvalidCredentials(ValidationError):
    # Deliberately identical for a wrong password and for an unknown email: a different message
    # would turn the endpoint into a user-enumeration oracle.
    code = "invalid_credentials"
    message = "Неверный адрес электронной почты или пароль."


class UserInactive(AccessDeniedError):
    code = "user_inactive"
    message = "Учётная запись заблокирована."


class UserNotFound(NotFoundError):
    code = "user_not_found"
    message = "Пользователь не найден."


class InvalidEmail(ValidationError):
    code = "invalid_email"
    message = "Некорректный адрес электронной почты."


class InvalidFullName(ValidationError):
    code = "invalid_full_name"
    message = "Укажите имя и фамилию."


class WeakPassword(ValidationError):
    code = "weak_password"
    message = "Пароль не соответствует требованиям безопасности."
