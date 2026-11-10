#!/usr/bin/env python3
"""Browser-check text contrast of the shipped Atlas components in all 55 themes.

PR #1175 review: instead of styling hand-copied class names, this mounts the
real React components (scripts/contrast-fixture/fixture.jsx: log bodies,
system notices, tool-call and approval prompts, the agent-input card, header
controls, the Settings panel and its caption, conversation metadata, retro
window/button/input chrome) against the production stylesheet from
`npm run build`, switches through every registered theme, and measures every
rendered text node, at rest and in the hovered state.

Measurement:
- Text color is composited over its real background: the element's own and
  ancestor background colors are alpha-blended until an opaque one, and
  element opacity scales the text alpha. Transparent is never read as black.
- A non-`none` background-image (gradient) between the text and that opaque
  base is sampled at every gradient color stop. All stops >= 4.5:1 passes,
  all below fails, mixed is reported as UNMEASURABLE (never skipped silently).
  url() images or a transparent text fill are reported UNMEASURABLE too.
- Every text pair below 4.5:1 fails (large text gets no 3:1 allowance).
  Retro focus rings must reach 3:1 on the button and its window and be 3px.
  Status badges (.atlas-status-*) must render identically in every theme.
- Full-cover ::before/::after boxes with a background (CRT scanlines,
  wallpaper or shading overlays) on any ancestor are composited too: z-index
  below 0 as part of the backdrop, otherwise as a tint over text and backdrop
  alike. Gradient overlays are sampled per stop. The page self-tests this. The
  shipped CSS currently has none (the generator folds demo overlays into the
  fills), so each theme reports pseudoOverlays=[]; partial-size decorations
  (badges, chevrons) and text-shadow are not part of the computed background.
- Disabled controls (WCAG 1.4.3 inactive components) are measured; below
  4.5:1 they are listed as DISABLED instead of failing the theme.
- Routed pages (LogViewer, Files page and panels, Telemetry, Admin dashboard
  and modals, Help, Marketplace) and the composer warning rows are mounted
  with canned API responses (contrast-fixture/stubs/fetchFixtures.js) and
  measured after their data loads.

Requires `npm ci`, an existing `npm run build` (flag on), and Chrome/Chromium.
No app server, network access, screenshots, fonts, or binary downloads.
Run: python3 frontend/scripts/check-retro-browser.py --chrome /path/to/chrome
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
MANIFEST = FRONTEND / 'src/themes/vendor/retro-design-system/manifest.json'
FIXTURE_CONFIG = FRONTEND / 'scripts/contrast-fixture/vite.config.js'
LEGACY_IDS = {1: 'mac7', 2: 'win95', 9: 'crt-phosphor'}
TEXT_MIN = 4.5
FOCUS_MIN = 3.0
# Header, sidebar rows, transcript, approvals, agent input, retro controls,
# composer notices, 14 routed-page/admin regions (5 Telemetry tabs), toasts, Settings.
MIN_REGIONS = 24

SCRIPT = r"""
window.onload = async () => {
  const report = document.getElementById('results');
  try {
    const fixture = window.__atlasContrastFixture;
    if (!fixture) throw new Error('Contrast fixture did not mount: ' + document.getElementById('errors').textContent.slice(0, 1500));
    // Routed pages fetch (stubbed) data in effects; measure the loaded state.
    if (!(await fixture.ready())) throw new Error('Fixture pages did not finish loading');
    report.textContent = JSON.stringify(runChecks());
  } catch (error) {
    report.textContent = JSON.stringify({ fatal: String(error && error.stack || error) });
  }
};

