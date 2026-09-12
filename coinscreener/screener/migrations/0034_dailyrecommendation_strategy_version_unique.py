from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('screener', '0033_intradayobservation_tracking_prices')]

    operations = [
        migrations.AlterUniqueTogether(
            name='dailyrecommendation',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='dailyrecommendation',
            constraint=models.UniqueConstraint(
                fields=('date', 'coin_ticker', 'trade_type', 'strategy_version'),
                name='unique_daily_recommendation_strategy_version',
            ),
        ),
    ]
