"""Test zoomed-out Natura 2000 download."""
import tempfile, requests
from pathlib import Path
from app.core.downloads import download_natura2000
from PIL import Image

bbox = (188334.761, 426110.756, 188834.761, 426610.756)
s = requests.Session()
d = Path(tempfile.mkdtemp())
out = d / "natura2000.png"
download_natura2000(bbox, out, center=(188584.761, 426360.756), breed_radius=10000.0, px=1000, session=s)
img = Image.open(out)
print(f"Size: {img.size}, bytes: {out.stat().st_size}")
