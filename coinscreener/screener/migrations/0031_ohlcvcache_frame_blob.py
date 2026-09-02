from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0030_condition_ichimoku_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='ohlcvcache',
            name='frame_blob',
            field=models.BinaryField(
                blank=True,
                editable=False,
                null=True,
                verbose_name='Compressed OHLCV DataFrame',
            ),
        ),
    ]

