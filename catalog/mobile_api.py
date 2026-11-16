"""Small JSON API for the native iOS client. Browser views keep their session auth."""

import hashlib
import json
import secrets
from datetime import date, timedelta
from functools import wraps
from uuid import UUID

from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, F, IntegerField, Q, When
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .forms import parse_tag_names
from .isbn import normalize_isbn
from .models import Copy, Edition, MobileSaveRequest, MobileToken, Tag
from .views import _add_tags, _book_metadata, _edition_fields, _save_tags


def failure(message, status=400):
    return JsonResponse({'error': message}, status=status)


def payload(request):
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        raise ValueError('请求内容不是有效 JSON。')
    if not isinstance(data, dict):
        raise ValueError('请求内容必须是对象。')
    return data


def token_digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def mobile_endpoint(methods):
    def decorate(view):
        @csrf_exempt
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = failure('不支持此请求方法。', 405)
                response['Allow'] = ', '.join(methods)
                return response
            header = request.headers.get('Authorization', '')
            if not header.startswith('Bearer '):
                return failure('请先登录。', 401)
            raw = header[7:].strip()
            token = MobileToken.objects.select_related('user').filter(
                digest=token_digest(raw), expires_at__gt=timezone.now(), user__is_active=True,
            ).first() if raw else None
            if token is None:
                return failure('登录已过期，请重新登录。', 401)
            request.mobile_token = token
            request.mobile_user = token.user
            try:
                return view(request, *args, **kwargs)
            except ValueError as exc:
                return failure(str(exc))
        return wrapped
    return decorate


def copy_json(copy):
    return {
        'id': copy.pk, 'edition_id': copy.edition_id,
        'location': copy.location, 'status': copy.status,
        'acquired_on': copy.acquired_on.isoformat() if copy.acquired_on else None,
        'notes': copy.notes, 'created_at': copy.created_at.isoformat(),
    }


def edition_json(edition, include_copies=False):
    data = {
        'id': edition.pk, 'isbn': edition.isbn, 'title': edition.title,
        'author': edition.author, 'publisher': edition.publisher,
        'published_year': edition.published_year, 'cover_url': edition.cover_display_url,
        'cover_source_url': edition.cover_url,
        'tags': [{'id': tag.pk, 'name': tag.name} for tag in edition.tags.all()],
        'copy_count': getattr(edition, 'copy_count', None),
        'updated_at': edition.updated_at.isoformat(),
    }
    if include_copies:
        data['copies'] = [copy_json(copy) for copy in edition.copies.all()]
        data['copy_count'] = len(data['copies'])
    return data


@csrf_exempt
def login(request):
    if request.method != 'POST':
        return failure('不支持此请求方法。', 405)
    try:
        data = payload(request)
    except ValueError as exc:
        return failure(str(exc))
    username = str(data.get('username') or '').strip()[:150]
    password = str(data.get('password') or '')
    # A small per-account throttle avoids turning the native endpoint into an
    # unrestricted password oracle. Successful login clears the failure count.
    throttle_key = 'mobile-login:' + hashlib.sha256(username.casefold().encode()).hexdigest()
    failures = cache.get(throttle_key, 0)
    if failures >= 10:
        return failure('尝试次数过多，请稍后重试。', 429)
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        cache.set(throttle_key, failures + 1, 15 * 60)
        return failure('用户名或密码错误。', 401)
    cache.delete(throttle_key)
    raw = secrets.token_urlsafe(48)
    expires = timezone.now() + timedelta(days=90)
    MobileToken.objects.create(user=user, digest=token_digest(raw), expires_at=expires)
    return JsonResponse({'token': raw, 'expires_at': expires.isoformat(), 'username': user.get_username()})


@mobile_endpoint({'POST'})
def logout(request):
    request.mobile_token.delete()
    return JsonResponse({'ok': True})


@mobile_endpoint({'GET'})
def me(request):
    return JsonResponse({'username': request.mobile_user.get_username()})


