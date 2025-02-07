# Generated by Django 5.0.1 on 2024-02-06 20:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("puzzle_editing", "0018_discord_cache"),
    ]

    operations = [
        migrations.AlterField(
            model_name="supportrequest",
            name="team",
            field=models.CharField(
                choices=[
                    ("ART", "🎨 Art"),
                    ("ACC", "🔎 Accessibility"),
                    ("OPS", "🚧 Operations"),
                    ("TECH", "👩🏽\u200d💻 Tech"),
                ],
                max_length=4,
            ),
        ),
    ]