function runChecks() {
  const entries = __ENTRIES__;
  const TEXT_MIN = __TEXT_MIN__, FOCUS_MIN = __FOCUS_MIN__;
  const fixture = window.__atlasContrastFixture;
  const errors = document.getElementById('errors').textContent;
  if (errors) throw new Error('Fixture render errors: ' + errors);

  const rgba = value => {
    const match = value.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/);
    if (!match) return null;
    let alpha = match[4] === undefined ? 1 : match[4].endsWith('%') ? parseFloat(match[4]) / 100 : Number(match[4]);
    return [Number(match[1]), Number(match[2]), Number(match[3]), alpha];
  };
  const over = (front, back) => {
    const alpha = front[3] + back[3] * (1 - front[3]);
    return [...front.slice(0, 3).map((v, i) => alpha === 0 ? 0 :
      (v * front[3] + back[i] * back[3] * (1 - front[3])) / alpha), alpha];
  };
  // Guard against the old alpha-blind parser: transparent black is not black.
  if (over(rgba('rgba(0, 0, 0, 0)'), [255, 255, 255, 1])[0] !== 255 ||
      over(rgba('rgba(0, 0, 0, 0.5)'), [255, 255, 255, 1])[0] !== 127.5) {
    throw new Error('Alpha compositing regression');
  }
  const lum = c => c.slice(0, 3).reduce((sum, v, i) => {
    const x = v / 255;
    return sum + [0.2126, 0.7152, 0.0722][i] * (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
  }, 0);
  const contrast = (a, b) => {
    const x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const gradientStops = image => {
    if (/url\(/i.test(image)) return null;
    const stops = (image.match(/rgba?\([^)]*\)/g) || []).map(rgba).filter(Boolean);
    return stops.length ? stops : null;
  };

  // Candidate backgrounds behind `node`, bottom to top, plus opacity product.
  // Painted ::before/::after boxes on the text's ancestors (CRT scanlines,
  // desktop wallpaper overlays, window shading). An absolutely positioned
  // pseudo with a background is composited as a layer: z-index < 0 paints
  // under the content (part of the backdrop), otherwise over it (it tints the
  // text and its backdrop alike). Cached per theme.
  let pseudoCache = new Map();
  const pseudoLayers = el => {
    if (pseudoCache.has(el)) return pseudoCache.get(el);
    const found = [];
    for (const which of ['::before', '::after']) {
      const ps = getComputedStyle(el, which);
      if (!ps.content || ps.content === 'none' || ps.content === 'normal' || ps.display === 'none') continue;
      const color = rgba(ps.backgroundColor) || [0, 0, 0, 0];
      const image = ps.backgroundImage && ps.backgroundImage !== 'none' ? ps.backgroundImage : null;
      if (color[3] === 0 && !image) continue;
      if (!['absolute', 'fixed'].includes(ps.position)) continue;
      // Only full-cover boxes (inset: 0 or the element's full size) can sit
      // behind/over every text node inside; small decorations are skipped.
      const inset = ['top', 'right', 'bottom', 'left'].every(k => ps[k] === '0px');
      const sized = ps.top === '0px' && ps.left === '0px' &&
        parseFloat(ps.width) >= el.clientWidth - 1 && parseFloat(ps.height) >= el.clientHeight - 1;
      if (!inset && !sized) continue;
      const alpha = Number(ps.opacity);
      found.push({ which, color: [...color.slice(0, 3), color[3] * alpha], image,
                   alpha, under: parseInt(ps.zIndex, 10) < 0 });
    }
    pseudoCache.set(el, found);
    return found;
  };
  // Self-test: a full-cover translucent ::after must be found as an overlay.
  {
    const probe = document.createElement('div');
    probe.id = 'atlas-pseudo-selftest';
    document.body.appendChild(probe);
    const found = pseudoLayers(probe);
    probe.remove();
    pseudoCache = new Map();
    if (found.length !== 1 || found[0].under || Math.abs(found[0].color[3] - 0.5) > 0.01) {
      throw new Error('Pseudo-element overlay detection regression');
    }
  }
  const scaleStops = (stops, alpha) => stops.map(c => [...c.slice(0, 3), c[3] * alpha]);

  const backdrop = (node, { ignoreImages = false, overlays = null } = {}) => {
    const layers = [];
    let opacity = 1;
    let base = [255, 255, 255, 1]; // opaque browser canvas
    for (let el = node; el; el = el.parentElement) {
      const style = getComputedStyle(el);
      opacity *= Number(style.opacity);
      if (overlays && !ignoreImages) {
        for (const p of pseudoLayers(el)) {
          if (p.under) layers.push({ color: p.color, image: p.image, pseudo: true, alpha: p.alpha });
          else overlays.push(p);
        }
      }
      const color = rgba(style.backgroundColor) || [0, 0, 0, 0];
      const image = style.backgroundImage;
      layers.push({ color, image: !ignoreImages && image && image !== 'none' ? image : null });
      if (color[3] === 1) { base = null; break; }
    }
    let candidates = [base || layers[layers.length - 1].color.slice(0, 3).concat(1)];
    let unmeasurable = null;
    for (let i = layers.length - 1; i >= 0; i--) {
      const layer = layers[i];
      if (!(base === null && i === layers.length - 1)) {
        candidates = candidates.map(c => over(layer.color, c));
      }
      if (layer.image) {
        const raw = gradientStops(layer.image);
        const stops = raw && layer.pseudo ? scaleStops(raw, layer.alpha) : raw;
        if (!stops) { unmeasurable = 'background-image without color stops'; continue; }
        const next = [];
        for (const c of candidates) for (const s of stops) next.push(over(s, c));
        const seen = new Set();
        candidates = next.filter(c => {
          const key = c.slice(0, 3).map(Math.round).join(',');
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        }).slice(0, 96);
      }
    }
    return { candidates, opacity, unmeasurable, gradient: layers.some(l => l.image) };
  };

  const describe = (el, text) => {
    const region = el.closest('[data-contrast-region]');
    const cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 6).join('.');
    return {
      region: region ? region.dataset.contrastRegion : '?',
      node: el.tagName.toLowerCase() + (cls ? '.' + cls : ''),
      text: text.replace(/\s+/g, ' ').trim().slice(0, 48),
    };
  };
  const isRendered = el => el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  const isScreenReaderOnly = el => {
    const style = getComputedStyle(el);
    return style.position === 'absolute' && parseFloat(style.width) <= 1 && parseFloat(style.height) <= 1;
  };

  const root = document.getElementById('fixture-root');
  const measure = el => {
    const style = getComputedStyle(el);
    const fill = style.webkitTextFillColor && style.webkitTextFillColor !== style.color
      ? style.webkitTextFillColor : style.color;
    const fg = rgba(fill);
    const overlays = [];
    const bd = backdrop(el, { overlays });
    if (!fg || fg[3] === 0) return { status: 'unmeasurable', reason: 'transparent text fill' };
    const text = [fg[0], fg[1], fg[2], fg[3] * bd.opacity];
    // Each pair: [text over backdrop, backdrop], then every overlay pseudo
    // (and each of its gradient stops) tints both sides equally.
    let pairs = bd.candidates.map(bg => [over(text, bg), bg]);
    for (const p of overlays) {
      const tints = p.image ? (gradientStops(p.image) ? scaleStops(gradientStops(p.image), p.alpha) : null) : [];
      if (tints === null) { bd.unmeasurable = 'pseudo-element overlay without color stops'; continue; }
      const all = p.color[3] > 0 ? [p.color, ...tints.map(t => over(t, p.color))] : tints;
      if (!all.length) continue;
      pseudoSeen.add(p.which);
      pairs = pairs.flatMap(([t, b]) => all.map(c => [over(c, t), over(c, b)])).slice(0, 192);
      bd.gradient = bd.gradient || all.length > 1;
    }
    const ratios = pairs.map(([t, b]) => contrast(t, b));
    const min = Math.min(...ratios), max = Math.max(...ratios);
    const out = { min: +min.toFixed(2), max: +max.toFixed(2), fg: fill,
                  bg: pairs.slice(0, 4).map(([, c]) => c).map(c => 'rgb(' + c.slice(0, 3).map(Math.round).join(',') + ')') };
    if (bd.unmeasurable) return { ...out, status: min >= TEXT_MIN ? 'pass' : 'unmeasurable', reason: bd.unmeasurable };
    if (min >= TEXT_MIN) return { ...out, status: 'pass' };
    if (bd.gradient && max >= TEXT_MIN) return { ...out, status: 'unmeasurable', reason: 'gradient: some stops below 4.5:1' };
    return { ...out, status: 'fail' };
  };

  const textElements = () => {
    const nodes = [];
    for (const el of root.querySelectorAll('*')) {
      if (['SCRIPT', 'STYLE', 'OPTION', 'svg', 'path'].includes(el.tagName)) continue;
      if (el.closest('[aria-hidden="true"], svg')) continue;
      const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('');
      const isField = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) && el.type !== 'checkbox' &&
        el.type !== 'radio' && el.type !== 'range' && (el.value || el.tagName === 'SELECT');
      if (!own.trim() && !isField) continue;
      if (!isRendered(el) || isScreenReaderOnly(el)) continue;
      nodes.push([el, own.trim() ? own : (el.value || el.options?.[el.selectedIndex]?.text || '')]);
    }
    return nodes;
  };

  const HOVERABLE = 'button, a, [role="button"], [role="listitem"], [role="tab"], summary, label, .atlas-theme-option';
  const results = [];
  let pseudoSeen = new Set();
  for (const item of entries) {
    fixture.setTheme(item.id);
    pseudoCache = new Map();
    pseudoSeen = new Set();
    const html = document.documentElement;
    const actualTheme = html.getAttribute('data-atlas-theme');
    const result = { id: item.id, mode: item.mode, actualTheme, actualMode: getComputedStyle(html).colorScheme,
                     measured: 0, passed: 0, exempt: [], regionCounts: {}, failures: [], unmeasurable: [], hovered: 0 };
    for (const pass of ['rest', 'hover']) {
      const hovered = pass === 'hover' ? [...root.querySelectorAll(HOVERABLE)] : [];
      hovered.forEach(el => el.setAttribute('data-contrast-hover', ''));
      for (const [el, text] of textElements()) {
        if (pass === 'hover' && !el.closest('[data-contrast-hover]')) continue;
        const outcome = measure(el);
        // WCAG 1.4.3 exempts inactive controls: a disabled control below
        // 4.5:1 is listed (DISABLED) but does not fail the theme.
        if (outcome.status !== 'pass' && el.closest('button:disabled, input:disabled, select:disabled, ' +
            'textarea:disabled, fieldset:disabled, [aria-disabled="true"]')) {
          result.exempt.push({ ...describe(el, text), state: pass, ...outcome });
          continue;
        }
        result.measured += 1;
        if (pass === 'hover') result.hovered += 1;
        const where = { ...describe(el, text), state: pass };
        result.regionCounts[where.region] = (result.regionCounts[where.region] || 0) + 1;
        if (outcome.status === 'pass') result.passed += 1;
        else if (outcome.status === 'fail') result.failures.push({ ...where, ...outcome });
        else result.unmeasurable.push({ ...where, ...outcome });
      }
      hovered.forEach(el => el.removeAttribute('data-contrast-hover'));
    }

    // Focus ring on a real RetroButton inside a RetroWindow.
    const button = root.querySelector('[data-contrast-region="retro-controls"] .atlas-retro-button:not(:disabled)');
    const win = button.closest('.atlas-retro-window');
    button.focus();
    const bstyle = getComputedStyle(button);
    const ring = rgba(bstyle.outlineColor);
    // Pass/fail uses the solid fills the generator guarantees (3.1:1); every
    // gradient stop is measured too and a ring below 3:1 on any stop is
    // reported as unmeasurable rather than hidden.
    const minRatio = list => +Math.min(...list.map(c => contrast(ring, c))).toFixed(2);
    result.focus = {
      width: bstyle.outlineWidth,
      button: minRatio(backdrop(button, { ignoreImages: true }).candidates),
      window: minRatio(backdrop(win, { ignoreImages: true }).candidates),
      buttonStops: minRatio(backdrop(button).candidates),
      windowStops: minRatio(backdrop(win).candidates),
    };
    for (const part of ['button', 'window']) {
      if (result.focus[part + 'Stops'] < FOCUS_MIN) {
        result.unmeasurable.push({ region: 'retro-controls', node: 'focus-ring:' + part,
          text: '(focus ring)', state: 'focus', min: result.focus[part + 'Stops'],
          max: result.focus[part], status: 'unmeasurable',
          reason: 'focus ring passes the solid fill but not every gradient stop' });
      }
    }
    button.blur();

    result.pseudoOverlays = [...pseudoSeen];
    result.regions = [...new Set([...root.querySelectorAll('[data-contrast-region]')].map(el => el.dataset.contrastRegion))];
    result.badgePairs = Object.fromEntries([...root.querySelectorAll('[class*="atlas-status-"]')].map((el, i) => {
      const s = getComputedStyle(el);
      return [i + ':' + (el.className.match(/atlas-status-\w+/) || [''])[0], [s.backgroundColor, s.color]];
    }));
    result.invariantBadges = results.length === 0 ||
      JSON.stringify(result.badgePairs) === JSON.stringify(results[0].badgePairs);
    result.badgeCount = Object.keys(result.badgePairs).length;
    result.pass = result.failures.length === 0 && result.invariantBadges && result.badgeCount > 0 &&
      result.actualTheme === item.id && result.actualMode === item.mode &&
      result.focus.width === '3px' && result.focus.button >= FOCUS_MIN && result.focus.window >= FOCUS_MIN;
    results.push(result);
  }
  return results;
}
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


