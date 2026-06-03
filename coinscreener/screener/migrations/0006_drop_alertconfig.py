from django.db import migrations

def drop_alertconfig(apps, schema_editor):
    """Obsolete alertconfig table left over from previous iterations causes foreign key violations.
    Drop it with CASCADE on PostgreSQL, and normally on other engines (like SQLite)."""
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TABLE IF EXISTS "screener_alertconfig" CASCADE;')
    else:
        schema_editor.execute('DROP TABLE IF EXISTS "screener_alertconfig";')

class Migration(migrations.Migration):

    dependencies = [
        ('screener', '0005_remove_condition_bb_period_condition_offset_mode_and_more'),
    ]

    operations = [
        migrations.RunPython(drop_alertconfig, reverse_code=migrations.RunPython.noop),
    ]
