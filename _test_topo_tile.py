import sys, os
sys.path.insert(0, os.getcwd())
from app.main import _search_wikimedia_images, _WIKIMEDIA_UA
import requests

results = _search_wikimedia_images("Megchelen, gemeente Oude IJsselstreek, provincie Gelderland")
print(f"Found {len(results)} images")
for img in results:
    print(f"\n  Title: {img['title']}")
    print(f"  URL: {img['thumb_url'][:80]}...")
    r = requests.get(img["thumb_url"], timeout=15, headers={"User-Agent": _WIKIMEDIA_UA})
    print(f"  Download: status={r.status_code}, size={len(r.content)}, ct={r.headers.get('Content-Type', '?')}")
