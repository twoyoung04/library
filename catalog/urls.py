from django.urls import path

from . import views

urlpatterns = [
    path('', views.book_list, name='book_list'),
    path('new/', views.book_new, name='book_new'),
    path('books/<int:pk>/', views.book_detail, name='book_detail'),
    path('books/<int:pk>/edit/', views.book_edit, name='book_edit'),
    path('copies/<int:pk>/edit/', views.copy_edit, name='copy_edit'),
    path('copies/<int:pk>/delete/', views.copy_delete, name='copy_delete'),
    path('categories/', views.categories, name='categories'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),
    path('api/lookup/', views.lookup_isbn, name='lookup_isbn'),
    path('api/scan-save/', views.scan_save, name='scan_save'),
    path('export/', views.export_csv, name='export_csv'),
    path('import/', views.import_csv, name='import_csv'),
]
