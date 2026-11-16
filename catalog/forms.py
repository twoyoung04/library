import re

from django import forms

from .isbn import normalize_isbn
from .models import Copy, Edition, Tag


def parse_tag_names(value):
    names = list(dict.fromkeys(name.strip() for name in re.split(r'[,，、\n]', value or '') if name.strip()))
    if any(len(name) > 80 for name in names):
        raise ValueError('每个标签最多 80 字。')
    return names


class EditionForm(forms.ModelForm):
    title = forms.CharField(label='书名', max_length=300)
    tags_text = forms.CharField(label='标签', required=False,
                                help_text='用逗号或换行分隔，可添加任意数量；新标签会自动创建。',
                                widget=forms.Textarea(attrs={'rows': 2}))

    class Meta:
        model = Edition
        fields = ['isbn', 'title', 'author', 'publisher', 'published_year', 'cover_url']
        labels = {'isbn': 'ISBN / 期刊条码'}
        widgets = {
            'isbn': forms.TextInput(attrs={'inputmode': 'text', 'autocomplete': 'off'}),
            'published_year': forms.NumberInput(attrs={'min': '1000', 'max': '2100'}),
        }

    def clean_isbn(self):
        value = self.cleaned_data.get('isbn')
        if not value:
            return None
        try:
            isbn = normalize_isbn(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        if (self.instance.owner_id and Edition.objects.filter(owner_id=self.instance.owner_id, isbn=isbn)
                .exclude(pk=self.instance.pk).exists()):
            raise forms.ValidationError('这个 ISBN 已属于另一条图书记录。')
        return isbn

    def clean_tags_text(self):
        try:
            return parse_tag_names(self.cleaned_data['tags_text'])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class CopyForm(forms.ModelForm):
    class Meta:
        model = Copy
        fields = ['location', 'status', 'acquired_on', 'notes']
        widgets = {
            'acquired_on': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ['name']

    def clean_name(self):
        name = self.cleaned_data['name']
        if (self.instance.owner_id and Tag.objects.filter(owner_id=self.instance.owner_id, name=name)
                .exclude(pk=self.instance.pk).exists()):
            raise forms.ValidationError('这个标签已存在。')
        return name
