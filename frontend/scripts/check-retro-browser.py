#!/usr/bin/env python3
"""Browser-check all Atlas Retro Design System themes against the built frontend CSS.

Requires an existing `npm run build` output and local Chrome/Chromium.
No app server, network access, screenshots, fonts, or binary downloads.
Run: python3 frontend/scripts/check-retro-browser.py --chrome /path/to/chrome
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
MANIFEST = FRONTEND / 'src/themes/vendor/retro-design-system/manifest.json'
LEGACY_IDS = {1: 'mac7', 2: 'win95', 9: 'crt-phosphor'}

SCRIPT = r"""
window.onload = () => {
  const entries = __ENTRIES__;
  const results = [];
  const el = id => document.getElementById(id);
  const rgba = value => {
    const match = value.match(/^rgba?\(\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)$/);
    if (!match) throw new Error('Unsupported computed color: '+value);
    return [...match.slice(1, 4).map(Number), match[4] === undefined ? 1 : Number(match[4])];
  };
  const over = (front, back) => {
    const alpha = front[3] + back[3] * (1-front[3]);
    return [...front.slice(0, 3).map((v, i) => alpha === 0 ? 0 :
      (v * front[3] + back[i] * back[3] * (1-front[3])) / alpha), alpha];
  };
  const background = node => {
    if (!node) return [255, 255, 255, 1]; // opaque browser canvas
    const color = rgba(getComputedStyle(node).backgroundColor);
    return color[3] === 1 ? color : over(color, background(node.parentElement));
  };
  const textContrast = node => {
    const bg = background(node);
    return contrast(bg, over(rgba(getComputedStyle(node).color), bg));
  };
  // Guard against the old alpha-blind parser: transparent black is not black.
  if (over(rgba('rgba(0, 0, 0, 0)'), [255, 255, 255, 1])[0] !== 255 ||
      over(rgba('rgba(0, 0, 0, 0.5)'), [255, 255, 255, 1])[0] !== 127.5) {
    throw new Error('Alpha compositing regression');
  }
  const lightness = c => c.slice(0, 3).reduce(
    (sum, v, i) => {
      const x = v / 255;
      return sum + [0.2126, 0.7152, 0.0722][i] *
        (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
    }, 0,
  );
  const contrast = (a, b) => {
    if (!a || !b) return 0;
    const x = lightness(a), y = lightness(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  for (const item of entries) {
    const root = document.documentElement;
    root.setAttribute('data-theme', item.mode);
    root.setAttribute('data-atlas-theme', item.id);
    const button = el('button');
    button.focus();
    const b = getComputedStyle(document.body);
    const w = getComputedStyle(el('window'));
    const a = getComputedStyle(button);
    const s = getComputedStyle(root);
    const checks = {
      body: textContrast(document.body),
      window: textContrast(el('window')),
      title: textContrast(el('titlebar')),
      button: textContrast(button),
      input: textContrast(el('input')),
      focusButton: contrast(background(button), rgba(a.outlineColor)),
      focusSurface: contrast(background(el('window')), rgba(a.outlineColor)),
    };
    for (const node of document.querySelectorAll('[data-content-check]')) {
      checks[node.id] = textContrast(node);
    }
    const badgePairs = {};
    for (const node of document.querySelectorAll('[data-status-check]')) {
      const style = getComputedStyle(node);
      badgePairs[node.id] = [style.backgroundColor, style.color];
    }
    const invariantBadges = results.length === 0 ||
      JSON.stringify(badgePairs) === JSON.stringify(results[0].badgePairs);
    const result = {
      badgePairs,
      invariantBadges,
      id: item.id,
      mode: item.mode,
      actualMode: s.colorScheme,
      ratios: checks,
      focusWidth: a.outlineWidth,
      focusColor: a.outlineColor,
      surface: w.backgroundColor,
      font: b.fontFamily,
    };
    result.pass = invariantBadges && result.actualMode === result.mode &&
      Object.entries(checks).every(([key, ratio]) =>
        ratio >= (key.startsWith('focus') ? 3 : 4.5)) &&
      result.focusWidth === '3px';
    results.push(result);
  }
  el('results').textContent = JSON.stringify(results);
  document.body.setAttribute('data-retro-check', results.every(r => r.pass) ? 'PASS' : 'FAIL');
};
"""


def expected_themes():
    # Declared mode is derived from generated build-time registry metadata,
    # rather than reimplementing the generator's mode-selection heuristics.
    source = (FRONTEND/'src/themes/retroCatalog.generated.js').read_text()
    catalog = re.search(r'\(\s*(\[.*?\])\.map\(', source, re.S)
    if not catalog:
        raise RuntimeError('Could not read generated registry')
    entries = json.loads(catalog.group(1))
    manifest = json.loads(MANIFEST.read_text())
    if len(entries) != 53 or manifest['count'] != 53:
        raise RuntimeError('Unexpected upstream catalog size')
    for item, entry in zip(manifest['systems'], entries):
        if entry['reference'] != item['slug']:
            raise RuntimeError('Out-of-order catalog: '+item['slug'])
        expected_id = LEGACY_IDS.get(item['id'], item['slug'][3:])
        if entry['id'] != expected_id:
            raise RuntimeError('Incorrect ID for '+item['slug'])
    return [{'id': 'dark', 'mode': 'dark'}, {'id': 'light', 'mode': 'light'}] + [
        {'id': item['id'], 'mode': item['mode']} for item in entries
    ]


def run(chrome, timeout):
    css_files = list((FRONTEND/'dist/assets').glob('index-*.css'))
    if len(css_files) != 1:
        raise RuntimeError('Run npm run build before checking browser themes')
    entries = expected_themes()
    assert len(entries) == 55
    script = SCRIPT.replace('__ENTRIES__', json.dumps(entries, separators=(',', ':')))
    # Use production CSS, including its hover rules. The additional selector
    # permits deterministic hovered-state checks in headless --dump-dom.
    css = css_files[0].read_text().replace(':hover', ':is(:hover, [data-contrast-hover])')
    badges = ''.join(
        f'<span id="badge-{color}" class="atlas-status-{color}" '
        f'data-content-check data-status-check>{color}</span>'
        for color in ('red', 'yellow', 'green', 'neutral', 'orange', 'blue'))
    syntax_extras = ''.join(
        f'<span id="syntax-extra-{name}" class="hljs-{name}" data-content-check>{name}</span>'
        for name in ('built_in', 'attribute', 'operator', 'subst', 'addition', 'deletion',
                     'selector-attr', 'selector-class', 'selector-id', 'code', 'formula',
                     'selector-pseudo', 'template-tag', 'params', 'punctuation'))
    page = ('<!doctype html><html data-theme="dark" data-atlas-theme="dark"><head>'
            '<meta charset="utf-8"><style>'+css+'</style></head>'
            '<body><section id="window" class="atlas-retro-window">'
            '<div id="titlebar" class="atlas-retro-window-titlebar">Theme test</div>'
            '<div class="atlas-retro-window-content">'
            '<input id="input" class="atlas-retro-input" aria-label="Example">'
            '<button id="button" class="atlas-retro-button" type="button">Test</button>'
            '</div></section>'
            # Message.jsx assistant markup, with transparent prose/link backgrounds.
            '<section class="bg-gray-800"><div class="prose prose-invert selectable-markdown">'
            '<p><a id="assistant-link" href="#" data-content-check>Assistant link</a>'
            '<a id="assistant-link-hover" href="#" data-contrast-hover data-content-check>Hovered link</a></p>'
            '<div class="code-block-container"><pre><code class="hljs">'
            '<span id="syntax-keyword" class="hljs-keyword" data-content-check>const</span>'
            '<span id="syntax-string" class="hljs-string" data-content-check>hello</span>'
            '<span id="syntax-number" class="hljs-number" data-content-check>42</span>'
            '<span id="syntax-comment" class="hljs-comment" data-content-check>comment</span>'
            +syntax_extras+'</code></pre></div></div>'+badges+'</section>'
            '<header class="bg-gray-800">'
            '<button id="header-control" class="bg-gray-700 text-gray-200" data-content-check>Header</button>'
            '<button id="header-accent" class="bg-gray-700 atlas-header-accent" data-content-check>Help</button>'
            '<button id="header-accent-hover" class="bg-gray-700 hover:bg-gray-600 atlas-header-accent" '
            'data-contrast-hover data-content-check>Help hovered</button>'
            '<div class="bg-gray-800">'
            '<button id="header-menu" class="atlas-header-menu-accent hover:bg-gray-700" data-content-check>Transcript</button>'
            '<button id="header-menu-hover" class="atlas-header-menu-accent hover:bg-gray-700" '
            'data-contrast-hover data-content-check>Transcript hovered</button></div></header>'
            '<div class="user-message-bubble"><span id="user-message" class="text-gray-200 whitespace-pre-wrap break-words" data-content-check>User message</span>'
            '<span id="user-caption" class="text-gray-300" data-content-check>You</span></div>'
            '<button id="suggestion-hover" class="bg-gray-800 hover:bg-gray-700 text-gray-300 hover:text-gray-50" '
            'data-contrast-hover data-content-check>Follow up</button>'
            '<button id="source-disabled" disabled class="bg-green-700 disabled:bg-gray-600 text-white disabled:text-gray-50" '
            'data-content-check>Select all</button>'
            '<pre id="results"></pre><script>'+script+'</script></body></html>')
    with tempfile.TemporaryDirectory(prefix='atlas-retro-check-') as tmp:
        file = Path(tmp)/'index.html'
        file.write_text(page)
        proc = subprocess.run([chrome, '--headless=new', '--no-sandbox',
                               '--disable-dev-shm-usage', '--disable-gpu',
                               '--allow-file-access-from-files', '--dump-dom',
                               file.as_uri()], capture_output=True, text=True,
                              timeout=timeout, check=False)
        if proc.returncode != 0:
            raise RuntimeError('Chrome exited '+str(proc.returncode)+': '+proc.stderr[-800:])
        marker = re.search(r'data-retro-check="(PASS|FAIL)"', proc.stdout)
        output = re.search(r'<pre id="results">(.*?)</pre>', proc.stdout, re.S)
        if not marker or not output:
            raise RuntimeError('Chrome did not finish the page self-test')
        rows = json.loads(html.unescape(output.group(1)))
        if len(rows) != 55:
            raise RuntimeError('Expected 55 browser tests, got '+str(len(rows)))
        failures = [row for row in rows if not row['pass']]
        for row in failures:
            print('FAIL', json.dumps(row, ensure_ascii=False))
        if marker.group(1) != 'PASS' or failures:
            raise RuntimeError(str(len(failures))+' theme(s) failed browser checks')
        print('Chrome browser smoke: 55/55 themes passed chrome + real content contrasts >=4.5:1, invariant status pairs, 2 focus contrasts >=3:1, color mode, and focus width')
        return rows


if __name__ == '__main__':
    argp = argparse.ArgumentParser(description=__doc__)
    argp.add_argument('--chrome', help='Path to Chrome/Chromium browser')
    argp.add_argument('--timeout', type=int, default=45)
    args = argp.parse_args()
    chrome = args.chrome or next(
        (path for name in ('google-chrome', 'chromium', 'chromium-browser', 'chrome')
         if (path := shutil.which(name))), None)
    if not chrome:
        sys.exit('Please supply --chrome /absolute/path/to/chrome')
    try:
        run(chrome, args.timeout)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit('Retro browser verification failed: '+str(error))
