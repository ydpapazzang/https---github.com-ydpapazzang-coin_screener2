from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0024_dailyrecommendation_last_checked_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScanLease',
            fields=[
                ('owner_key', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('token', models.CharField(max_length=32)),
                ('acquired_at', models.DateTimeField()),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
        ),
    ]

