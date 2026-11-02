"""Private COS cover storage and bounded downloads from public image hosts."""
import hashlib
import http.client
import io
import ipaddress
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, build_opener

from django.conf import settings
from PIL import Image

MAX_BYTES = 8 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000
PLACEHOLDER_HASHES = {
    '5c7e44ecf3964cbf4d6609fb1f63f47434774fe70a1047697b6df20ed263f15c',
    'daf7b8338800d61048583a009f6392376a3eb619bd545b587f0e2e96847d3d38',
}


def source_prefix(url):
    return 'covers/' + hashlib.sha256(url.encode()).hexdigest() + '/'


def current_key(edition):
    return bool(edition.cover_object_key and edition.cover_object_key.startswith(source_prefix(edition.cover_url))
                and edition.cover_object_key.rsplit('/', 1)[-1].split('.')[0] not in PLACEHOLDER_HASHES)


def client():
    from qcloud_cos import CosConfig, CosS3Client
    if not settings.COS_REGION or not settings.COS_SECRET_ID or not settings.COS_SECRET_KEY:
        raise ValueError('Configure COS_REGION, COS_SECRET_ID and COS_SECRET_KEY')
    return CosS3Client(CosConfig(Region=settings.COS_REGION, SecretId=settings.COS_SECRET_ID,
                               SecretKey=settings.COS_SECRET_KEY, Scheme='https', Timeout=15))


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Invalid cover URL')
    if parts.port not in (None, 80, 443):
        raise ValueError('Invalid cover port')


class PublicConnection:
    def connect(self):
        super().connect()
        if not ipaddress.ip_address(self.sock.getpeername()[0]).is_global:
            self.close()
            raise ValueError('Cover host must be public')


class PublicHTTPConnection(PublicConnection, http.client.HTTPConnection):
    pass


class PublicHTTPSConnection(PublicConnection, http.client.HTTPSConnection):
    pass


class PublicHTTPHandler(HTTPHandler):
    def http_open(self, request):
        validate_url(request.full_url)
        return self.do_open(PublicHTTPConnection, request)


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        validate_url(request.full_url)
        return self.do_open(PublicHTTPSConnection, request)


class PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_image(url):
    from urllib.request import ProxyHandler, Request
    validate_url(url)
    opener = build_opener(ProxyHandler({}), PublicHTTPHandler(), PublicHTTPSHandler(), PublicRedirectHandler())
    headers = {'User-Agent': 'PersonalLibrary/1.0'}
    if (urlsplit(url).hostname or '').endswith('.doubanio.com'):
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://book.douban.com/'}
    with opener.open(Request(url, headers=headers), timeout=15) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Cover exceeds 8 MB')
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('JPEG', 'PNG', 'WEBP', 'GIF'):
            raise ValueError('Unsupported cover format')
        if image.width * image.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError('Cover dimensions too large')
        image.load()
        image = image.convert('RGB')
        image.thumbnail((1600, 2400))
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=88)
    data = output.getvalue()
    if hashlib.sha256(data).hexdigest() in PLACEHOLDER_HASHES:
        raise ValueError('Known placeholder image')
    return data


def sync_edition(edition, storage):
    from .models import Edition
    if not edition.cover_url or current_key(edition):
        return False
    source = edition.cover_url
    image_url = source
    if urlsplit(source).hostname in ('book345.com', 'www.book345.com') and edition.isbn:
        from .lookup import lookup_wuming
        metadata = lookup_wuming(edition.isbn) or {}
        image_url = metadata.get('cover_url') or source
    body = download_image(image_url)
    key = source_prefix(source) + hashlib.sha256(body).hexdigest() + '.jpg'
    storage.put_object(Bucket=settings.COS_BUCKET, Key=key, Body=body,
                       ContentType='image/jpeg', CacheControl='private, max-age=86400', EnableMD5=True)
    # A concurrent edit must not attach an old cover to the new source URL.
    return bool(Edition.objects.filter(pk=edition.pk, cover_url=source).update(cover_object_key=key))
