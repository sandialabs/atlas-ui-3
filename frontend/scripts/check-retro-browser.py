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
  const rgb = value => {
    const match = value.match(/rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)/);
    return match ? match.slice(1).map(Number) : null;
  };
  const lightness = c => c.reduce(
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
    const t = getComputedStyle(el('titlebar'));
    const a = getComputedStyle(button);
    const i = getComputedStyle(el('input'));
    const s = getComputedStyle(root);
    const checks = {
      body: contrast(rgb(b.backgroundColor), rgb(b.color)),
      window: contrast(rgb(w.backgroundColor), rgb(w.color)),
      title: contrast(rgb(t.backgroundColor), rgb(t.color)),
      button: contrast(rgb(a.backgroundColor), rgb(a.color)),
      input: contrast(rgb(i.backgroundColor), rgb(i.color)),
      focusButton: contrast(rgb(a.backgroundColor), rgb(a.outlineColor)),
      focusSurface: contrast(rgb(w.backgroundColor), rgb(a.outlineColor)),
    };
    const result = {
      id: item.id,
      mode: item.mode,
      actualMode: s.colorScheme,
      ratios: checks,
      focusWidth: a.outlineWidth,
      focusColor: a.outlineColor,
      surface: w.backgroundColor,
      font: b.fontFamily,
    };
    result.pass = result.actualMode === result.mode &&
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
    page = ('<!doctype html><html data-theme="dark" data-atlas-theme="dark"><head>'
            '<meta charset="utf-8"><link rel="stylesheet" href="'+
            html.escape(css_files[0].resolve().as_uri(), quote=True)+'"></head>'
            '<body><section id="window" class="atlas-retro-window">'
            '<div id="titlebar" class="atlas-retro-window-titlebar">Theme test</div>'
            '<div class="atlas-retro-window-content">'
            '<input id="input" class="atlas-retro-input" aria-label="Example">'
            '<button id="button" class="atlas-retro-button" type="button">Test</button>'
            '</div></section><pre id="results"></pre><script>'+script+'</script></body></html>')
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
        print('Chrome browser smoke: 55/55 themes passed 5 text contrasts >=4.5:1, 2 focus contrasts >=3:1, color mode, and focus width')
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
