# Generated by Django 5.0.7 on 2024-08-27 18:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("puzzle_editing", "0030_add_late_testsolve_flag"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hint",
            name="description",
            field=models.CharField(
                help_text='A description of when this hint should apply; e.g. "Solvers have done X and currently have..."',
                max_length=1000,
            ),
        ),
    ]
