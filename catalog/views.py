import csv
import hashlib
import io
import os
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .forms import CategoryForm, CopyForm, EditionForm
from .isbn import normalize_isbn
from .lookup import lookup_book
from .models import Category, Copy, Edition, Tag


def _save_tags(edition, names):
    edition.tags.set([Tag.objects.get_or_create(name=name)[0] for name in names])


def _descendant_ids(category_id):
    ids = {category_id}
    pending = [category_id]
    while pending:
        children = list(Category.objects.filter(parent_id__in=pending).values_list('id', flat=True))
        pending = [pk for pk in children if pk not in ids]
        ids.update(pending)
    return ids


def _book_metadata(isbn):
    provider_config = os.environ.get('SHOWAPI_APP_KEY', '') + '|' + os.environ.get('JUHE_ISBN_KEY', '')
    provider_version = hashlib.sha256(provider_config.encode()).hexdigest()[:12]
    key = f'book-lookup:v4:{provider_version}:{isbn}'
    result = cache.get(key)
    if result is None:
        result = lookup_book(isbn) or {}
        cache.set(key, result, 24 * 60 * 60 if result else 60 * 60)
    return result


def _edition_fields(metadata):
    year = metadata.get('published_year')
    return {
        'title': str(metadata.get('title') or '').strip()[:300],
        'author': str(metadata.get('author') or '').strip()[:300],
        'publisher': str(metadata.get('publisher') or '').strip()[:200],
        'published_year': year if isinstance(year, int) and 1000 <= year <= 2100 else None,
        'cover_url': str(metadata.get('cover_url') or '').strip()[:1000],
    }


@login_required
def book_list(request):
    copies = Copy.objects.select_related('edition', 'edition__category').prefetch_related('edition__tags')
    query = request.GET.get('q', '').strip()[:100]
    category = request.GET.get('category', '')
    status = request.GET.get('status', '')
    if query:
        copies = copies.filter(Q(edition__title__icontains=query) |
                               Q(edition__author__icontains=query) |
                               Q(edition__isbn__icontains=query) |
                               Q(edition__publisher__icontains=query) |
                               Q(location__icontains=query) |
                               Q(edition__tags__name__icontains=query)).distinct()
    if category.isdigit() and Category.objects.filter(pk=category).exists():
        copies = copies.filter(edition__category_id__in=_descendant_ids(int(category)))
    if status in Copy.Status.values:
        copies = copies.filter(status=status)
    page = Paginator(copies, 24).get_page(request.GET.get('page'))
    return render(request, 'catalog/list.html', {
        'page': page, 'query': query, 'selected_category': category,
        'selected_status': status, 'categories': Category.objects.all(),
        'statuses': Copy.Status.choices, 'total_copies': Copy.objects.count(),
        'total_editions': Edition.objects.count(),
        'read_count': Copy.objects.filter(status=Copy.Status.READ).count(),
    })


@login_required
def book_new(request):
    if request.method == 'POST':
        try:
            submitted_isbn = normalize_isbn(request.POST.get('isbn', '')) if request.POST.get('isbn', '').strip() else None
        except ValueError:
            submitted_isbn = None
        existing_for_validation = Edition.objects.filter(isbn=submitted_isbn).first() if submitted_isbn else None
        edition_form = EditionForm(request.POST, instance=existing_for_validation)
        copy_form = CopyForm(request.POST)
        if edition_form.is_valid() and copy_form.is_valid():
            with transaction.atomic():
                isbn = edition_form.cleaned_data['isbn']
                existing = Edition.objects.filter(isbn=isbn).first() if isbn else None
                if existing:
                    edition = existing
                    if not edition.title:
                        fields = ('title', 'author', 'publisher', 'published_year', 'cover_url')
                        updates = []
                        for field in fields:
                            value = edition_form.cleaned_data[field]
                            if value and not getattr(edition, field):
                                setattr(edition, field, value)
                                updates.append(field)
                        if updates:
                            edition.save(update_fields=updates + ['updated_at'])
                    messages.info(request, '这个 ISBN 已存在，已为它新增一册。')
                else:
                    edition = edition_form.save()
                    _save_tags(edition, edition_form.cleaned_data['tags_text'])
                    messages.success(request, '图书已录入。')
                copy = copy_form.save(commit=False)
                copy.edition = edition
                copy.save()
            return redirect('book_detail', pk=edition.pk)
    else:
        initial = {}
        raw_isbn = request.GET.get('isbn', '')
        try:
            if raw_isbn:
                initial['isbn'] = normalize_isbn(raw_isbn)
        except ValueError:
            pass
        edition_form = EditionForm(initial=initial)
        copy_form = CopyForm()
    return render(request, 'catalog/form.html', {
        'edition_form': edition_form, 'copy_form': copy_form,
        'heading': '录入新书', 'mode': 'new',
    })


