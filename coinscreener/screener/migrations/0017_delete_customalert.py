# 커스텀 알림 기능 철회: CustomAlert 모델 제거
# (0016에서 생성한 테이블을 되돌린다. 0016이 아직 적용되지 않았다면 no-op에 가깝게 동작)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0016_customalert'),
    ]

    operations = [
        migrations.DeleteModel(
            name='CustomAlert',
        ),
    ]
