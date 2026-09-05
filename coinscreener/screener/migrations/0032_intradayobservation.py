from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('screener', '0031_ohlcvcache_frame_blob')]

    operations = [
        migrations.CreateModel(
            name='IntradayObservation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('detected_at', models.DateTimeField(db_index=True, verbose_name='신호 시각')),
                ('ticker', models.CharField(db_index=True, max_length=50, verbose_name='티커')),
                ('name', models.CharField(blank=True, max_length=100, verbose_name='종목명')),
                ('entry_price', models.FloatField(verbose_name='관찰 진입가')),
                ('target_1_price', models.FloatField(verbose_name='1차 목표가')),
                ('target_2_price', models.FloatField(verbose_name='2차 목표가')),
                ('stop_loss', models.FloatField(verbose_name='초기 손절가')),
                ('status', models.CharField(choices=[('open', '관찰중'), ('target_1', '1차 목표 도달'), ('target_2', '2차 목표 도달'), ('stopped', '손절'), ('expired', '시간 만료')], default='open', max_length=20)),
                ('reason', models.TextField(blank=True, verbose_name='신호 근거')),
                ('strategy_version', models.CharField(db_index=True, max_length=64)),
                ('last_checked_at', models.DateTimeField(blank=True, null=True)),
                ('exit_price', models.FloatField(blank=True, null=True)),
                ('result_pct', models.FloatField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-detected_at', '-id']},
        ),
        migrations.AddIndex(model_name='intradayobservation', index=models.Index(fields=['status', 'detected_at'], name='screener_in_status_33cc1c_idx')),
        migrations.AddIndex(model_name='intradayobservation', index=models.Index(fields=['ticker', 'strategy_version'], name='screener_in_ticker_15d35c_idx')),
    ]

