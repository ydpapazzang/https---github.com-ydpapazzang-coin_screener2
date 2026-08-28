from django.db import migrations
from django.db.models import Q


def keep_only_wolbongi(apps, schema_editor):
    Strategy = apps.get_model('screener', 'Strategy')
    Strategy.objects.filter(
        Q(owner_key__isnull=True) | Q(owner_key='')
    ).exclude(name='월봉이').delete()


class Migration(migrations.Migration):
    dependencies = [('screener', '0028_paperposition')]
    operations = [
        migrations.RunPython(keep_only_wolbongi, migrations.RunPython.noop),
    ]

