#!/usr/bin/env python3
import os
import requests

# Create vendor directory
os.makedirs("frontend/vendor", exist_ok=True)

# Download JS libraries
files = [
    ("https://cdn.jsdelivr.net/npm/marked@9.1.6/marked.min.js", "frontend/vendor/marked.min.js"),
    ("https://cdn.jsdelivr.net/npm/dompurify@3.0.5/dist/purify.min.js", "frontend/vendor/purify.min.js")
]

for url, path in files:
    print(f"Downloading {url}")
    with open(path, 'wb') as f:
        f.write(requests.get(url).content)
    print(f"Saved to {path}")

print("Done!")