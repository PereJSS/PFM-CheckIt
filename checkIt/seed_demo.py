"""
Script de datos de demostración para CheckIt.
Uso: python3 manage.py shell < seed_demo.py
  o: python3 seed_demo.py  (desde el directorio checkIt con el entorno activado)
"""

import os
import io
import hashlib
import django
import requests

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.core.files.base import ContentFile
from django.conf import settings
from PIL import Image, ImageDraw
from users.models import User
from propiedades.models import Propiedad
from inspecciones.models import Inspeccion, Evidencia

# ── Limpiar datos anteriores ────────────────────────────────────────────────
Evidencia.objects.all().delete()
Inspeccion.objects.all().delete()
Propiedad.objects.all().delete()
User.objects.filter(username__in=["admin_demo", "operario1", "operario2"]).delete()
print("Datos anteriores eliminados.")

# ── Usuarios ────────────────────────────────────────────────────────────────
admin = User.objects.create_user(
    username="admin_demo",
    password="checkIt2026!",
    first_name="Administrador",
    last_name="CheckIt",
    email="admin@checkit.local",
    role=User.Role.ADMIN,
)
op1 = User.objects.create_user(
    username="operario1",
    password="checkIt2026!",
    first_name="Carlos",
    last_name="Martínez",
    email="carlos@checkit.local",
    role=User.Role.OPERARIO,
)
op2 = User.objects.create_user(
    username="operario2",
    password="checkIt2026!",
    first_name="Lucía",
    last_name="Fernández",
    email="lucia@checkit.local",
    role=User.Role.OPERARIO,
)
print(f"Usuarios creados: {admin}, {op1}, {op2}")

# ── Propiedades ─────────────────────────────────────────────────────────────
p1 = Propiedad.objects.create(
    nombre="Apartamento Mar Azul",
    direccion="Calle del Mar, 14 — Alicante",
    descripcion="Apartamento de 2 habitaciones con vistas al mar.",
)
p2 = Propiedad.objects.create(
    nombre="Villa Las Palmeras",
    direccion="Avda. de las Palmeras, 3 — Benidorm",
    descripcion="Villa con piscina privada y jardín.",
)
p3 = Propiedad.objects.create(
    nombre="Estudio Centro Histórico",
    direccion="Calle Mayor, 8, 3º A — Valencia",
    descripcion="Estudio reformado en pleno centro histórico.",
)
print(f"Propiedades creadas: {p1.nombre}, {p2.nombre}, {p3.nombre}")

# ── Inspecciones ─────────────────────────────────────────────────────────────
i1 = Inspeccion.objects.create(propiedad=p1, operario=op1, estado=Inspeccion.Estado.PENDIENTE)
i2 = Inspeccion.objects.create(propiedad=p2, operario=op1, estado=Inspeccion.Estado.EN_PROGRESO)
i3 = Inspeccion.objects.create(propiedad=p3, operario=op2, estado=Inspeccion.Estado.COMPLETADA)
i4 = Inspeccion.objects.create(propiedad=p1, operario=op2, estado=Inspeccion.Estado.PENDIENTE)
print(f"Inspecciones creadas: #{i1.pk}, #{i2.pk}, #{i3.pk}, #{i4.pk}")

# ── Generador de imágenes de prueba ──────────────────────────────────────────
DAÑOS = [
    # (color_fondo, color_mancha, etiqueta, sala)
    ((240, 220, 200), (160,  60,  60), "Mancha en la pared",      "salon"),
    ((200, 215, 230), ( 80,  80, 180), "Grieta en el techo",      "cocina"),
    ((220, 235, 220), ( 50, 120,  50), "Humedad en esquina",      "bano"),
    ((245, 235, 210), (190, 130,  30), "Golpe en puerta",         "dormitorio1"),
    ((210, 225, 240), (100, 150, 200), "Ventana rota",            "dormitorio2"),
    ((230, 230, 230), (130,  60, 160), "Baldosa levantada",       "terraza"),
]

def _crear_imagen(etiqueta: str, bg: tuple, mancha: tuple) -> bytes:
    """Genera un JPEG 640×480 simulando una fotografía de daño."""
    img = Image.new("RGB", (640, 480), color=bg)
    draw = ImageDraw.Draw(img)

    # Simular mancha/daño con elipse semitransparente
    overlay = Image.new("RGB", (640, 480), color=bg)
    od = ImageDraw.Draw(overlay)
    od.ellipse([220, 140, 420, 320], fill=mancha)
    img = Image.blend(img, overlay, alpha=0.55)
    draw = ImageDraw.Draw(img)

    # Bordes decorativos
    draw.rectangle([10, 10, 630, 470], outline=(180, 180, 180), width=3)

    # Texto descriptivo
    draw.rectangle([0, 390, 640, 480], fill=(30, 30, 30))
    draw.text((20, 400), f"CheckIt · Evidencia de daño", fill=(255, 255, 255))
    draw.text((20, 425), etiqueta, fill=(255, 220, 80))
    draw.text((20, 450), "Uso exclusivo pericial — no modificar", fill=(160, 160, 160))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()

# ── Evidencias ───────────────────────────────────────────────────────────────
# Inspección #2 (EN_PROGRESO) → 3 evidencias
# Inspección #3 (COMPLETADA)  → 3 evidencias
evidencias_spec = [
    (i2, DAÑOS[0]),
    (i2, DAÑOS[1]),
    (i2, DAÑOS[2]),
    (i3, DAÑOS[3]),
    (i3, DAÑOS[4]),
    (i3, DAÑOS[5]),
]

for inspeccion, (bg, mancha, etiqueta, sala) in evidencias_spec:
    jpg_bytes = _crear_imagen(etiqueta, bg, mancha)
    sha256 = hashlib.sha256(jpg_bytes).hexdigest()
    nombre_archivo = f"demo_{inspeccion.pk}_{sala}.jpg"

    ev = Evidencia(
        inspeccion=inspeccion,
        descripcion=etiqueta,
        hash_sha256=sha256,
    )
    ev.foto.save(nombre_archivo, ContentFile(jpg_bytes), save=True)
    print(f"  Evidencia '{etiqueta}' → {nombre_archivo} (sha256: {sha256[:12]}…)")

print()
print("=== Credenciales de acceso ===")
print("  Admin    → usuario: admin_demo  / contraseña: checkIt2026!")
print("  Operario → usuario: operario1   / contraseña: checkIt2026!")
print("  Operario → usuario: operario2   / contraseña: checkIt2026!")