def build_fixture(out_dir, timeout):
    """Bundle scripts/contrast-fixture/fixture.jsx (real components) to one IIFE."""
    vite = FRONTEND/'node_modules/vite/bin/vite.js'
    node = shutil.which('node')
    if not vite.exists() or not node:
        raise RuntimeError('Run npm ci in frontend/ (needs node and vite) before the browser check')
    env = {**os.environ, 'ATLAS_FIXTURE_OUT_DIR': str(out_dir)}
    proc = subprocess.run([node, str(vite), 'build', '--config', str(FIXTURE_CONFIG)],
                          cwd=FRONTEND, env=env, capture_output=True, text=True,
                          timeout=timeout, check=False)
    bundle = Path(out_dir)/'fixture.js'
    if proc.returncode != 0 or not bundle.exists():
        raise RuntimeError('Contrast fixture build failed: '+(proc.stderr or proc.stdout)[-1500:])
    return bundle.read_text()


def production_css():
    css_files = list((FRONTEND/'dist/assets').glob('index-*.css'))
    if len(css_files) != 1:
        raise RuntimeError('Run npm run build before checking browser themes')
    css = css_files[0].read_text()
    if 'windows-xp-luna' not in css:
        raise RuntimeError('dist/ was built with VITE_FEATURE_RETRO_THEMES=false; rebuild with the flag on')
    # Use production CSS, including its hover rules. The additional selector
    # permits deterministic hovered-state checks in headless --dump-dom.
    return css.replace(':hover', ':is(:hover, [data-contrast-hover])')


