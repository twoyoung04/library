import re

from django import forms

from .isbn import normalize_isbn
from .models import Category, Copy, Edition


class EditionForm(forms.ModelForm):
    title = forms.CharField(label='书名', max_length=300)
    tags_text = forms.CharField(label='标签', required=False,
                                help_text='多个标签用逗号隔开', max_length=400)

    class Meta:
        model = Edition
        fields = ['isbn', 'title', 'author', 'publisher', 'published_year',
                  'cover_url', 'category']
        widgets = {
            'isbn': forms.TextInput(attrs={'inputmode': 'text', 'autocomplete': 'off'}),
            'published_year': forms.NumberInput(attrs={'min': '1000', 'max': '2100'}),
        }

    def clean_isbn(self):
        value = self.cleaned_data.get('isbn')
        if not value:
            return None
        try:
            return normalize_isbn(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_tags_text(self):
        value = self.cleaned_data['tags_text']
        names = [name.strip() for name in re.split(r'[,，、]', value) if name.strip()]
        if len(names) > 12 or any(len(name) > 50 for name in names):
            raise forms.ValidationError('最多 12 个标签，每个不超过 50 字')
        return names


class CopyForm(forms.ModelForm):
    class Meta:
        model = Copy
        fields = ['location', 'status', 'acquired_on', 'notes']
        widgets = {
            'acquired_on': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'parent']

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if self.instance.pk and parent and (parent.pk == self.instance.pk or parent.parent_id == self.instance.pk):
            raise forms.ValidationError('不能把分类放到自身或子分类下面')
        return parent
