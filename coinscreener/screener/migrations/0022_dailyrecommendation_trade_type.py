from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0021_alertsetting_last_run_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='dailyrecommendation',
            name='trade_type',
            field=models.CharField(
                choices=[('danta', '단타'), ('swing', '스윙')],
                db_index=True,
                default='danta',
                max_length=10,
                verbose_name='매매 유형',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='dailyrecommendation',
            unique_together={('date', 'coin_ticker', 'trade_type')},
        ),
        migrations.AlterModelOptions(
            name='dailyrecommendation',
            options={'ordering': ['-date', 'trade_type', 'coin_ticker']},
        ),
    ]
