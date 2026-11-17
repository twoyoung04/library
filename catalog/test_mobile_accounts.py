import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Copy, Edition, EmailLoginChallenge, LoginIdentity, MobileToken


@override_settings(MOBILE_EMAIL_ENABLED=True)
class MobileAccountTests(TestCase):
    def post(self, endpoint, data=None, auth=None):
        return self.client.post('/api/mobile/' + endpoint + '/', data or {}, content_type='application/json',
                                **(auth or {}))

    def password_session(self, name='existing'):
        user = get_user_model().objects.create_user(username=name, password='test-only-password')
        response = self.post('login', {'username': name, 'password': 'test-only-password'})
        return user, self.auth(response)

    def auth(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return {'HTTP_AUTHORIZATION': 'Bearer ' + response.json()['token']}

    def send_code(self, email):
        self.assertEqual(self.post('account/email/send', {'email': email}).status_code, 200)
        return re.search(r'[0-9]{6}', mail.outbox[-1].body).group()

    def test_email_binding_and_restore_keep_existing_book_ids(self):
        user, auth = self.password_session()
        edition = Edition.objects.create(owner=user, title='Existing')
        copy = Copy.objects.create(edition=edition)
        email = 'Person@example.org'
        code = self.send_code(email)
        self.assertEqual(self.post('account/email/bind', {'email': email, 'code': code}, auth).status_code, 200)
        self.assertEqual(self.post('account/email/login', {'email': email, 'code': code}).status_code, 400)
        MobileToken.objects.all().delete()
        EmailLoginChallenge.objects.update(sent_at=timezone.now() - timedelta(seconds=61))
        code = self.send_code(email)
        login = self.post('account/email/login', {'email': email, 'code': code})
        auth = self.auth(login)
        self.assertEqual(login.json()['copy_count'], 1)
        snapshot = self.client.get('/api/mobile/snapshot/', **auth).json()
        self.assertEqual(snapshot['items'][0]['copy']['id'], copy.pk)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_email_registration_requires_code_and_reauth_cannot_create_other_account(self):
        email = 'new@example.org'
        code = self.send_code(email)
        self.assertEqual(self.post('account/email/send', {'email': email}).status_code, 429)
        wrong = '000000' if code != '000000' else '000001'
        self.assertEqual(self.post('account/email/login', {'email': email, 'code': wrong}).status_code, 400)
        self.assertEqual(self.post('account/email/login', {
            'email': email, 'code': code, 'expected_username': 'someone-else',
        }).status_code, 401)
        self.assertEqual(get_user_model().objects.count(), 0)
        EmailLoginChallenge.objects.update(sent_at=timezone.now() - timedelta(seconds=61))
        signup = self.post('account/email/login', {'email': email, 'code': self.send_code(email)})
        auth = self.auth(signup)
        self.assertEqual(signup.json()['copy_count'], 0)
        self.assertEqual(self.client.get('/api/mobile/snapshot/', **auth).json()['items'], [])
        self.assertFalse(get_user_model().objects.get().has_usable_password())

    @patch('catalog.apple_login.enabled', return_value=True)
    @patch('catalog.apple_login.verify', return_value=('apple-id', 'encrypted-token'))
    @patch('catalog.apple_login.revoke')
    def test_apple_binding_challenge_and_deletion(self, revoke, verify, _enabled):
        user, auth = self.password_session()
        copy = Copy.objects.create(edition=Edition.objects.create(owner=user, title='Existing'))
        challenge = self.post('account/apple/bind-challenge', auth=auth).json()
        proof = {'challenge': challenge['challenge'], 'identity_token': 'identity', 'authorization_code': 'code'}
        self.assertEqual(self.post('account/apple/login', proof).status_code, 400)
        verify.assert_not_called()
        self.assertEqual(self.post('account/apple/bind', proof, auth).status_code, 200)
        verify.assert_called_once_with('identity', 'code', challenge['nonce'])
        self.assertEqual(LoginIdentity.objects.get().user, user)
        self.assertEqual(self.client.delete('/api/mobile/account/delete/', {'confirm': True},
                                           content_type='application/json', **auth).status_code, 200)
        revoke.assert_called_once_with('encrypted-token')
        self.assertFalse(Copy.objects.filter(pk=copy.pk).exists())
        self.assertEqual(self.client.get('/api/mobile/snapshot/', **auth).status_code, 401)

    def test_expired_verification_cannot_bind_or_delete(self):
        user, auth = self.password_session()
        MobileToken.objects.update(created_at=timezone.now() - timedelta(minutes=11))
        self.assertEqual(self.post('account/email/bind', auth=auth).status_code, 403)
        result = self.client.delete('/api/mobile/account/delete/', {'confirm': True},
                                    content_type='application/json', **auth)
        self.assertEqual(result.status_code, 403)
        user.refresh_from_db()


@override_settings(APPLE_CLIENT_ID='site.wuminglib.mylibrary')
class AppleVerificationTests(TestCase):
    def test_checks_signature_audience_and_nonce_before_token_exchange(self):
        import time
        from types import SimpleNamespace

        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        from . import apple_login

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())
        claims = {
            'iss': apple_login.ISSUER, 'aud': 'site.wuminglib.mylibrary',
            'sub': 'apple-subject', 'iat': now, 'exp': now + 300, 'nonce': 'session-nonce',
        }
        token = jwt.encode(claims, key, algorithm='RS256')
        with patch.object(apple_login.KEYS, 'get_signing_key_from_jwt',
                          return_value=SimpleNamespace(key=key.public_key())), patch(
            'catalog.apple_login._exchange', return_value={'id_token': token, 'refresh_token': 'refresh'},
        ) as exchange:
            invalid = jwt.encode({**claims, 'aud': 'another-app'}, key, algorithm='RS256')
            with self.assertRaises(ValueError):
                apple_login.verify(invalid, 'code', 'session-nonce')
            with self.assertRaises(ValueError):
                apple_login.verify(token, 'code', 'wrong-nonce')
            exchange.assert_not_called()
            subject, stored = apple_login.verify(token, 'code', 'session-nonce')
            self.assertEqual(subject, 'apple-subject')
            self.assertNotEqual(stored, 'refresh')
            apple_login.revoke(stored)
            exchange.assert_called_with('revoke', {'token': 'refresh', 'token_type_hint': 'refresh_token'})
