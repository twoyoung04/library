import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.utils.crypto import salted_hmac

ISSUER = 'https://appleid.apple.com'
KEYS = jwt.PyJWKClient(ISSUER + '/auth/keys', timeout=5)


class AppleUnavailable(Exception):
    pass


def enabled():
    return all((settings.APPLE_CLIENT_ID, settings.APPLE_TEAM_ID, settings.APPLE_KEY_ID, settings.APPLE_PRIVATE_KEY))


def _exchange(operation, fields):
    if not enabled():
        raise AppleUnavailable()
    now = int(time.time())
    try:
        secret = jwt.encode({
            'iss': settings.APPLE_TEAM_ID, 'sub': settings.APPLE_CLIENT_ID,
            'aud': ISSUER, 'iat': now, 'exp': now + 300,
        }, settings.APPLE_PRIVATE_KEY, algorithm='ES256', headers={'kid': settings.APPLE_KEY_ID})
    except (jwt.InvalidKeyError, ValueError) as exc:
        raise AppleUnavailable() from exc
    request = Request(f'{ISSUER}/auth/{operation}', data=urlencode({
        'client_id': settings.APPLE_CLIENT_ID, 'client_secret': secret, **fields,
    }).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read(65536)
    except HTTPError as exc:
        if operation == 'token' and exc.code == 400:
            try:
                detail = json.loads(exc.read(8192))
            except ValueError:
                detail = {}
            if detail.get('error') == 'invalid_grant':
                raise ValueError('Apple 授权已失效，请重试。') from exc
        raise AppleUnavailable() from exc
    except (URLError, TimeoutError) as exc:
        raise AppleUnavailable() from exc
    return json.loads(body) if body else {}


def _claims(token):
    try:
        signing_key = KEYS.get_signing_key_from_jwt(token).key
        return jwt.decode(token, signing_key, algorithms=['RS256'], audience=settings.APPLE_CLIENT_ID,
                          issuer=ISSUER, options={'require': ['exp', 'iat', 'sub']})
    except jwt.PyJWKClientConnectionError as exc:
        raise AppleUnavailable() from exc
    except jwt.PyJWTError as exc:
        raise ValueError('Apple 授权无法验证，请重试。') from exc


def _cipher():
    keys = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(salted_hmac(
            'apple-refresh-token', '', secret=key, algorithm='sha256',
        ).digest()))
        for key in keys
    ])


def verify(identity_token, code, nonce):
    claims = _claims(identity_token)
    if claims.get('nonce') != nonce:
        raise ValueError('Apple 授权已失效，请重试。')
    result = _exchange('token', {'code': code, 'grant_type': 'authorization_code'})
    if not result.get('id_token') or not result.get('refresh_token'):
        raise AppleUnavailable()
    if _claims(result['id_token'])['sub'] != claims['sub']:
        raise ValueError('Apple 授权无法验证，请重试。')
    return claims['sub'], _cipher().encrypt(result['refresh_token'].encode()).decode()


def revoke(encrypted_token):
    try:
        token = _cipher().decrypt(encrypted_token.encode()).decode()
    except InvalidToken as exc:
        raise AppleUnavailable() from exc
    _exchange('revoke', {'token': token, 'token_type_hint': 'refresh_token'})
