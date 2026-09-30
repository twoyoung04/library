from django.db import models


class Category(models.Model):
    name = models.CharField('名称', max_length=80, unique=True)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='children', verbose_name='上级分类')

    class Meta:
        ordering = ['name']
        verbose_name = '分类'
        verbose_name_plural = '分类'

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField('名称', max_length=50, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name = '标签'
        verbose_name_plural = '标签'

    def __str__(self):
        return self.name


class Edition(models.Model):
    isbn = models.CharField('ISBN', max_length=13, unique=True, null=True, blank=True)
    title = models.CharField('书名', max_length=300, blank=True)
    author = models.CharField('作者', max_length=300, blank=True)
    publisher = models.CharField('出版社', max_length=200, blank=True)
    published_year = models.PositiveSmallIntegerField('出版年', null=True, blank=True)
    cover_url = models.URLField('封面链接', max_length=1000, blank=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='editions', verbose_name='分类')
    tags = models.ManyToManyField(Tag, blank=True, related_name='editions', verbose_name='标签')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        ordering = ['title', 'id']
        verbose_name = '图书版本'
        verbose_name_plural = '图书版本'

    def __str__(self):
        return self.title or self.isbn or '未命名图书'


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
