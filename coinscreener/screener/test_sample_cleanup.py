from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .models import Condition, Strategy
from .sample_cleanup import prune_public_samples


class PublicSampleCleanupTestCase(TestCase):
    def setUp(self):
        self.keep = Strategy.objects.create(name='월봉이', owner_key=None)
        self.public_null = Strategy.objects.create(name='일봉이', owner_key=None)
        self.public_blank = Strategy.objects.create(name='주봉이', owner_key='')
        self.private = Strategy.objects.create(
            name='나의 개인 전략', owner_key='private-owner'
        )
        self.private_same_name = Strategy.objects.create(
            name='일봉이', owner_key='another-owner'
        )
        Condition.objects.create(strategy=self.public_null)

    def test_only_other_public_samples_are_deleted(self):
        ids, deleted_count = prune_public_samples(Strategy)

        self.assertCountEqual(ids, [self.public_null.id, self.public_blank.id])
        self.assertGreaterEqual(deleted_count, 3)
        self.assertTrue(Strategy.objects.filter(id=self.keep.id).exists())
        self.assertTrue(Strategy.objects.filter(id=self.private.id).exists())
        self.assertTrue(
            Strategy.objects.filter(id=self.private_same_name.id).exists()
        )
        self.assertFalse(Condition.objects.filter(strategy=self.public_null).exists())

    def test_management_command_is_safe_to_run_repeatedly(self):
        output = StringIO()
        call_command('prune_sample_strategies', stdout=output)
        call_command('prune_sample_strategies', stdout=output)

        self.assertEqual(
            list(
                Strategy.objects.filter(owner_key__isnull=True)
                .values_list('name', flat=True)
            ),
            ['월봉이'],
        )
        self.assertIn('삭제 대상 전략=0개', output.getvalue())

