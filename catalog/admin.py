from django.contrib import admin

from .models import Copy, Edition, Tag


class CopyInline(admin.TabularInline):
    model = Copy
    extra = 0


class LibraryOwnerAdmin(admin.ModelAdmin):
    exclude = ['owner']

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser else queryset.filter(owner=request.user)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.owner = request.user
        super().save_model(request, obj, form, change)


@admin.register(Edition)
class EditionAdmin(LibraryOwnerAdmin):
    exclude = ['owner', 'tags']
    list_display = ['title', 'author', 'isbn', 'copy_count']
    search_fields = ['title', 'author', 'isbn', 'publisher']
    inlines = [CopyInline]

    def get_list_filter(self, request):
        return ['owner'] if request.user.is_superuser else []

    @admin.display(description='册数')
    def copy_count(self, obj):
        return obj.copies.count()


@admin.register(Tag)
class TagAdmin(LibraryOwnerAdmin):
    list_display = ['name', 'owner']
