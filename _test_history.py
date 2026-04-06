"""Test web search history function."""
import sys
sys.path.insert(0, ".")
from app.main import _web_search_history

result = _web_search_history("Julianaweg 22, Megchelen, gemeente Oude IJsselstreek, provincie Gelderland")
print("=== TEXT ===")
print(result["text"][:1000])
print("\n=== IMAGE URLs ===")
for url in result["image_urls"]:
    print(f"  {url}")
