from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0025_scanlease'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScanUsage',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('owner_key', models.CharField(db_index=True, max_length=64)),
                ('date', models.DateField(db_index=True)),
                ('scan_count', models.PositiveIntegerField(default=0)),
                ('reward_credits', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('owner_key', 'date'),
                        name='unique_daily_scan_usage',
                    ),
                ],
            },
        ),
    ]

