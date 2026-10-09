"""The catalogue table, its full-text index and its trigram index."""

import uuid

import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        # `gin_trgm_ops` below is provided by pg_trgm, so the extension has to exist before the
        # index that uses it. Creating an extension needs a superuser connection; in this stack
        # the application role is the one the postgres image created, which is a superuser.
        TrigramExtension(),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('sku', models.CharField(max_length=64, unique=True, verbose_name='артикул')),
                ('name', models.CharField(max_length=300, verbose_name='наименование')),
                ('description', models.TextField(blank=True, verbose_name='описание')),
                ('category', models.CharField(db_index=True, max_length=120, verbose_name='категория')),
                ('price', models.DecimalField(decimal_places=2, max_digits=12, verbose_name='цена')),
                ('unit', models.CharField(default='шт', max_length=32, verbose_name='единица')),
                ('in_stock', models.BooleanField(db_index=True, default=True, verbose_name='в наличии')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='добавлен')),
                ('search_vector', django.contrib.postgres.search.SearchVectorField(editable=False, null=True)),
            ],
            options={
                'verbose_name': 'товар',
                'verbose_name_plural': 'товары',
                'db_table': 'catalog_product',
                'ordering': ['name'],
                'indexes': [django.contrib.postgres.indexes.GinIndex(fields=['search_vector'], name='catalog_product_fts'), django.contrib.postgres.indexes.GinIndex(fields=['name'], name='catalog_product_trgm', opclasses=['gin_trgm_ops'])],
            },
        ),
    ]
