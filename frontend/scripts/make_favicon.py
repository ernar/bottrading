"""Genera el favicon recortando SOLO la cabeza del logo.

Uso: python frontend/scripts/make_favicon.py
Lee  frontend/public/logo.png  y escribe  frontend/public/favicon.png (256x256).

El recorte por defecto está calibrado para el logo cuadrado original (la cabeza
del caballero a la izquierda). Si el encuadre no es exacto, ajusta CROP (en
fracciones 0..1 del lado del logo) y vuelve a ejecutar.
"""
from pathlib import Path
from PIL import Image

# Región de la cabeza como fracción del lado de la imagen (left, top, right, bottom).
CROP = (0.225, 0.39, 0.45, 0.61)
OUT_SIZE = 256

root = Path(__file__).resolve().parents[1] / "public"
src = root / "logo.png"
dst = root / "favicon.png"

img = Image.open(src).convert("RGBA")
w, h = img.size
box = (int(CROP[0] * w), int(CROP[1] * h), int(CROP[2] * w), int(CROP[3] * h))
head = img.crop(box)

# Encaja en un lienzo cuadrado transparente, centrado.
side = max(head.size)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(head, ((side - head.width) // 2, (side - head.height) // 2), head)
canvas.resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS).save(dst)
print(f"favicon escrito en {dst} (recorte {box})")
