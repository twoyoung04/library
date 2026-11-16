from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def assign_to_wuming(apps, schema_editor):
    database = schema_editor.connection.alias
    owner_field = settings.AUTH_USER_MODEL.split('.')
    users = apps.get_model(*owner_field).objects.using(database)
    edition = apps.get_model('catalog', 'Edition').objects.using(database)
    tag = apps.get_model('catalog', 'Tag').objects.using(database)
    if not (edition.exists() or tag.exists()):
        return
    owner = users.filter(username='wuming', is_active=True).first()
    if owner is None:
        raise RuntimeError('Existing library data requires an active wuming account.')
    edition.filter(owner__isnull=True).update(owner=owner)
    tag.filter(owner__isnull=True).update(owner=owner)


def owner_field(nullable=False):
    return models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE,
                             null=nullable)


class Migration(migrations.Migration):
    dependencies = [('catalog', '0007_mobiletoken_mobilesaverequest'),
                    migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name='edition', name='owner', field=owner_field(True)),
        migrations.AddField(model_name='tag', name='owner', field=owner_field(True)),
        migrations.RunPython(assign_to_wuming, migrations.RunPython.noop),
        migrations.AlterField(model_name='edition', name='owner', field=owner_field()),
        migrations.AlterField(model_name='tag', name='owner', field=owner_field()),
        migrations.AlterField(model_name='edition', name='isbn',
                              field=models.CharField('ISBN', max_length=13, null=True, blank=True)),
        migrations.AlterField(model_name='tag', name='name', field=models.CharField('名称', max_length=80)),
        migrations.AddConstraint(model_name='edition', constraint=models.UniqueConstraint(
            fields=['owner', 'isbn'], name='unique_owner_isbn')),
        migrations.AddConstraint(model_name='tag', constraint=models.UniqueConstraint(
            fields=['owner', 'name'], name='unique_owner_tag')),
    ]
