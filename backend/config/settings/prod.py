"""Deployed settings. Nothing here may fall back to a development default."""

from config.settings.base import *  # noqa: F403
from config.settings.base import env

DEBUG = False

# No default: an unset secret must fail the boot, not silently ship a known key.
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)

SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
# nginx redirects to HTTPS; keeping this on makes Django refuse to answer a plain-HTTP request
# that slipped past the proxy rather than setting cookies over it.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
X_FRAME_OPTIONS = "DENY"