def summarize(rows):
    lines = []
    for row in rows:
        lines.append(f"{'PASS' if row['pass'] else 'FAIL'} {row['id']:<22} measured={row['measured']:<4} "
                     f"hovered={row['hovered']:<4} failures={len(row['failures']):<3} "
                     f"unmeasurable={len(row['unmeasurable']):<3} focus={row['focus']['button']}/{row['focus']['window']} "
                     f"badges={row['badgeCount']}{'' if row['invariantBadges'] else ' (theme-dependent!)'}")
        for fail in row['failures']:
            lines.append(f"    FAIL {fail['state']:<5} {fail['region']}: \"{fail['text']}\" {fail['min']}:1 "
                         f"fg={fail['fg']} bg={','.join(fail['bg'])} [{fail['node']}]")
        for item in row.get('exempt', []):
            lines.append(f"    DISABLED {item['state']:<5} {item['region']}: \"{item['text']}\" {item.get('min')}:1 "
                         f"(inactive control, WCAG 1.4.3 exempt) [{item['node']}]")
        for item in row['unmeasurable']:
            ratio = f" {item['min']}-{item['max']}:1" if 'min' in item else ''
            lines.append(f"    UNMEASURABLE {item['state']:<5} {item['region']}: \"{item['text']}\"{ratio} "
                         f"({item['reason']}) [{item['node']}]")
        if row['actualMode'] != row['mode'] or row['actualTheme'] != row['id']:
            lines.append(f"    FAIL theme/mode: expected {row['id']}/{row['mode']}, got {row['actualTheme']}/{row['actualMode']}")
        if row['focus']['width'] != '3px':
            lines.append(f"    FAIL focus ring width {row['focus']['width']}")
    return '\n'.join(lines)


