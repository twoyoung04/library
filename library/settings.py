import os
import secrets
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
IS_VERCEL = os.environ.get('VERCEL') == '1'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '').strip()
if not SECRET_KEY:
    if IS_VERCEL:
        raise ImproperlyConfigured('Set DJANGO_SECRET_KEY in Vercel environment variables.')
    key_file = BASE_DIR / '.secret_key'
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(48))
        key_file.chmod(0o600)
    SECRET_KEY = key_file.read_text().strip()
DEBUG = os.environ.get('DJANGO_DEBUG', '0' if IS_VERCEL else '1') == '1'
ALLOWED_HOSTS = [v.strip() for v in os.environ.get(
    'DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if v.strip()]
CSRF_TRUSTED_ORIGINS = [v.strip() for v in os.environ.get(
    'DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if v.strip()]
if IS_VERCEL:
    for name in ('VERCEL_URL', 'VERCEL_BRANCH_URL', 'VERCEL_PROJECT_PRODUCTION_URL'):
        host = os.environ.get(name, '').strip()
        if host:
            if host not in ALLOWED_HOSTS:
                ALLOWED_HOSTS.append(host)
            origin = f'https://{host}'
            if origin not in CSRF_TRUSTED_ORIGINS:
                CSRF_TRUSTED_ORIGINS.append(origin)
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'catalog',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'library.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
WSGI_APPLICATION = 'library.wsgi.application'
database_url = os.environ.get('DATABASE_URL', '').strip()
if IS_VERCEL and not database_url:
    raise ImproperlyConfigured('Set DATABASE_URL to the Supabase PostgreSQL pooler URL.')
if database_url:
    url = urlparse(database_url)
    if url.scheme not in ('postgres', 'postgresql') or not url.hostname or not url.path.strip('/'):
        raise ImproperlyConfigured('DATABASE_URL must be a PostgreSQL connection URL.')
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': unquote(url.path.lstrip('/')),
        'USER': unquote(url.username or ''),
        'PASSWORD': unquote(url.password or ''),
        'HOST': url.hostname,
        'PORT': url.port or 5432,
        'OPTIONS': {'sslmode': 'require'},
        'CONN_MAX_AGE': 0,
        'DISABLE_SERVER_SIDE_CURSORS': True,
    }}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'book_list'
LOGOUT_REDIRECT_URL = 'login'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_FAILURE_VIEW = 'library.views.csrf_failure'