@mobile_endpoint({'GET'})
def books(request):
    copies = Copy.objects.select_related('edition').prefetch_related('edition__tags')
    query = request.GET.get('q', '').strip()[:100]
    tag = request.GET.get('tag', '')
    location = request.GET.get('location', '')[:126]
    sort = request.GET.get('sort', 'added_desc')
    if query:
        copies = copies.filter(Q(edition__title__icontains=query) | Q(edition__author__icontains=query)
                              | Q(edition__isbn__icontains=query) | Q(edition__publisher__icontains=query)
                              | Q(location__icontains=query) | Q(edition__tags__name__icontains=query)).distinct()
    if tag.isdigit():
        copies = copies.filter(edition__tags__pk=int(tag))
    if location == 'missing':
        copies = copies.filter(location='')
    elif location.startswith('place:'):
        copies = copies.filter(location=location[6:])
    if sort in ('title_asc', 'title_desc'):
        copies = copies.order_by(Case(When(edition__title='', then=1), default=0,
                                      output_field=IntegerField()),
                                  'edition__title' if sort == 'title_asc' else '-edition__title', '-created_at', '-id')
    elif sort in ('year_asc', 'year_desc'):
        year = F('edition__published_year')
        copies = copies.order_by(year.asc(nulls_last=True) if sort == 'year_asc'
                                 else year.desc(nulls_last=True), '-created_at', '-id')
    elif sort == 'added_asc':
        copies = copies.order_by('created_at', 'id')
    else:
        copies = copies.order_by('-created_at', '-id')
    page = Paginator(copies, 30).get_page(request.GET.get('page'))
    return JsonResponse({
        'items': [{'copy': copy_json(copy), 'edition': edition_json(copy.edition)} for copy in page],
        'page': page.number, 'next_page': page.next_page_number() if page.has_next() else None,
        'total': page.paginator.count,
    })


@mobile_endpoint({'GET'})
def book_detail(request, pk):
    edition = Edition.objects.prefetch_related('tags', 'copies').filter(pk=pk).first()
    return JsonResponse({'book': edition_json(edition, True)}) if edition else failure('图书不存在。', 404)


@mobile_endpoint({'GET'})
def filters(request):
    tags = list(Tag.objects.order_by('name').values('id', 'name'))
    locations = list(Copy.objects.exclude(location='').order_by('location')
                     .values_list('location', flat=True).distinct())
    return JsonResponse({'tags': tags, 'locations': locations})


@mobile_endpoint({'GET'})
def lookup(request):
    isbn = normalize_isbn(request.GET.get('isbn', ''))
    edition = Edition.objects.prefetch_related('tags').filter(isbn=isbn).first()
    if edition:
        result = edition_json(edition)
        result['copy_count'] = edition.copies.count()
        return JsonResponse({'existing': True, 'isbn': isbn, 'book': result})
    return JsonResponse({'existing': False, 'isbn': isbn, 'metadata': _edition_fields(_book_metadata(isbn))})


def clean_fields(data, *, require_identity=False):
    raw_isbn = str(data.get('isbn') or '').strip()
    isbn = normalize_isbn(raw_isbn) if raw_isbn else None
    title = str(data.get('title') or '').strip()
    if require_identity and not (isbn or title):
        raise ValueError('请填写 ISBN 或书名。')
    text_fields = {'title': 300, 'author': 300, 'publisher': 200, 'cover_url': 1000}
    fields = {'isbn': isbn}
    for name, limit in text_fields.items():
        value = str(data.get(name) or '').strip()
        if len(value) > limit:
            raise ValueError(f'{name} 内容过长。')
        fields[name] = value
    year = data.get('published_year')
    if year not in (None, ''):
        if isinstance(year, bool) or not str(year).isdigit() or not 1000 <= int(year) <= 2100:
            raise ValueError('出版年无效。')
        year = int(year)
    fields['published_year'] = year or None
    return fields


def clean_copy(data):
    location = str(data.get('location') or '').strip()
    notes = str(data.get('notes') or '').strip()
    if len(location) > 120:
        raise ValueError('存放位置最多 120 字。')
    raw_date = data.get('acquired_on')
    try:
        acquired = date.fromisoformat(raw_date) if raw_date else None
    except (TypeError, ValueError):
        raise ValueError('入藏日期需为 YYYY-MM-DD。')
    status = str(data.get('status') or Copy.Status.UNREAD)
    if status not in Copy.Status.values:
        raise ValueError('阅读状态无效。')
    return {'location': location, 'status': status, 'notes': notes, 'acquired_on': acquired}


