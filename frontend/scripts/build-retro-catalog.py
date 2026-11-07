#!/usr/bin/env python3
"""Rebuild scoped Retro Design System catalog from pinned, vendored MIT sources.

Only static tokens + manifest metadata are imported. Demo HTML/JS, user assets,
fonts and ANSI files never enter the frontend. Run with --check in CI.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'themes'
VENDOR = ROOT / 'vendor' / 'retro-design-system'
SHA = '8988f50a514e7f845dbe5ad190faa2afd926f0b4'
LEGACY_IDS = {1: 'mac7', 2: 'win95', 9: 'crt-phosphor'}
SOURCE_PATTERN = re.compile(r'\A(/\*[^*]*(?:\*(?!/)[^*]*)*\*/\s*)?:root\s*\{([^{}]*)\}\s*\Z', re.S)
TOKEN_PATTERN = re.compile(r'--([\w-]+)\s*:\s*([^;]+);')
HEX_PATTERN = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


def rgb(value):
    if not isinstance(value, str) or not HEX_PATTERN.fullmatch(value.strip()):
        return None
    digits = value.strip().lstrip('#').lower()
    if len(digits) == 3:
        digits = ''.join(c * 2 for c in digits)
    return tuple(int(digits[i:i+2], 16) for i in (0, 2, 4))


def hexcolor(color):
    return '#%02x%02x%02x' % tuple(color)


def blend(a, b, t):
    return tuple(round(x * (1-t) + y * t) for x, y in zip(a, b))


def luminance(color):
    vals = []
    for v in color:
        v /= 255
        vals.append(v/12.92 if v <= 0.04045 else ((v+0.055)/1.055)**2.4)
    return .2126*vals[0] + .7152*vals[1] + .0722*vals[2]


def contrast(a, b):
    first, second = sorted((luminance(a), luminance(b)))
    return (second+.05)/(first+.05)


def high_contrast(surface):
    return max(((0,0,0), (255,255,255)), key=lambda c: contrast(c, surface))


def focus_color(accent, surface, control):
    if all(contrast(accent, bg) >= 3.1 for bg in (surface, control)):
        return accent
    choices = [blend(accent, endpoint, step / 100)
               for endpoint in ((0, 0, 0), (255, 255, 255))
               for step in range(1, 101)]
    eligible = [c for c in choices
                if all(contrast(c, bg) >= 3.1 for bg in (surface, control))]
    if not eligible:
        raise ValueError('Unusable focus color')
    return min(eligible, key=lambda c: sum((x - y) ** 2 for x, y in zip(c, accent)))


def content_foreground(accent, surfaces):
    """Closest accent shade meeting AA on every actual state of this surface."""
    choices = [blend(accent, endpoint, step / 100)
               for endpoint in ((0, 0, 0), (255, 255, 255))
               for step in range(101)]
    eligible = [c for c in choices
                if all(contrast(c, bg) >= 4.55 for bg in surfaces)]
    if not eligible:
        raise ValueError('No AA content foreground for surfaces '+str(surfaces))
    return min(eligible, key=lambda c: sum((x-y)**2 for x, y in zip(c, accent)))


def content_adapters(catalog, generated_css):
    # Resolve the same gray cascade as the browser, including the handcrafted
    # legacy adapters. Never change the original MIT source tokens.
    sources = [(ROOT.parent/'index.css').read_text(),
               (ROOT/'theme-presets.css').read_text(), generated_css]
    entries = [{'id': 'dark', 'mode': 'dark'}, {'id': 'light', 'mode': 'light'}] + catalog
    rules = ['/* AA content aliases against real conversation and header surfaces. */']
    for entry in entries:
        grays = {}
        for source in sources:
            for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', source):
                if (f'[data-theme="{entry["mode"]}"]' in selector or
                        f'[data-atlas-theme="{entry["id"]}"]' in selector):
                    grays.update({int(k): tuple(map(int, v.split())) for k, v in
                                  re.findall(r'--gray-(\d+):\s*([\d ]+);', declarations)})
        # Assistant prose lives on gray-800; highlighted code on gray-900.
        # Header menu/help controls also hover from gray-700 to gray-600.
        surfaces = {'link': [grays[800]], 'syntax-keyword': [grays[900]],
                    'syntax-string': [grays[900]], 'syntax-number': [grays[900]],
                    'syntax-comment': [grays[900]],
                    'header-menu-accent': [grays[800]],
                    'header-accent': [grays[700]], 'header-hover-accent': [grays[600]]}
        accents = {'link': '#2563eb', 'syntax-keyword': '#2563eb',
                   'syntax-string': '#16a34a', 'syntax-number': '#ca8a04',
                   'syntax-comment': '#6b7280', 'header-menu-accent': '#60a5fa',
                   'header-accent': '#60a5fa', 'header-hover-accent': '#60a5fa'}
        declarations = [f'  --atlas-{key}: {hexcolor(content_foreground(rgb(accents[key]), bgs))};'
                        for key, bgs in surfaces.items()]
        rules.append(f'[data-atlas-theme="{entry["id"]}"] {{\n'+
                     '\n'.join(declarations)+'\n}')
    return '\n\n'.join(rules)


def theme_bootstrap(catalog):
    modes = {'dark': 'dark', 'light': 'light', **{t['id']: t['mode'] for t in catalog}}
    return ("// Generated by build-retro-catalog.py; do not edit.\n"
            "// Synchronous first-paint counterpart of ThemeContext / resolveThemeId.\n"
            "(function () {\n"
            "  var modes = "+json.dumps(modes, separators=(',', ':'))+";\n"
            "  var id = 'dark';\n"
            "  try {\n"
            "    var saved = localStorage.getItem('atlas-theme');\n"
            "    if (Object.prototype.hasOwnProperty.call(modes, saved)) id = saved;\n"
            "  } catch { /* Storage can be unavailable in privacy modes. */ }\n"
            "  document.documentElement.setAttribute('data-theme', modes[id]);\n"
            "  document.documentElement.setAttribute('data-atlas-theme', id);\n"
            "})();\n")


def text_shade(surface, minimum):
    target = high_contrast(surface)
    for n in range(101):
        value = blend(surface, target, n/100)
        if contrast(value, surface) >= minimum:
            return value
    return target


def utility_surface(surface, amount, foregrounds):
    # Gray utility controls can sit near the luminance crossover. Cap their
    # shift toward the foreground before it erodes AA text contrast.
    for step in range(round(amount * 100), -1, -1):
        candidate = blend(surface, high_contrast(surface), step / 100)
        if all(contrast(candidate, fg) >= 4.55 for fg in foregrounds):
            return candidate
    raise ValueError('Unusable utility surface')


def source_colors(info, vars):
    palette = [rgb(c) for c in info['palette']]
    palette = [c for c in palette if c is not None]
    keys = ('bg', 'desktop', 'paper', 'shell', 'tt-black', 'c-black', 'wb-blue',
            'bg-top', 'sky-top', 'tape', 'panel-bg', 'lcd-0')
    background = next((rgb(vars[k]) for k in keys if k in vars and rgb(vars[k])),
                      palette[0] if palette else (255,255,255))
    dark = luminance(background) < .21
    surface = blend(background, (255,255,255) if dark else (0,0,0), .12 if dark else .07)
    foreground = high_contrast(surface)
    colorful = palette[1:] + palette[:1]
    usable = [c for c in colorful if contrast(c, surface) >= 3]
    accent = max(usable, key=lambda c: (max(c)-min(c), contrast(c,surface))) if usable else foreground
    header = accent
    return background, surface, foreground, accent, header, dark


def css_rgb(color):
    return ' '.join(str(x) for x in color)


def entry_id(item):
    return LEGACY_IDS.get(item['id'], item['slug'][3:])


# All 53 source designs have a chrome treatment. These eight already have
# hand-reviewed adapters in theme-presets.css / retroChrome.css, whereas the
# other 45 are generated from the pinned manifest and original palette.
HANDCRAFTED_CHROME = frozenset((1, 2, 3, 4, 5, 6, 7, 9))
CHROME_MOTIFS = {
    8: 'broadcast', 10: 'terminal', 11: 'pixel', 12: 'glass',
    13: 'industrial', 14: 'web', 15: 'science', 16: 'neon',
    17: 'graphic', 18: 'pixel', 19: 'bevel', 20: 'bevel',
    21: 'gloss', 22: 'lcd', 23: 'industrial', 24: 'science',
    25: 'glitch', 26: 'paper', 27: 'terminal', 28: 'terminal',
    29: 'terminal', 30: 'terminal', 31: 'tui', 32: 'science',
    33: 'tui', 34: 'pixel', 35: 'minimal', 36: 'glass',
    37: 'soft', 38: 'grid', 39: 'soft', 40: 'brutalist',
    41: 'minimal', 42: 'graphic', 43: 'comic', 44: 'optical',
    45: 'dream', 46: 'minimal', 47: 'web', 48: 'paper',
    49: 'glitch', 50: 'gloss', 51: 'poster', 52: 'grid',
    53: 'web',
}

# Four semantic properties for each architectural design family: window,
# titlebar, button and input. All selectors are scoped to RetroControls.
# No page-wide reset, injected third-party demo layout, or animation.
CHROME_MOTIF_RULES = {
    'broadcast': (
        'border: 3px solid var(--atlas-retro-control-border); border-radius: 0;',
        'border-bottom: 3px solid var(--atlas-retro-control-border);',
        'border-radius: 0; border-width: 3px;',
        'border-radius: 0; border-width: 2px;'),
    'terminal': (
        'border: 4px double var(--atlas-retro-control-border); border-radius: 0;',
        'border-bottom: 1px dotted var(--atlas-retro-control-border);',
        'border-radius: 0; border-style: solid;',
        'border-radius: 0; border-width: 1px;'),
    'pixel': (
        'border-width: 3px; border-radius: 0; box-shadow: 5px 5px 0 #0006;',
        'border-bottom-width: 3px;',
        'border-radius: 0; border-width: 3px; box-shadow: 2px 2px 0 #0005;',
        'border-radius: 0; border-width: 2px;'),
    'glass': (
        'border-radius: 12px; box-shadow: 0 8px 24px #0003, inset 0 1px 0 #fff5;',
        'background-image: linear-gradient(to bottom, #fff3, #fff0 65%, #0001);',
        'border-radius: 9px; box-shadow: inset 0 1px 0 #fff5, 0 2px 5px #0002;',
        'border-radius: 8px; box-shadow: inset 0 1px 2px #0002;'),
    'industrial': (
        'border-width: 2px; border-radius: 2px; box-shadow: inset 2px 2px 0 #fff5, inset -2px -2px 0 #0003;',
        'border-bottom-width: 2px;',
        'border-width: 2px; box-shadow: inset 1px 1px 0 #fff5, inset -1px -1px 0 #0003;',
        'border-width: 2px; box-shadow: inset 1px 1px 0 #0003;'),
    'web': (
        'border-style: dashed; border-width: 2px; border-radius: 0;',
        'border-bottom-style: dotted; border-bottom-width: 2px;',
        'border-radius: 0; text-decoration: underline; text-underline-offset: 3px;',
        'border-radius: 0;'),
    'science': (
        'border-width: 1px; border-radius: 0; box-shadow: inset 0 0 0 2px #fff2;',
        'background-image: repeating-linear-gradient(to right, #fff0 0 22px, #fff2 22px 23px);',
        'border-radius: 0; letter-spacing: .06em;',
        'border-radius: 0;'),
    'neon': (
        'border-radius: 4px; border-width: 2px; box-shadow: 0 0 12px #ffffff23;',
        'border-bottom-width: 2px; text-transform: uppercase;',
        'border-radius: 3px; box-shadow: 0 0 5px #ffffff26;',
        'border-radius: 2px;'),
    'graphic': (
        'border-left-width: 7px; border-radius: 0; box-shadow: 5px 5px 0 #0004;',
        'border-bottom-width: 4px;',
        'border-radius: 0; font-weight: 800; text-transform: uppercase;',
        'border-radius: 0; border-width: 2px;'),
    'bevel': (
        'border-width: 3px; border-radius: 0; box-shadow: inset 1px 1px 0 #fff5, inset -1px -1px 0 #0004;',
        'border-bottom-width: 2px;',
        'border-width: 2px; border-radius: 0; box-shadow: inset 1px 1px 0 #fff5, inset -1px -1px 0 #0003;',
        'border-width: 2px; border-radius: 0;'),
    'gloss': (
        'border-radius: 10px; box-shadow: 0 4px 12px #0004;',
        'background-image: linear-gradient(to bottom, #fff5, #fff0 52%, #0002);',
        'border-radius: 14px; background-image: linear-gradient(to bottom, #fff4, #fff0 55%, #0002);',
        'border-radius: 7px;'),
    'lcd': (
        'border: 5px solid var(--atlas-retro-control-border); border-radius: 11px; box-shadow: inset 0 0 0 2px #0002;',
        'border-bottom-width: 3px;',
        'border-radius: 4px; border-width: 2px;',
        'border-radius: 0; border-width: 2px;'),
    'glitch': (
        'border-radius: 0; border-width: 2px; box-shadow: -3px 0 #ff00664d, 3px 0 #00ffff4d;',
        'border-bottom-width: 3px;',
        'border-radius: 0; box-shadow: 2px 1px 0 #ff006633;',
        'border-radius: 0;'),
    'paper': (
        'border: 1px solid var(--atlas-retro-control-border); border-radius: 0; box-shadow: 4px 4px 0 #0003;',
        'background-image: repeating-linear-gradient(to bottom, #fff0 0 10px, #0001 10px 11px);',
        'border-radius: 0; border-width: 1px;',
        'border-radius: 0; border-bottom-width: 2px;'),
    'tui': (
        'border: 3px double var(--atlas-retro-control-border); border-radius: 0;',
        'border-bottom: 3px double var(--atlas-retro-control-border);',
        'border-radius: 0; font-family: monospace;',
        'border-radius: 0; font-family: monospace;'),
    'minimal': (
        'border-width: 1px; border-radius: 0; box-shadow: none;',
        'border-bottom-width: 1px; text-transform: uppercase; letter-spacing: .05em;',
        'border-radius: 0; border-width: 1px; box-shadow: none;',
        'border-radius: 0; border-width: 1px;'),
    'soft': (
        'border-width: 1px; border-radius: 18px; box-shadow: 6px 6px 15px #0002, -2px -2px 8px #fff2;',
        'border-bottom-width: 0;',
        'border-radius: 16px; box-shadow: 2px 3px 8px #0002;',
        'border-radius: 10px;'),
    'grid': (
        'border-width: 2px; border-radius: 0; box-shadow: inset 0 0 0 1px #fff2;',
        'background-image: repeating-linear-gradient(to right, #fff0 0 15px, #fff2 15px 16px);',
        'border-radius: 0; letter-spacing: .04em;',
        'border-radius: 0;'),
    'brutalist': (
        'border: 5px solid var(--atlas-retro-control-border); border-radius: 0; box-shadow: 8px 8px 0 #0009;',
        'border-bottom-width: 4px; text-transform: uppercase;',
        'border: 3px solid var(--atlas-retro-control-border); border-radius: 0; box-shadow: 4px 4px 0 #0007;',
        'border-width: 3px; border-radius: 0;'),
    'comic': (
        'border: 3px solid var(--atlas-retro-control-border); border-radius: 16px; box-shadow: 6px 6px 0 #0007;',
        'background-image: radial-gradient(#fff4 1px, transparent 1px); background-size: 6px 6px;',
        'border-width: 3px; border-radius: 14px; box-shadow: 2px 3px 0 #0005;',
        'border-width: 2px; border-radius: 4px;'),
    'optical': (
        'border: 3px double var(--atlas-retro-control-border); border-radius: 0;',
        'background-image: repeating-linear-gradient(120deg, #fff0 0 6px, #fff2 6px 7px);',
        'border-radius: 0; border-width: 2px;',
        'border-radius: 0;'),
    'dream': (
        'border-radius: 24px; box-shadow: 0 8px 20px #0002;',
        'background-image: linear-gradient(135deg, #fff3, #fff0 70%);',
        'border-radius: 30px; box-shadow: 0 2px 8px #0002;',
        'border-radius: 16px;'),
    'poster': (
        'border-left-width: 9px; border-radius: 0; box-shadow: 3px 4px 0 #0005;',
        'border-bottom-width: 4px; text-transform: uppercase;',
        'border-radius: 0; font-weight: 900;',
        'border-radius: 0; border-width: 2px;'),
}
assert set(CHROME_MOTIFS) == set(range(1, 54)) - set(HANDCRAFTED_CHROME)


def generate_chrome(systems):
    parts = ['/* Generated by build-retro-catalog.py; do not edit.',
             ' * All 53 RDS identities receive chrome: eight have handcrafted',
             ' * adapters and 45 get scoped source-derived visual grammar.',
             ' * No JavaScript, bitmap assets, runtime artwork, or remote CSS.',
             ' */']
    for item in systems:
        number = item['id']
        if number in HANDCRAFTED_CHROME:
            continue
        ident = entry_id(item)
        motif = CHROME_MOTIFS[number]
        window, title, button, field = CHROME_MOTIF_RULES[motif]
        parts.append(
            f'/* #{number:02} — {item["name"]} · {motif} */\n'
            f'[data-atlas-theme="{ident}"] .atlas-retro-window {{ {window} }}\n'
            f'[data-atlas-theme="{ident}"] .atlas-retro-window-titlebar {{ {title} }}\n'
            f'[data-atlas-theme="{ident}"] .atlas-retro-button {{ {button} }}\n'
            f'[data-atlas-theme="{ident}"] .atlas-retro-input {{ {field} }}'
        )
    return '\n\n'.join(parts)+'\n'



def build():
    doc = json.loads((VENDOR/'manifest.json').read_text())
    systems = doc['systems']
    assert doc['count'] == len(systems) == 53, 'Expected full upstream catalog'
    assert sorted(item['id'] for item in systems) == list(range(1,54))
    ids = [entry_id(item) for item in systems]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r'[a-z0-9][a-z0-9-]*', i) for i in ids)

    scopes = ['/* Vendored source tokens, scoped to registered IDs, generated offline.',
              ' * Retro Design System / NovusGFX, MIT, upstream '+SHA,
              ' * See vendor/retro-design-system/LICENSE. No scripts or external assets.',
              ' */']
    adapters = ['/* Generated semantic Atlas adapters from the complete RDS manifest.',
                ' * Upstream '+SHA+' (MIT). No demo CSS, images or fonts imported.',
                ' * Explicit legacy adapters (Win95, Mac7, CRT) live in theme-presets.css.',
                ' */']
    catalog = []

    for item, ident in zip(systems, ids):
        token_file = VENDOR/'source-tokens'/f"{item['slug']}.css"
        source = token_file.read_text()
        matched = SOURCE_PATTERN.fullmatch(source)
        if not matched or any(x in source.lower() for x in ('@import', '@font-face', 'url(', 'expression(', '</style')):
            raise ValueError(f'Unexpected CSS format or external asset in {token_file}')
        variables = dict((key, value.strip()) for key,value in TOKEN_PATTERN.findall(matched.group(2)))
        # Every source token must be a simple custom property, not an injected selector.
        residual = TOKEN_PATTERN.sub('', matched.group(2)).strip()
        if residual:
            raise ValueError(f'Unexpected non-token CSS in {token_file}: {residual[:80]}')
        scopes.append(source.replace(':root {', f'[data-atlas-theme="{ident}"] {{', 1).rstrip())

        bg, surface, fg, accent, header, dark = source_colors(item, variables)
        # The Win95 source's teal desktop is dark but its chrome and
        # preexisting Atlas light-mode adaptation are light surfaces.
        if item['id'] == 2:
            dark = False
        preview_bg = bg
        preview_surface = surface
        preview_accent = accent
        if item['id'] == 1:
            preview_bg, preview_surface, fg, preview_accent = rgb('#a8a8a8'), rgb('#dddddd'), rgb('#000000'), rgb('#000000')
        if item['id'] == 2:
            preview_bg, preview_surface, fg, preview_accent = rgb('#c0c0c0'), rgb('#dfdfdf'), rgb('#000000'), rgb('#000080')
        if item['id'] == 9:
            preview_bg, preview_surface, fg, preview_accent = rgb('#001a00'), rgb('#003300'), rgb('#aaffaa'), rgb('#33ff33')
        era = item.get('era') or 'Retro'
        year = item.get('year')
        desc = ', '.join(item.get('tags',[])[:4]) or era
        catalog.append({
            'id':ident, 'name':item['name'],
            'era': f'{era} · {year}' if year else era,
            'description':desc,
            'mode':'dark' if dark else 'light', 'reference':item['slug'],
            'tags':item.get('tags',[]),
            'preview': {'background':hexcolor(preview_bg),
                        'surface':hexcolor(preview_surface),
                        'foreground':hexcolor(fg), 'accent':hexcolor(preview_accent)},
        })

        if item['id'] in LEGACY_IDS:
            continue
        control = blend(surface, high_contrast(surface), .07)
        border = blend(surface, high_contrast(surface), .45)
        header_foreground = high_contrast(header)
        radii = '0' if era in ('Terminal','Desktop OS','Technical') or item['id'] in (40,47) else (
            '.7rem' if 'glass' in desc or 'clay' in desc else '.35rem')
        font = ('var(--font-sys, var(--font, Georgia, serif))' if item['id'] not in (40,47)
                else ('"Times New Roman", Times, serif'))
        if item['id'] == 40:
            font = '"Times New Roman", Times, serif'
        lines = [
            f'[data-atlas-theme="{ident}"] {{',
            f'  color-scheme: {"dark" if dark else "light"};',
            f'  --atlas-font-ui: {font};',
            f'  --atlas-body-fg: {hexcolor(high_contrast(bg))};',
            f'  --atlas-retro-window-fg: {hexcolor(high_contrast(surface))};',
            f'  --atlas-radius: {radii};',
            f'  --atlas-accent: {hexcolor(accent)};',
            f'  --atlas-focus-ring: {hexcolor(focus_color(accent, surface, control))};',
            f'  --atlas-retro-window-bg: {hexcolor(surface)};',
            f'  --atlas-retro-header-bg: {hexcolor(header)};',
            f'  --atlas-retro-header-fg: {hexcolor(header_foreground)};',
            f'  --atlas-retro-control-bg: {hexcolor(control)};',
            f'  --atlas-retro-control-fg: {hexcolor(high_contrast(control))};',
            f'  --atlas-retro-input-bg: {hexcolor(bg)};',
            f'  --atlas-retro-input-fg: {hexcolor(high_contrast(bg))};',
            f'  --atlas-retro-control-border: {hexcolor(border)};',
        ]
        grays = {900:bg, 800:surface,
                 700:utility_surface(surface, .07, [text_shade(surface, 7.4), high_contrast(surface)]),
                 600:utility_surface(surface, .22, [high_contrast(surface)]),
                 500:text_shade(surface,4.6), 400:text_shade(surface,5.2),
                 300:text_shade(surface,6.4), 200:text_shade(surface,7.4),
                 100:text_shade(surface,9.1), 50:high_contrast(surface)}
        lines.extend(f'  --gray-{key}: {css_rgb(value)};' for key,value in sorted(grays.items(),reverse=True))
        lines.append('}')
        adapters.append('\n'.join(lines))

    catalog_file = ROOT/'retroCatalog.generated.js'
    registry_data = json.dumps(catalog,ensure_ascii=False,indent=2)
    catalog_js = (
        '// Generated by frontend/scripts/build-retro-catalog.py; do not edit.\n'
        '// Pinned NovusGFX Retro Design System '+SHA+' (MIT).\n'
        'export const RETRO_CATALOG = Object.freeze(\n'
        '  '+registry_data.replace('\n','\n  ')+'.map(entry => Object.freeze({\n'
        '    ...entry, preview: Object.freeze(entry.preview), tags: Object.freeze(entry.tags),\n'
        '  }))\n'
        ')\n'
    )
    return {
        catalog_file: catalog_js,
        VENDOR/'tokens.css': '\n\n'.join(scopes).rstrip()+'\n',
        ROOT/'retroCatalog.generated.css': '\n\n'.join(adapters).rstrip()+'\n\n'+
            content_adapters(catalog, '\n\n'.join(adapters))+'\n',
        ROOT.parents[1]/'public/theme-bootstrap.js': theme_bootstrap(catalog),
        ROOT/'retroChrome.generated.css': generate_chrome(systems),
    }


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--check', action='store_true', help='Fail if generated assets are stale')
    check = args.parse_args().check
    results = build()
    for path, content in results.items():
        if check:
            if not path.exists() or path.read_text() != content:
                print(f'STALE {path}',file=sys.stderr)
                sys.exit(1)
        else:
            path.write_text(content)
        print(('OK ' if check else 'WROTE ')+str(path.relative_to(ROOT.parents[1])))
    print('53 upstream designs + 2 Atlas defaults, with no ANSI runtime or artwork')
