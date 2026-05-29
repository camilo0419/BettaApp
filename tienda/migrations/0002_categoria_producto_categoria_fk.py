import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def unique_slug(model, nombre):
    base = slugify(nombre) or "categoria"
    slug = base
    i = 2
    while model.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


def migrate_categorias(apps, schema_editor):
    Categoria = apps.get_model("tienda", "Categoria")
    Producto = apps.get_model("tienda", "Producto")

    cache = {}
    for producto in Producto.objects.all().order_by("orden", "id"):
        nombre = (producto.categoria or "").strip() or "Sin categoria"
        key = nombre.lower()
        categoria = cache.get(key)
        if categoria is None:
            categoria = Categoria.objects.filter(nombre__iexact=nombre).first()
            if categoria is None:
                categoria = Categoria.objects.create(
                    nombre=nombre,
                    slug=unique_slug(Categoria, nombre),
                    orden=producto.orden,
                    activa=True,
                )
            cache[key] = categoria
        producto.categoria_tmp_id = categoria.id
        producto.save(update_fields=["categoria_tmp"])


class Migration(migrations.Migration):

    dependencies = [
        ("tienda", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Categoria",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(blank=True, max_length=140, unique=True)),
                ("orden", models.PositiveIntegerField(default=0)),
                ("activa", models.BooleanField(default=True)),
                ("creada", models.DateTimeField(auto_now_add=True)),
                ("actualizada", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Categoría",
                "verbose_name_plural": "Categorías",
                "ordering": ["orden", "nombre"],
            },
        ),
        migrations.AddField(
            model_name="producto",
            name="categoria_tmp",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tienda.categoria",
            ),
        ),
        migrations.RunPython(migrate_categorias, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="producto",
            name="categoria",
        ),
        migrations.RenameField(
            model_name="producto",
            old_name="categoria_tmp",
            new_name="categoria",
        ),
        migrations.AlterField(
            model_name="producto",
            name="categoria",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="productos",
                to="tienda.categoria",
            ),
        ),
        migrations.AlterField(
            model_name="producto",
            name="destacado",
            field=models.BooleanField(default=True, verbose_name="Destacado en home"),
        ),
    ]