@login_required
def book_detail(request, pk):
    edition = get_object_or_404(Edition.objects.select_related('category').prefetch_related('tags', 'copies'), pk=pk)
    return render(request, 'catalog/detail.html', {'edition': edition})


@login_required
def book_edit(request, pk):
    edition = get_object_or_404(Edition, pk=pk)
    if request.method == 'POST':
        form = EditionForm(request.POST, instance=edition)
        if form.is_valid():
            edition = form.save()
            _save_tags(edition, form.cleaned_data['tags_text'])
            messages.success(request, '图书信息已更新。')
            return redirect('book_detail', pk=pk)
    else:
        form = EditionForm(instance=edition, initial={'tags_text': '，'.join(edition.tags.values_list('name', flat=True))})
    return render(request, 'catalog/form.html', {
        'edition_form': form, 'heading': '编辑图书信息', 'mode': 'edit', 'edition': edition,
    })


@login_required
def copy_edit(request, pk):
    copy = get_object_or_404(Copy.objects.select_related('edition'), pk=pk)
    if request.method == 'POST':
        form = CopyForm(request.POST, instance=copy)
        if form.is_valid():
            form.save()
            messages.success(request, '这册书的信息已更新。')
            return redirect('book_detail', pk=copy.edition_id)
    else:
        form = CopyForm(instance=copy)
    return render(request, 'catalog/copy_form.html', {'form': form, 'copy': copy})


@login_required
@require_POST
def copy_delete(request, pk):
    copy = get_object_or_404(Copy, pk=pk)
    edition = copy.edition
    copy.delete()
    if not edition.copies.exists():
        edition.delete()
        messages.success(request, '最后一册已删除，图书记录也已移除。')
        return redirect('book_list')
    messages.success(request, '这册书已删除。')
    return redirect('book_detail', pk=edition.pk)


@login_required
def categories(request):
    form = CategoryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, '分类已添加。')
        return redirect('categories')
    items = Category.objects.select_related('parent').annotate(book_count=Count('editions', distinct=True))
    return render(request, 'catalog/categories.html', {'form': form, 'items': items})


@login_required
@require_POST
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    category.delete()
    messages.success(request, '分类已删除，相关图书保留。')
    return redirect('categories')


@login_required
@require_GET
def lookup_isbn(request):
    try:
        isbn = normalize_isbn(request.GET.get('isbn', ''))
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    existing = Edition.objects.filter(isbn=isbn).annotate(copy_count=Count('copies')).first()
    if existing:
        found = _edition_fields(_book_metadata(isbn)) if not existing.title else {}
        return JsonResponse({'existing': True, 'edition_id': existing.pk,
                             'copy_count': existing.copy_count, 'title': existing.title or found.get('title', ''),
                             'author': existing.author or found.get('author', ''),
                             'publisher': existing.publisher or found.get('publisher', ''),
                             'published_year': existing.published_year or found.get('published_year'),
                             'cover_url': existing.cover_url or found.get('cover_url', ''), 'isbn': isbn})
    result = _book_metadata(isbn)
    return JsonResponse({'existing': False, 'isbn': isbn, **result})


@login_required
@require_POST
def scan_save(request):
    try:
        isbn = normalize_isbn(request.POST.get('isbn', ''))
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    copy_form = CopyForm(request.POST)
    if not copy_form.is_valid():
        return JsonResponse({'error': '请检查这册书的位置、状态或日期。',
                             'fields': copy_form.errors.get_json_data()}, status=400)

    existing = Edition.objects.filter(isbn=isbn).first()
    metadata = _book_metadata(isbn) if not existing or not existing.title else {}
    fields = _edition_fields(metadata)
    with transaction.atomic():
        edition, created = Edition.objects.get_or_create(isbn=isbn, defaults=fields)
        if not created and not edition.title and fields['title']:
            updates = []
            for field, value in fields.items():
                if value and not getattr(edition, field):
                    setattr(edition, field, value)
                    updates.append(field)
            if updates:
                edition.save(update_fields=updates + ['updated_at'])
        copy = copy_form.save(commit=False)
        copy.edition = edition
        copy.save()
        copy_count = edition.copies.count()

    return JsonResponse({
        'saved': True, 'isbn': isbn, 'title': edition.title,
        'author': edition.author, 'publisher': edition.publisher,
        'published_year': edition.published_year, 'cover_url': edition.cover_url,
        'source': metadata.get('source') or '', 'copy_count': copy_count,
        'edition_id': edition.pk, 'copy_id': copy.pk,
        'needs_details': not bool(edition.title),
        'detail_url': reverse('book_detail', args=[edition.pk]),
        'edit_url': reverse('book_edit', args=[edition.pk]),
    })


