from django.contrib import admin

from .models import Category, Copy, Edition, Tag


class CopyInline(admin.TabularInline):
    model = Copy
    extra = 0


@admin.register(Edition)
class EditionAdmin(admin.ModelAdmin):
    list_display = ['title', 'author', 'isbn', 'category', 'copy_count']
    list_filter = ['category', 'tags']
    search_fields = ['title', 'author', 'isbn', 'publisher']
    inlines = [CopyInline]

    @admin.display(description='册数')
    def copy_count(self, obj):
        return obj.copies.count()


admin.site.register(Category)
admin.site.register(Tag)
