from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('screener', '0027_dailyrecommendation_version_snapshot')]
    operations = [
        migrations.CreateModel(
            name='PaperPosition',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('owner_key', models.CharField(db_index=True, max_length=64)),
                ('trade_type', models.CharField(choices=[('danta', '단타'), ('swing', '스윙')], db_index=True, max_length=10)),
                ('coin_ticker', models.CharField(db_index=True, max_length=50)),
                ('coin_name', models.CharField(max_length=100)),
                ('entry_price', models.FloatField(verbose_name='실제 모의 체결가')),
                ('invested_amount', models.FloatField(verbose_name='투입 금액')),
                ('target_price', models.FloatField(verbose_name='목표가')),
                ('stop_loss', models.FloatField(verbose_name='손절가')),
                ('current_price', models.FloatField(blank=True, null=True)),
                ('highest_price', models.FloatField(blank=True, null=True)),
                ('lowest_price', models.FloatField(blank=True, null=True)),
                ('status', models.CharField(choices=[('open', '보유 중'), ('closed', '청산 완료')], db_index=True, default='open', max_length=10)),
                ('exit_price', models.FloatField(blank=True, null=True)),
                ('exit_reason', models.CharField(blank=True, max_length=20)),
                ('opened_at', models.DateTimeField(auto_now_add=True)),
                ('exit_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('recommendation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='paper_positions', to='screener.dailyrecommendation')),
            ],
            options={'ordering': ['-opened_at']},
        ),
        migrations.AddConstraint(
            model_name='paperposition',
            constraint=models.UniqueConstraint(fields=('owner_key', 'recommendation'), name='unique_session_paper_recommendation'),
        ),
    ]