@mobile_endpoint({'POST'})
def create_copy(request):
    data = payload(request)
    try:
        request_id = UUID(str(data.get('request_id') or ''))
    except ValueError:
        raise ValueError('缺少有效的 request_id。')
    previous = MobileSaveRequest.objects.select_related('copy__edition').filter(
        user=request.mobile_user, request_id=request_id).first()
    if previous:
        return JsonResponse({'copy': copy_json(previous.copy), 'edition_id': previous.copy.edition_id,
                             'replayed': True})
    fields = clean_fields(data, require_identity=True)
    copy_fields = clean_copy(data)
    tag_names = parse_tag_names(data.get('tags', ''))
    try:
        with transaction.atomic():
            edition = Edition.objects.filter(isbn=fields['isbn']).first() if fields['isbn'] else None
            if edition is None:
                metadata = _edition_fields(_book_metadata(fields['isbn'])) if fields['isbn'] and not fields['title'] else {}
                values = {name: fields[name] or metadata.get(name) for name in fields}
                for name in ('title', 'author', 'publisher', 'cover_url'):
                    values[name] = values[name] or ''
                if fields['isbn']:
                    edition, created = Edition.objects.get_or_create(isbn=fields['isbn'], defaults={
                        name: value for name, value in values.items() if name != 'isbn'})
                else:
                    edition = Edition.objects.create(**values)
                    created = True
            else:
                created = False
            if not created and not edition.title:
                metadata = _edition_fields(_book_metadata(fields['isbn'])) if not fields['title'] else {}
                for name in ('title', 'author', 'publisher', 'published_year', 'cover_url'):
                    value = fields[name] or metadata.get(name)
                    if value and not getattr(edition, name):
                        setattr(edition, name, value)
                edition.save()
            _add_tags(edition, tag_names)
            copy = Copy.objects.create(edition=edition, **copy_fields)
            MobileSaveRequest.objects.create(user=request.mobile_user, request_id=request_id, copy=copy)
    except IntegrityError:
        previous = MobileSaveRequest.objects.select_related('copy').filter(
            user=request.mobile_user, request_id=request_id).first()
        if previous is None:
            raise
        return JsonResponse({'copy': copy_json(previous.copy), 'edition_id': previous.copy.edition_id,
                             'replayed': True})
    return JsonResponse({'copy': copy_json(copy), 'edition_id': edition.pk, 'replayed': False}, status=201)


@mobile_endpoint({'PATCH'})
def update_book(request, pk):
    data = payload(request)
    edition = Edition.objects.filter(pk=pk).first()
    if edition is None:
        return failure('图书不存在。', 404)
    current = {name: getattr(edition, name) for name in
               ('isbn', 'title', 'author', 'publisher', 'published_year', 'cover_url')}
    fields = clean_fields({**current, **data}, require_identity=True)
    if fields['isbn'] and Edition.objects.filter(isbn=fields['isbn']).exclude(pk=pk).exists():
        return failure('这个 ISBN 已属于另一条图书记录。', 409)
    for name, value in fields.items():
        setattr(edition, name, value)
    with transaction.atomic():
        edition.save()
        if 'tags' in data:
            _save_tags(edition, parse_tag_names(data['tags']))
    edition = Edition.objects.prefetch_related('tags', 'copies').get(pk=pk)
    return JsonResponse({'book': edition_json(edition, True)})


@mobile_endpoint({'PATCH', 'DELETE'})
def copy_detail(request, pk):
    copy = Copy.objects.select_related('edition').filter(pk=pk).first()
    if copy is None:
        return failure('这册书不存在。', 404)
    if request.method == 'PATCH':
        data = payload(request)
        current = copy_json(copy)
        fields = clean_copy({**current, **data})
        for name, value in fields.items():
            setattr(copy, name, value)
        copy.save(update_fields=list(fields))
        return JsonResponse({'copy': copy_json(copy)})
    edition = copy.edition
    with transaction.atomic():
        copy.delete()
        if not edition.copies.exists():
            edition.delete()
    return JsonResponse({'ok': True})
