from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('screener', '0032_intradayobservation')]

    operations = [
        migrations.AddField(
            model_name='intradayobservation',
            name='highest_price',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='intradayobservation',
            name='lowest_price',
            field=models.FloatField(blank=True, null=True),
        ),
    ]

