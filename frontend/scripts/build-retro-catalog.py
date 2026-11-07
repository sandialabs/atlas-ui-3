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


def text_shade(surface, minimum):
    target = high_contrast(surface)
    for n in range(101):
        value = blend(surface, target, n/100)
        if contrast(value, surface) >= minimum:
            return value
    return target


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
        grays = {900:bg, 800:surface, 700:control,
                 600:blend(surface, high_contrast(surface), .22),
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
        ROOT/'retroCatalog.generated.css': '\n\n'.join(adapters).rstrip()+'\n',
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
