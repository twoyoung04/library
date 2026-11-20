from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def public_page(request, page):
    return render(request, f'public/{page}.html', {
        'support_email': settings.LIBRARY_SUPPORT_EMAIL,
        'operator_name': settings.LIBRARY_OPERATOR_NAME,
    })
