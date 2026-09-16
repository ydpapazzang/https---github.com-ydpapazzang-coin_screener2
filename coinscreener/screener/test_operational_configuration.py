from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class OperationalConfigurationTestCase(SimpleTestCase):
    def test_existing_auto_field_type_is_explicit(self):
        self.assertEqual(
            settings.DEFAULT_AUTO_FIELD,
            'django.db.models.AutoField',
        )

    def test_swing_timer_slot_is_configured(self):
        systemd_dir = Path(settings.BASE_DIR) / 'deploy' / 'systemd'
        swing = (
            systemd_dir / 'coinscreener-swing-picks.timer'
        ).read_text(encoding='utf-8')

        def calendar_lines(content):
            return {
                line.strip()
                for line in content.splitlines()
                if line.startswith('OnCalendar=')
            }

        self.assertTrue(calendar_lines(swing))

    def test_operations_document_describes_current_runtime(self):
        document = (
            Path(settings.BASE_DIR) / 'PROJECT_STRUCTURE.md'
        ).read_text(encoding='utf-8')

        for expected in (
            'Gunicorn',
            'SQLite',
            'WAL',
            'coinscreener-backup.timer',
            'coinscreener-swing-picks.timer',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, document)
