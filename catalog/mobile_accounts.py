import secrets
from datetime import timedelta
from smtplib import SMTPException
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.csrf import csrf_exempt

from . import apple_login
from .mobile_api import failure, issue_session, mobile_endpoint, payload, token_digest
from .models import AuthRateWindow, EmailLoginChallenge, LoginIdentity

EMAIL = LoginIdentity.Provider.EMAIL
APPLE = LoginIdentity.Provider.APPLE
User = get_user_model()


def public_account_endpoint(method):
    def decorator(view):
        @csrf_exempt
        def endpoint(request):
            if request.method != method:
                response = failure('不支持此请求方法。', 405)
                response['Allow'] = method
                return response
            try:
                return view(request)
            except ValueError as exc:
                return failure(str(exc))
        return endpoint
    return decorator


def _email(data):
    raw = data.get('email')
    email = raw.strip().lower() if isinstance(raw, str) else ''
    try:
        if len(email) > 254:
            raise ValidationError('invalid length')
        validate_email(email)
    except ValidationError as exc:
        raise ValueError('请输入有效的邮箱地址。') from exc
    return email


def _code_digest(key, code):
    return salted_hmac('mobile-email-code', f'{key}:{code}', algorithm='sha256').hexdigest()


def _rate_limit(key, maximum):
    now = timezone.now()
    with transaction.atomic():
        window, _ = AuthRateWindow.objects.select_for_update().get_or_create(
            key=token_digest(key), defaults={'started_at': now},
        )
        if window.started_at <= now - timedelta(hours=1):
            window.started_at, window.count = now, 0
        if window.count >= maximum:
            return False
        window.count += 1
        window.save(update_fields=['started_at', 'count'])
        return True


def _ip(request):
    return request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR', '')


@public_account_endpoint('GET')
def capabilities(request):
    return JsonResponse({'email': settings.MOBILE_EMAIL_ENABLED, 'apple': apple_login.enabled()})


@public_account_endpoint('POST')
def send_code(request):
    if not settings.MOBILE_EMAIL_ENABLED:
        return failure('邮箱登录暂不可用。', 503)
    email = _email(payload(request))
    key = token_digest(email)
    if not _rate_limit('email-ip:' + _ip(request), 30) or not _rate_limit('email:' + key, 8):
        return failure('发送过于频繁，请稍后再试。', 429)
    code = f'{secrets.randbelow(1_000_000):06d}'
    now = timezone.now()
    digest = _code_digest(key, code)
    with transaction.atomic():
        challenge, _ = EmailLoginChallenge.objects.select_for_update().get_or_create(key=key)
        if challenge.sent_at and challenge.sent_at > now - timedelta(seconds=60):
            return failure('请等待 60 秒后再发送。', 429)
        challenge.digest, challenge.sent_at, challenge.attempts = digest, now, 0
        challenge.save(update_fields=['digest', 'sent_at', 'attempts'])
    try:
        delivered = send_mail('我的藏书登录验证码', f'验证码：{code}\n\n10 分钟内有效，请勿转发。', None, [email])
        if delivered != 1:
            raise SMTPException('Email backend did not accept the message')
    except (OSError, SMTPException):
        EmailLoginChallenge.objects.filter(key=key, digest=digest).update(digest='', sent_at=None)
        return failure('邮件暂时无法发送，请稍后重试。', 503)
    return JsonResponse({'ok': True})


def _verify_code(email, value):
    if not isinstance(value, str) or len(value) != 6 or not value.isascii() or not value.isdigit():
        return False
    key = token_digest(email)
    with transaction.atomic():
        challenge = EmailLoginChallenge.objects.select_for_update().filter(key=key).first()
        if (not challenge or not challenge.digest or not challenge.sent_at
                or challenge.sent_at <= timezone.now() - timedelta(minutes=10) or challenge.attempts >= 5):
            return False
        challenge.attempts += 1
        correct = constant_time_compare(challenge.digest, _code_digest(key, value))
        if correct:
            challenge.digest = ''
        challenge.save(update_fields=['digest', 'attempts'])
        return correct


def _identity_session(provider, subject, encrypted_token='', owner=None, expected_username=None):
    try:
        with transaction.atomic():
            existing = LoginIdentity.objects.filter(provider=provider, subject=subject).first()
            if expected_username and (not existing or existing.user.get_username() != expected_username):
                return failure('请使用当前书库所属的账号验证。', 401)
            if owner:
                user = User.objects.select_for_update().filter(pk=owner.pk, is_active=True).first()
                if not user:
                    return failure('请重新登录。', 401)
                if existing and existing.user_id != user.pk:
                    return failure('此登录方式已绑定另一个账号。', 409)
                if provider == APPLE and LoginIdentity.objects.filter(user=user, provider=APPLE).exclude(
                    subject=subject
                ).exists():
                    return failure('此账号已绑定另一个 Apple 账号。', 409)
            elif existing:
                user = User.objects.select_for_update().filter(pk=existing.user_id, is_active=True).first()
                if not user or not LoginIdentity.objects.filter(
                    user=user, provider=provider, subject=subject
                ).exists():
                    return failure('登录方式已更新，请重新验证。', 403)
            else:
                user = User.objects.create_user(username='library-' + uuid4().hex)
            LoginIdentity.objects.update_or_create(user=user, provider=provider, defaults={
                'subject': subject, 'encrypted_refresh_token': encrypted_token,
            })
            return JsonResponse({'ok': True}) if owner else issue_session(user)
    except IntegrityError:
        return failure('登录方式已被使用，请重新验证。', 409)


