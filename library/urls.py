from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from .public_pages import public_page

urlpatterns = [
    path('privacy/', public_page, {'page': 'privacy'}, name='privacy'),
    path('support/', public_page, {'page': 'support'}, name='support'),
    path('terms/', public_page, {'page': 'terms'}, name='terms'),
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('catalog.urls')),
]