CSV_FIELDS = ['ISBN', '书名', '作者', '出版社', '出版年', '封面链接',
              '分类', '标签', '存放位置', '阅读状态', '入藏日期', '备注']


@login_required
@require_GET
def export_csv(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="personal-library.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(CSV_FIELDS)
    for copy in Copy.objects.select_related('edition', 'edition__category').prefetch_related('edition__tags').iterator(chunk_size=500):
        book = copy.edition
        writer.writerow([
            book.isbn or '', book.title, book.author, book.publisher,
            book.published_year or '', book.cover_url,
            book.category.name if book.category else '',
            '、'.join(book.tags.values_list('name', flat=True)), copy.location,
            copy.status, copy.acquired_on or '', copy.notes,
        ])
    return response


@login_required
def import_csv(request):
    if request.method == 'POST':
        upload = request.FILES.get('file')
        if not upload or upload.size > 5 * 1024 * 1024:
            messages.error(request, '请选择不超过 5 MB 的 CSV 文件。')
            return redirect('import_csv')
        try:
            text = upload.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or not {'书名', 'ISBN'}.issubset(reader.fieldnames):
                raise ValueError('CSV 至少需要“书名”和“ISBN”两列。')
            rows = list(reader)
            if len(rows) > 10000:
                raise ValueError('一次最多导入 10000 行。')
            with transaction.atomic():
                for number, row in enumerate(rows, start=2):
                    _import_row(row, number)
            messages.success(request, f'已导入 {len(rows)} 册图书。相同 ISBN 会新增实体副本。')
            return redirect('book_list')
        except (UnicodeError, csv.Error, ValueError) as exc:
            messages.error(request, f'导入失败：{exc}')
    return render(request, 'catalog/import.html', {'fields': CSV_FIELDS})


def _import_row(row, number):
    title = (row.get('书名') or '').strip()
    raw_isbn = (row.get('ISBN') or '').strip()
    try:
        isbn = normalize_isbn(raw_isbn) if raw_isbn else None
    except ValueError as exc:
        raise ValueError(f'第 {number} 行 ISBN 无效。') from exc
    if not title and not isbn:
        raise ValueError(f'第 {number} 行需要书名或 ISBN。')
    year_raw = (row.get('出版年') or '').strip()
    if year_raw and (not year_raw.isdigit() or not 1000 <= int(year_raw) <= 2100):
        raise ValueError(f'第 {number} 行出版年无效。')
    acquired_raw = (row.get('入藏日期') or '').strip()
    try:
        acquired = date.fromisoformat(acquired_raw) if acquired_raw else None
    except ValueError as exc:
        raise ValueError(f'第 {number} 行入藏日期需为 YYYY-MM-DD。') from exc
    status = (row.get('阅读状态') or Copy.Status.UNREAD).strip()
    if status not in Copy.Status.values:
        raise ValueError(f'第 {number} 行阅读状态无效。')
    category_name = (row.get('分类') or '').strip()
    category = Category.objects.get_or_create(name=category_name)[0] if category_name else None
    edition = Edition.objects.filter(isbn=isbn).first() if isbn else None
    if not edition:
        edition = Edition.objects.create(
            isbn=isbn, title=title[:300], author=(row.get('作者') or '').strip()[:300],
            publisher=(row.get('出版社') or '').strip()[:200],
            published_year=int(year_raw) if year_raw else None,
            cover_url=(row.get('封面链接') or '').strip()[:1000], category=category,
        )
        names = [(name.strip()) for name in (row.get('标签') or '').replace('，', '、').split('、') if name.strip()]
        _save_tags(edition, names[:12])
    Copy.objects.create(edition=edition, location=(row.get('存放位置') or '').strip()[:120],
                        status=status, acquired_on=acquired, notes=(row.get('备注') or '').strip())