def _fresh(request):
    return request.mobile_token.created_at >= timezone.now() - timedelta(minutes=10)


def _email_finish(request, owner=None):
    if not settings.MOBILE_EMAIL_ENABLED:
        return failure('邮箱登录暂不可用。', 503)
    data = payload(request)
    email = _email(data)
    if not _verify_code(email, data.get('code')):
        return failure('验证码错误或已过期，请重新获取。', 400)
    return _identity_session(EMAIL, email, owner=owner, expected_username=data.get('expected_username'))


@public_account_endpoint('POST')
def email_login(request):
    return _email_finish(request)


@mobile_endpoint({'POST'})
def email_bind(request):
    if not _fresh(request):
        return failure('请先重新验证当前账号，再绑定邮箱。', 403)
    return _email_finish(request, request.mobile_user)


def _apple_challenge(owner_key):
    if not apple_login.enabled():
        return failure('Apple 登录暂不可用。', 503)
    nonce = secrets.token_urlsafe(32)
    challenge = signing.dumps({'nonce': nonce, 'owner': owner_key}, salt='mobile-apple-login')
    return JsonResponse({'nonce': nonce, 'challenge': challenge})


@public_account_endpoint('POST')
def apple_challenge(request):
    if not _rate_limit('apple-ip:' + _ip(request), 45):
        return failure('尝试次数过多，请稍后重试。', 429)
    return _apple_challenge(None)


@mobile_endpoint({'POST'})
def apple_bind_challenge(request):
    if not _fresh(request):
        return failure('请先重新验证当前账号，再绑定 Apple 账号。', 403)
    return _apple_challenge(request.mobile_token.pk)


def _apple_finish(request, owner=None):
    if not apple_login.enabled():
        return failure('Apple 登录暂不可用。', 503)
    if not _rate_limit('apple-verify-ip:' + _ip(request), 60):
        return failure('尝试次数过多，请稍后重试。', 429)
    data = payload(request)
    try:
        fields = [data.get(name) for name in ('challenge', 'identity_token', 'authorization_code')]
        if not all(isinstance(field, str) and 0 < len(field) <= 8192 for field in fields):
            raise signing.BadSignature()
        challenge, identity_token, code = fields
        decoded = signing.loads(challenge, salt='mobile-apple-login', max_age=300)
        if decoded['owner'] != (request.mobile_token.pk if owner else None):
            raise signing.BadSignature()
        subject, token = apple_login.verify(identity_token, code, decoded['nonce'])
    except signing.BadSignature:
        return failure('Apple 授权已失效，请重试。', 400)
    except apple_login.AppleUnavailable:
        return failure('暂时无法连接 Apple，请稍后再试。', 503)
    return _identity_session(APPLE, subject, token, owner=owner, expected_username=data.get('expected_username'))


@public_account_endpoint('POST')
def apple_sign_in(request):
    return _apple_finish(request)


@mobile_endpoint({'POST'})
def apple_bind(request):
    if not _fresh(request):
        return failure('请先重新验证当前账号，再绑定 Apple 账号。', 403)
    return _apple_finish(request, request.mobile_user)


@mobile_endpoint({'GET'})
def identities(request):
    bound = {item.provider: item.subject for item in LoginIdentity.objects.filter(user=request.mobile_user)}
    return JsonResponse({'email': bound.get(EMAIL), 'apple': APPLE in bound, 'recently_verified': _fresh(request)})


@mobile_endpoint({'DELETE'})
def delete_account(request):
    if request.mobile_user.is_staff or request.mobile_user.is_superuser:
        return failure('管理账号请通过服务器管理流程注销。', 403)
    if not _fresh(request):
        return failure('请先重新验证账号，再注销。', 403)
    if payload(request).get('confirm') != True:
        return failure('请确认注销。')
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=request.mobile_user.pk).first()
        if user is None:
            return JsonResponse({'ok': True})
        apple = LoginIdentity.objects.filter(user=user, provider=APPLE).first()
        if apple:
            try:
                apple_login.revoke(apple.encrypted_refresh_token)
            except apple_login.AppleUnavailable:
                return failure('暂时无法撤销 Apple 授权，请重试。', 503)
        user.delete()
    return JsonResponse({'ok': True})
