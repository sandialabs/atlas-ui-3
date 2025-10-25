#!/usr/bin/env python3
"""
Download external dependencies for offline deployment.
This script downloads all CDN resources and fonts to serve locally.
"""

import os
import requests
import re
from pathlib import Path

def create_directories():
    """Create necessary directories for downloaded assets."""
    frontend_dir = Path(__file__).parent.parent / "frontend"
    vendor_dir = frontend_dir / "vendor"
    fonts_dir = frontend_dir / "fonts"
    
    vendor_dir.mkdir(exist_ok=True)
    fonts_dir.mkdir(exist_ok=True)
    
    return frontend_dir, vendor_dir, fonts_dir

def download_file(url, destination):
    """Download a file from URL to destination."""
    try:
        print(f"Downloading {url}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        with open(destination, 'wb') as f:
            f.write(response.content)
        print(f"✓ Downloaded to {destination}")
        return True
    except Exception as e:
        print(f"✗ Failed to download {url}: {e}")
        return False

def download_google_fonts():
    """Download Google Fonts and their CSS."""
    frontend_dir, vendor_dir, fonts_dir = create_directories()
    
    # Font URLs
    inter_url = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
    jetbrains_url = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap"
    
    # Download CSS files
    inter_css = requests.get(inter_url).text
    jetbrains_css = requests.get(jetbrains_url).text
    
    # Extract font file URLs and download them
    font_urls = re.findall(r'url\((https://[^)]+)\)', inter_css + jetbrains_css)
    
    for font_url in font_urls:
        font_name = font_url.split('/')[-1].split('?')[0]
        font_path = fonts_dir / font_name
        download_file(font_url, font_path)
        
        # Update CSS to use local paths
        inter_css = inter_css.replace(font_url, f"fonts/{font_name}")
        jetbrains_css = jetbrains_css.replace(font_url, f"fonts/{font_name}")
    
    # Save updated CSS files
    with open(vendor_dir / "inter.css", 'w') as f:
        f.write(inter_css)
    
    with open(vendor_dir / "jetbrains-mono.css", 'w') as f:
        f.write(jetbrains_css)
    
    print("✓ Google Fonts downloaded and localized")

def download_js_libraries():
    """Download JavaScript libraries from CDN."""
    frontend_dir, vendor_dir, fonts_dir = create_directories()
    
    libraries = [
        {
            "url": "https://cdn.jsdelivr.net/npm/marked@9.1.6/marked.min.js",
            "filename": "marked.min.js"
        },
        {
            "url": "https://cdn.jsdelivr.net/npm/dompurify@3.0.5/dist/purify.min.js", 
            "filename": "purify.min.js"
        }
    ]
    
    for lib in libraries:
        destination = vendor_dir / lib["filename"]
        download_file(lib["url"], destination)

def create_offline_index():
    """Create an offline version of index.html with local dependencies."""
    frontend_dir = Path(__file__).parent.parent / "frontend"
    index_path = frontend_dir / "index.html"
    offline_index_path = frontend_dir / "index-offline.html"
    
    with open(index_path, 'r') as f:
        content = f.read()
    
    # Replace Google Fonts with local versions
    content = content.replace(
        'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"',
        'href="vendor/inter.css"'
    )
    content = content.replace(
        'href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap"',
        'href="vendor/jetbrains-mono.css"'
    )
    
    # Replace CDN JavaScript with local versions
    content = content.replace(
        'src="https://cdn.jsdelivr.net/npm/marked@9.1.6/marked.min.js"',
        'src="vendor/marked.min.js"'
    )
    content = content.replace(
        'src="https://cdn.jsdelivr.net/npm/dompurify@3.0.5/dist/purify.min.js"',
        'src="vendor/purify.min.js"'
    )
    
    with open(offline_index_path, 'w') as f:
        f.write(content)
    
    print(f"✓ Created offline index.html at {offline_index_path}")

def main():
    """Main function to download all dependencies."""
    print("📦 Downloading dependencies for offline deployment...")
    
    try:
        download_google_fonts()
        download_js_libraries()
        create_offline_index()
        
        print("\n✅ All dependencies downloaded successfully!")
        print("📁 Files created:")
        print("  - frontend/vendor/ (JavaScript libraries)")
        print("  - frontend/fonts/ (Font files)")
        print("  - frontend/index-offline.html (Offline version)")
        print("\n💡 To use offline mode, copy index-offline.html to index.html")
        
    except Exception as e:
        print(f"\n❌ Error downloading dependencies: {e}")

if __name__ == "__main__":
    main()