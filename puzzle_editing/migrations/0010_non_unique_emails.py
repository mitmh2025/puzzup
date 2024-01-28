# Generated by Django 4.2.9 on 2024-01-28 22:55

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("puzzle_editing", "0009_discord_only_login"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(
                blank=True, max_length=254, verbose_name="email address"
            ),
        ),
    ]
