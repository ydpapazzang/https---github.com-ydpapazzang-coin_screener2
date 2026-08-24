from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class OperationalConfigurationTestCase(SimpleTestCase):
    def test_existing_auto_field_type_is_explicit(self):
        self.assertEqual(
            settings.DEFAULT_AUTO_FIELD,
            'django.db.models.AutoField',
        )

    def test_danta_and_swing_timer_slots_do_not_overlap(self):
        systemd_dir = Path(settings.BASE_DIR) / 'deploy' / 'systemd'
        daily = (
            systemd_dir / 'coinscreener-daily-picks.timer'
        ).read_text(encoding='utf-8')
        swing = (
            systemd_dir / 'coinscreener-swing-picks.timer'
        ).read_text(encoding='utf-8')

        def calendar_lines(content):
            return {
                line.strip()
                for line in content.splitlines()
                if line.startswith('OnCalendar=')
            }

        self.assertTrue(calendar_lines(daily))
        self.assertTrue(calendar_lines(swing))
        self.assertTrue(
            calendar_lines(daily).isdisjoint(calendar_lines(swing))
        )

    def test_operations_document_describes_current_runtime(self):
        document = (
            Path(settings.BASE_DIR) / 'PROJECT_STRUCTURE.md'
        ).read_text(encoding='utf-8')

        for expected in (
            'Gunicorn',
            'SQLite',
            'WAL',
            'coinscreener-backup.timer',
            'coinscreener-daily-picks.timer',
            'coinscreener-swing-picks.timer',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, document)
