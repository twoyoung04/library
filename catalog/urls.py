from django.urls import path

from . import mobile_api, views

urlpatterns = [
    path('', views.book_list, name='book_list'),
    path('books/<int:pk>/cover/', views.book_cover, name='book_cover'),
    path('new/', views.book_new, name='book_new'),
    path('books/<int:pk>/', views.book_detail, name='book_detail'),
    path('covers/<int:pk>/', views.edition_cover_proxy, name='edition_cover_proxy'),
    path('books/<int:pk>/edit/', views.book_edit, name='book_edit'),
    path('copies/<int:pk>/edit/', views.copy_edit, name='copy_edit'),
    path('copies/<int:pk>/delete/', views.copy_delete, name='copy_delete'),
    path('tags/', views.tags, name='tags'),
    path('tags/<int:pk>/delete/', views.tag_delete, name='tag_delete'),
    path('categories/', views.legacy_categories, name='categories'),
    path('api/lookup/', views.lookup_isbn, name='lookup_isbn'),
    path('api/scan-save/', views.scan_save, name='scan_save'),
    path('api/mobile/metadata/', mobile_api.public_book_metadata),
    path('api/mobile/snapshot/', mobile_api.library_snapshot),
    path('api/mobile/login/', mobile_api.login),
    path('api/mobile/logout/', mobile_api.logout),
    path('api/mobile/me/', mobile_api.me),
    path('api/mobile/books/', mobile_api.books),
    path('api/mobile/books/<int:pk>/', mobile_api.book_detail),
    path('api/mobile/books/<int:pk>/edit/', mobile_api.update_book),
    path('api/mobile/copies/', mobile_api.create_copy),
    path('api/mobile/copies/<int:pk>/', mobile_api.copy_detail),
    path('api/mobile/filters/', mobile_api.filters),
    path('api/mobile/lookup/', mobile_api.lookup),
    path('export/', views.export_csv, name='export_csv'),
    path('import/', views.import_csv, name='import_csv'),
]