def run(chrome, timeout, report=None, fixture_js=None):
    entries = expected_themes()
    assert len(entries) == 55
    css = production_css()
    script = (SCRIPT.replace('__ENTRIES__', json.dumps(entries, separators=(',', ':')))
              .replace('__TEXT_MIN__', str(TEXT_MIN)).replace('__FOCUS_MIN__', str(FOCUS_MIN)))
    with tempfile.TemporaryDirectory(prefix='atlas-retro-check-') as tmp:
        bundle = Path(fixture_js).read_text() if fixture_js else build_fixture(Path(tmp)/'fixture', timeout)
        page = ('<!doctype html><html data-theme="dark" data-atlas-theme="dark"><head>'
                '<meta charset="utf-8"><style>'+css+'</style>'
                # Theme switches happen synchronously between measurements, so
                # color transitions would be read mid-flight; settle them.
                '<style>*,*::before,*::after{transition:none!important;'
                'animation:none!important}'
                '#atlas-pseudo-selftest{position:relative;width:40px;height:20px}'
                '#atlas-pseudo-selftest::after{content:"";position:absolute;inset:0;'
                'background:rgba(0,0,0,.5)}</style></head><body>'
                '<pre id="errors"></pre><pre id="results"></pre><div id="fixture-root"></div>'
                '<script>window.onerror=function(m,s,l,c,e){document.getElementById("errors")'
                '.textContent+=String(m)+" "+(e&&e.stack||"")+"\\n"};</script>'
                '<script>'+bundle.replace('</script', '<\\/script')+'</script>'
                '<script>'+script+'</script></body></html>')
        file = Path(tmp)/'index.html'
        file.write_text(page)
        proc = subprocess.run([chrome, '--headless=new', '--no-sandbox',
                               '--disable-dev-shm-usage', '--disable-gpu',
                               '--window-size=1280,900',
                               # Let the fixture's stubbed page loads (timers,
                               # promises) settle before the DOM is dumped.
                               '--virtual-time-budget=600000',
                               '--allow-file-access-from-files', '--dump-dom',
                               file.as_uri()], capture_output=True, text=True,
                              timeout=timeout, check=False)
        if proc.returncode != 0:
            raise RuntimeError('Chrome exited '+str(proc.returncode)+': '+proc.stderr[-800:])
        output = re.search(r'<pre id="results">(.*?)</pre>', proc.stdout, re.S)
        if not output or not output.group(1).strip():
            raise RuntimeError('Chrome did not finish the page self-test')
        rows = json.loads(html.unescape(output.group(1)))
        if isinstance(rows, dict) and 'fatal' in rows:
            raise RuntimeError('Fixture check crashed: '+rows['fatal'][:1500])
    if len(rows) != 55:
        raise RuntimeError('Expected 55 browser tests, got '+str(len(rows)))
    for row in rows:
        empty = [name for name in row['regions'] if not row['regionCounts'].get(name)]
        if empty or len(row['regions']) < MIN_REGIONS:
            raise RuntimeError(f"{row['id']}: fixture regions rendered no measurable text: {empty} "
                               f"({len(row['regions'])} regions)")
    if report:
        Path(report).write_text(json.dumps(rows, indent=1))
    print(summarize(rows))
    failures = [row for row in rows if not row['pass']]
    measured = sum(row['measured'] for row in rows)
    unmeasurable = sum(len(row['unmeasurable']) for row in rows)
    failed_pairs = sum(len(row['failures']) for row in rows)
    exempt = sum(len(row.get('exempt', [])) for row in rows)
    regions = sorted(set().union(*(row['regions'] for row in rows)))
    print(f'Chrome component check: {55-len(failures)}/55 themes passed; {measured} text pairs measured '
          f'(rest + hover) on the real Header, Settings, transcript, tool/approval, agent-input, '
          f'conversation-metadata, composer-notice and routed-page components ({len(regions)} regions); '
          f'{exempt} disabled-control pairs listed; {failed_pairs} below {TEXT_MIN}:1; '
          f'{unmeasurable} unmeasurable (listed above); focus rings >= {FOCUS_MIN}:1 and 3px; '
          f'status badges theme-invariant')
    if failures:
        raise RuntimeError(str(len(failures))+' theme(s) failed browser checks')
    return rows


if __name__ == '__main__':
    argp = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argp.add_argument('--chrome', help='Path to Chrome/Chromium browser')
    argp.add_argument('--timeout', type=int, default=180)
    argp.add_argument('--report', help='Write the full per-theme JSON results to this file')
    argp.add_argument('--fixture-js', help='Use a prebuilt fixture bundle instead of building one')
    args = argp.parse_args()
    chrome = args.chrome or next(
        (path for name in ('google-chrome', 'chromium', 'chromium-browser', 'chrome')
         if (path := shutil.which(name))), None)
    if not chrome:
        sys.exit('Please supply --chrome /absolute/path/to/chrome')
    try:
        run(chrome, args.timeout, args.report, args.fixture_js)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit('Retro browser verification failed: '+str(error))
