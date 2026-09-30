from django.shortcuts import redirect
from django.urls import reverse
from django.views.csrf import csrf_failure as default_csrf_failure


def csrf_failure(request, reason=''):
    # Login rotates the CSRF token. A duplicate submission from the old form
    # must not turn a successful login into an error page.
    if request.path == reverse('login') and request.user.is_authenticated:
        return redirect('book_list')
    return default_csrf_failure(request, reason=reason)
