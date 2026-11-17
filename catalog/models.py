from django.db import models, router
from django.urls import reverse
from urllib.parse import urlparse
from django.conf import settings


class Tag(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField('名称', max_length=80)

    class Meta:
        ordering = ['name']
        verbose_name = '标签'
        verbose_name_plural = '标签'
        constraints = [models.UniqueConstraint(fields=['owner', 'name'], name='unique_owner_tag')]

    def __str__(self):
        return self.name


class PublicEdition(models.Model):
    isbn = models.CharField('ISBN-13', max_length=13, unique=True)
    title = models.CharField('书名', max_length=300)
    author = models.CharField('作者', max_length=300, blank=True)
    publisher = models.CharField('出版社', max_length=200, blank=True)
    published_year = models.PositiveSmallIntegerField('出版年', null=True, blank=True)
    cover_url = models.URLField('封面链接', max_length=1000, blank=True)
    source = models.CharField('书目来源', max_length=100)
    created_at = models.DateTimeField('收录时间', auto_now_add=True)
    collected_fields = models.JSONField('采集字段来源', default=dict, blank=True, editable=False)

    def __str__(self):
        return self.title


class BibliographyImport(models.Model):
    snapshot = models.CharField('快照', max_length=100, unique=True)
    offset = models.PositiveBigIntegerField('读取位置', default=0)
    counts = models.JSONField('导入统计', default=dict, blank=True)
    updated_at = models.DateTimeField('最后进度', auto_now=True)
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)


class BibliographyConflict(models.Model):
    public_edition = models.ForeignKey(PublicEdition, on_delete=models.CASCADE)
    source = models.CharField('来源', max_length=100)
    source_key = models.CharField('来源记录编号', max_length=100)
    snapshot = models.CharField('快照', max_length=100)
    candidate = models.JSONField('待复核书目')
    updated_at = models.DateTimeField('最后发现时间', auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['public_edition', 'source', 'source_key'],
                                    name='unique_bibliography_conflict'),
        ]


class BibliographyLookup(models.Model):
    isbn = models.CharField('ISBN-13', max_length=13, primary_key=True)
    retry_after = models.DateTimeField('下次可查询时间')


class Edition(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    public_edition = models.ForeignKey(PublicEdition, on_delete=models.SET_NULL, null=True,
                                       editable=False, related_name='library_editions')
    isbn = models.CharField('ISBN', max_length=13, null=True, blank=True)
    title = models.CharField('书名', max_length=300, blank=True)
    author = models.CharField('作者', max_length=300, blank=True)
    publisher = models.CharField('出版社', max_length=200, blank=True)
    published_year = models.PositiveSmallIntegerField('出版年', null=True, blank=True)
    cover_url = models.URLField('封面链接', max_length=1000, blank=True)
    cover_object_key = models.CharField(max_length=300, blank=True, editable=False)
    tags = models.ManyToManyField(Tag, blank=True, related_name='editions', verbose_name='标签')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    def save(self, *args, update_fields=None, **kwargs):
        if update_fields is None or {'isbn', 'public_edition'}.intersection(update_fields):
            if self.isbn:
                database = kwargs.get('using') or router.db_for_write(Edition, instance=self)
                self.public_edition_id = PublicEdition.objects.using(database).filter(
                    isbn=self.isbn).values_list('pk', flat=True).first()
            else:
                self.public_edition_id = None
            if update_fields is not None:
                update_fields = set(update_fields) | {'public_edition'}
        super().save(*args, update_fields=update_fields, **kwargs)

    @property
    def cover_display_url(self):
        from django.conf import settings
        from .covers import current_key, public_url
        if current_key(self):
            return public_url(self.cover_object_key)
        return '' if settings.COS_ENABLED else self.cover_url

    class Meta:
        ordering = ['title', 'id']
        verbose_name = '图书版本'
        verbose_name_plural = '图书版本'
        constraints = [models.UniqueConstraint(fields=['owner', 'isbn'], name='unique_owner_isbn')]

    def __str__(self):
        return self.title or self.isbn or '未命名图书'

    @property
    def display_cover_url(self):
        host = urlparse(self.cover_url).hostname or ''
        if host == 'asset.doubanio.com' or (host.startswith('img') and host.endswith('.doubanio.com')):
            return reverse('edition_cover_proxy', args=[self.pk])
        return self.cover_url


class Copy(models.Model):
    class Status(models.TextChoices):
        UNREAD = 'unread', '未读'
        READING = 'reading', '在读'
        READ = 'read', '已读'
        LOANED = 'loaned', '借出'

    edition = models.ForeignKey(Edition, on_delete=models.CASCADE,
                                related_name='copies', verbose_name='图书版本')
    location = models.CharField('存放位置', max_length=120, blank=True)
    status = models.CharField('阅读状态', max_length=12, choices=Status.choices,
                              default=Status.UNREAD)
    acquired_on = models.DateField('入藏日期', null=True, blank=True)
    notes = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('录入时间', auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = '馆藏副本'
        verbose_name_plural = '馆藏副本'

    def __str__(self):
        return f'{self.edition} #{self.pk}'


class MobileToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()


class MobileSaveRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    request_id = models.UUIDField()
    copy = models.ForeignKey(Copy, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'request_id'], name='unique_mobile_save_request')]
