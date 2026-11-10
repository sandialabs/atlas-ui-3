#!/usr/bin/env python3
"""Rebuild scoped Retro Design System catalog from pinned, vendored MIT sources.

Tokens, manifest metadata, and the original demo <style> blocks are imported
offline. The generator scopes those styles to [data-atlas-theme] and maps
upstream window/titlebar/button/input rules onto Atlas surfaces. Demo HTML/JS,
user assets, @font-face, remote url(), global resets, and animations never
enter the frontend bundle. Run with --check in CI.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'themes'
VENDOR = ROOT / 'vendor' / 'retro-design-system'
DEMOS = VENDOR / 'source-demos'
SHA = '8988f50a514e7f845dbe5ad190faa2afd926f0b4'
LEGACY_IDS = {1: 'mac7', 2: 'win95', 9: 'crt-phosphor'}
SOURCE_PATTERN = re.compile(r'\A(/\*[^*]*(?:\*(?!/)[^*]*)*\*/\s*)?:root\s*\{([^{}]*)\}\s*\Z', re.S)
TOKEN_PATTERN = re.compile(r'--([\w-]+)\s*:\s*([^;]+);')
HEX_PATTERN = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')

WINDOW_CLASSES = (
    'window', 'win-norton', 'pixel-box', 'metal-panel', 'panel-inset',
    'terminal', 'player', 'browser-chrome', 'game', 'screen', 'lcd',
    'construction-inner', 'construction', 'guestbook',
    'panel', 'card', 'box', 'page', 'sheet', 'glass', 'title-block',
    'ascii-box', 'placeholder-box', 'sketch-box', 'container',
)
TITLEBAR_CLASSES = (
    'titlebar', 'titlebar-inner', 'titlebar-screen', 'header-bar',
    'menu-bar', 'menubar', 'topline', 'header', 'status-bar',
    'browser-bar', 'red-bar', 'bar-black', 'caption', 'marquee',
    'head', 'bar', 'status',
)
BUTTON_CLASSES = (
    'btn', 'btn-default', 'btn-primary', 'beveled-btn', 'btn-chrome',
    'btn-sketch', 'tbtn', 'gadget',
)
PRIMARY_BUTTON_CLASSES = (
    'btn-default', 'btn-primary', 'btn-ok', 'start-btn', 'btn',
    'beveled-btn', 'btn-chrome', 'btn-sketch',
)
INPUT_CLASSES = (
    'input', 'input-chrome', 'input-sketch', 'prompt',
)
CLOSE_CLASSES = (
    'closebox', 'close-box', 'tbtn',
)
TITLE_TEXT_CLASSES = ('title',)

# Some demos draw their chrome as page *text* (box-drawing / ASCII / ANSI
# spans) rather than CSS classes, so class mapping alone yields a generic dark
# window. For those, map the colour roles of the demo's own text frames onto
# Atlas surfaces, using only the demo's original tokens. No demo text, art,
# counters, or maps enter Atlas; this is palette + border chrome only.
CONTENT_CHROME = {
    # 14-geocities-web10: mostly class-mapped (marquee titlebar, construction
    # hazard frame). Nested Atlas windows use the black, hot-pink ridged
    # .webring box rather than a second hazard frame.
    'geocities-web10': {
        'retro_window': ('from', 'webring'),
    },
    # 28-nethack-ascii: rooms are framed by --wall '-'/'|' runs; the message
    # topline and bottom status line are --ink on --bg with a --dim rule, and
    # status labels (St:, HP:) are --monster-green. Items like '(' '[' are
    # --item-cyan. body::before contributes the faint CRT scanline overlay.
    'nethack-ascii': {
        'fold_body_overlay': True,
        'window': [('background', 'var(--bg)'), ('background-color', '#000000'),
                   ('color', 'var(--ink)'), ('border', '1px dashed var(--wall)')],
        'titlebar': [('background', 'var(--bg)'), ('background-color', '#000000'),
                     ('color', 'var(--ink)'), ('padding', '6px 10px'),
                     ('border', '0'), ('border-bottom', '1px solid var(--dim)')],
        'title': [('color', 'var(--monster-green)'), ('font-family', 'var(--font)')],
        'button': [('background', 'var(--bg)'), ('background-color', '#000000'),
                   ('color', 'var(--item-cyan)'), ('border', '1px solid var(--item-cyan)'),
                   ('border-radius', '0'), ('font-family', 'var(--font)')],
        'input': [('background', 'var(--bg)'), ('background-color', '#000000'),
                  ('color', 'var(--player)'), ('border', '1px solid var(--dim)'),
                  ('border-radius', '0'), ('font-family', 'var(--font)')],
        'footer': [('border-top', '1px solid var(--dim)'), ('background', 'var(--bg)'),
                   ('color', 'var(--ink)')],
        'close': [('background', 'var(--bg)'), ('background-color', '#000000'),
                  ('color', 'var(--ink)'), ('border', '1px solid var(--dim)'),
                  ('border-radius', '0')],
    },
    # 30-bbs-ansi: the sysop info panel is a --yellow-hi double-line box
    # (╔═╗), the MAIN MENU band is --red-hi shade blocks around spaced
    # --yellow-hi caps on black, menu keys are --green-hi labels in --cyan-hi
    # brackets, the Command: prompt is --yellow-hi, and rules are --magenta.
    'bbs-ansi': {
        'window': [('background', 'var(--bg)'), ('background-color', '#000000'),
                   ('color', 'var(--white-hi)'), ('border', '4px double var(--yellow-hi)')],
        'titlebar': [('background', 'var(--bg)'), ('background-color', '#000000'),
                     ('color', 'var(--yellow-hi)'), ('padding', '4px 10px'),
                     ('border', '4px solid var(--red-hi)'),
                     ('letter-spacing', '0.25em'), ('text-transform', 'uppercase')],
        'title': [('color', 'var(--yellow-hi)'), ('font-family', 'var(--font)')],
        'button': [('background', 'var(--bg)'), ('background-color', '#000000'),
                   ('color', 'var(--green-hi)'), ('border', '1px solid var(--cyan-hi)'),
                   ('border-radius', '0'), ('font-family', 'var(--font)')],
        'input': [('background', 'var(--bg)'), ('background-color', '#000000'),
                  ('color', 'var(--yellow-hi)'), ('border', '1px solid var(--cyan)'),
                  ('border-radius', '0'), ('font-family', 'var(--font)')],
        'footer': [('border-top', '1px solid var(--magenta)'), ('background', 'var(--bg)'),
                   ('color', 'var(--cyan-hi)')],
        'close': [('background', 'var(--bg)'), ('background-color', '#000000'),
                  ('color', 'var(--red-hi)'), ('border', '1px solid var(--red-hi)'),
                  ('border-radius', '0')],
    },
}

BODY_PROPS = (
    'background', 'background-color', 'background-image', 'background-size',
    'background-repeat', 'background-position', 'background-blend-mode',
    'background-attachment', 'color', 'font-family', 'font-weight',
    'font-style', 'letter-spacing', 'line-height', 'image-rendering',
    'cursor',
)
VISUAL_PROPS = BODY_PROPS + (
    'font', 'font-size', 'font-variant', 'text-shadow', 'text-transform',
    'text-decoration', 'text-underline-offset', 'text-align', 'text-overflow',
    'white-space', 'overflow-wrap', 'word-break', 'caret-color', 'opacity',
    'filter', 'backdrop-filter', 'box-shadow', 'border-radius',
    'border', 'border-top', 'border-right', 'border-bottom', 'border-left',
    'border-color', 'border-style', 'border-width',
    'border-top-color', 'border-right-color', 'border-bottom-color', 'border-left-color',
    'border-top-style', 'border-right-style', 'border-bottom-style', 'border-left-style',
    'border-top-width', 'border-right-width', 'border-bottom-width', 'border-left-width',
    'outline-offset', 'padding', 'padding-top', 'padding-right',
    'padding-bottom', 'padding-left', 'box-sizing',
)
TITLE_LAYOUT_PROPS = (
    'display', 'align-items', 'justify-content', 'gap', 'flex-direction',
    'flex-wrap',
)
CONTROL_SIZE_PROPS = ('width', 'height', 'min-width', 'min-height')
BLOCKED_PROP_PREFIXES = ('animation', 'transition', 'offset')
BLOCKED_PROPS = {
    'animation', 'transition', 'transform', 'will-change', 'content',
    'position', 'top', 'left', 'right', 'bottom', 'inset', 'float', 'clear',
    'z-index', 'overflow', 'overflow-x', 'overflow-y', 'margin', 'margin-top',
    'margin-right', 'margin-bottom', 'margin-left', 'max-width', 'max-height',
    'flex', 'flex-grow', 'flex-shrink', 'flex-basis', 'grid', 'grid-template',
    'grid-template-columns', 'grid-template-rows', 'order', 'align-self',
    'justify-self', 'clip-path', 'resize', 'columns',
}


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


def text_shade(surface, minimum):
    target = high_contrast(surface)
    for n in range(101):
        value = blend(surface, target, n/100)
        if contrast(value, surface) >= minimum:
            return value
    return target


def utility_surface(surface, amount, foregrounds):
    for step in range(round(amount * 100), -1, -1):
        candidate = blend(surface, high_contrast(surface), step / 100)
        if all(contrast(candidate, fg) >= 4.55 for fg in foregrounds):
            return candidate
    raise ValueError('Unusable utility surface')


def css_rgb(color):
    return ' '.join(str(x) for x in color)


def entry_id(item):
    return LEGACY_IDS.get(item['id'], item['slug'][3:])


def content_adapters(catalog, generated_css, link_accents=None):
    # Resolve the same gray cascade as the browser, including the handcrafted
    # legacy adapters. Never change the original MIT source tokens.
    sources = [(ROOT.parent/'index.css').read_text(),
               (ROOT/'theme-presets.css').read_text(), generated_css]
    entries = [{'id': 'dark', 'mode': 'dark'}, {'id': 'light', 'mode': 'light'}] + catalog
    link_accents = link_accents or {}
    rules = ['/* AA content aliases against real conversation and header surfaces. */']
    for entry in entries:
        grays = {}
        for source in sources:
            for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', source):
                if (f'[data-theme="{entry["mode"]}"]' in selector or
                        f'[data-atlas-theme="{entry["id"]}"]' in selector):
                    grays.update({int(k): tuple(map(int, v.split())) for k, v in
                                  re.findall(r'--gray-(\d+):\s*([\d ]+);', declarations)})
        surfaces = {'link': [grays[800]], 'syntax-keyword': [grays[900]],
                    'syntax-string': [grays[900]], 'syntax-number': [grays[900]],
                    'syntax-comment': [grays[900]],
                    'header-menu-accent': [grays[800]],
                    'header-accent': [grays[700]], 'header-hover-accent': [grays[600]]}
        accents = {'link': link_accents.get(entry['id'], '#2563eb'),
                   'syntax-keyword': '#2563eb',
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


def source_colors(info, vars):
    palette = [rgb(c) for c in info['palette']]
    palette = [c for c in palette if c is not None]
    # Window/chrome surfaces decide light/dark mode. Desktop/body backgrounds
    # are applied separately and may be a darker "wallpaper" than the chrome.
    surface_keys = ('paper', 'chrome', 'chrome-light', 'wb-white', 'wb-gray',
                    'panel-bg', 'lcd-0', 'shell')
    desktop_keys = ('desktop', 'bg', 'wb-blue', 'bg-top', 'sky-top', 'tape')
    surface = next((rgb(vars[k]) for k in surface_keys if k in vars and rgb(vars[k])), None)
    background = next((rgb(vars[k]) for k in desktop_keys if k in vars and rgb(vars[k])),
                      palette[0] if palette else (255, 255, 255))
    if surface is None:
        surface = blend(background, (255, 255, 255) if luminance(background) < .21 else (0, 0, 0),
                        .12 if luminance(background) < .21 else .07)
    dark = luminance(surface) < .21
    foreground = high_contrast(surface)
    colorful = palette[1:] + palette[:1]
    usable = [c for c in colorful if contrast(c, surface) >= 3]
    accent = max(usable, key=lambda c: (max(c)-min(c), contrast(c, surface))) if usable else foreground
    # Prefer named accent tokens from the original demo when they contrast.
    for key in ('aqua-blue', 'title', 'mx', 'mx-hi', 'ink', 'accent', 'link',
                'select', 'gold', 'player'):
        named = rgb(vars[key]) if key in vars else None
        if named and contrast(named, surface) >= 3:
            accent = named
            break
    header = accent
    return background, surface, foreground, accent, header, dark


# ---------------------------------------------------------------------------
# Demo CSS parsing and mapping onto Atlas surfaces
# ---------------------------------------------------------------------------

def strip_css_comments(css):
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def extract_demo_css(html):
    return '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', html, re.S | re.I))


def skip_at_rule(css, start):
    i = start
    n = len(css)
    while i < n and css[i] not in '{;':
        i += 1
    if i < n and css[i] == ';':
        return i + 1
    depth = 0
    while i < n:
        if css[i] == '{':
            depth += 1
        elif css[i] == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def iter_rules(css):
    css = strip_css_comments(css)
    i = 0
    n = len(css)
    while i < n:
        while i < n and css[i].isspace():
            i += 1
        if i >= n:
            break
        if css[i] == '@':
            i = skip_at_rule(css, i)
            continue
        start = i
        while i < n and css[i] != '{':
            i += 1
        if i >= n:
            break
        selector = css[start:i].strip()
        i += 1
        depth = 1
        body_start = i
        while i < n and depth:
            if css[i] == '{':
                depth += 1
            elif css[i] == '}':
                depth -= 1
            i += 1
        body = css[body_start:i-1] if depth == 0 else css[body_start:i]
        if selector and '{' not in body:
            yield selector, body


def parse_decls(block):
    decls = []
    current = []
    depth = 0
    for char in block:
        if char == '(':
            depth += 1
            current.append(char)
        elif char == ')':
            depth = max(0, depth - 1)
            current.append(char)
        elif char == ';' and depth == 0:
            item = ''.join(current).strip()
            current = []
            if item and ':' in item:
                prop, _, value = item.partition(':')
                decls.append((prop.strip().lower(), value.strip()))
        else:
            current.append(char)
    item = ''.join(current).strip()
    if item and ':' in item:
        prop, _, value = item.partition(':')
        decls.append((prop.strip().lower(), value.strip()))
    return decls


def format_decls(decls, indent='  '):
    return '\n'.join(f'{indent}{prop}: {value};' for prop, value in decls)


def has_visual_fill(decls):
    keys = {prop for prop, _ in decls}
    return bool(keys & {
        'background', 'background-color', 'background-image',
        'border', 'border-top', 'border-bottom', 'border-color',
        'box-shadow',
    })


def filter_decls(decls, *, extra=(), allow_size=False, allow_outline=False):
    allowed = set(VISUAL_PROPS) | set(extra)
    if allow_size:
        allowed.update(CONTROL_SIZE_PROPS)
    kept = []
    for prop, value in decls:
        if prop in BLOCKED_PROPS or any(prop.startswith(p + '-') for p in BLOCKED_PROP_PREFIXES):
            continue
        if not allow_outline and (prop == 'outline' or prop.startswith('outline-')):
            continue
        if prop not in allowed:
            continue
        lowered = value.lower()
        if 'animation' in lowered or '@' in lowered:
            continue
        kept.append((prop, value))
    return kept


def split_selectors(selector):
    parts, current, depth = [], [], 0
    for char in selector:
        if char == '(':
            depth += 1
            current.append(char)
        elif char == ')':
            depth = max(0, depth - 1)
            current.append(char)
        elif char == ',' and depth == 0:
            item = ''.join(current).strip()
            if item:
                parts.append(item)
            current = []
        else:
            current.append(char)
    item = ''.join(current).strip()
    if item:
        parts.append(item)
    return parts


def selector_targets_class(selector, classname):
    pattern = re.compile(r'\.' + re.escape(classname) + r'(?![\w-])')
    return any(pattern.search(part) and not re.search(r':(?:hover|active|focus|focus-visible|visited|disabled)\b', part)
               for part in split_selectors(selector))


def selector_is_exactly_class(selector, classname):
    pattern = re.compile(r'^\.' + re.escape(classname) + r'$')
    return any(pattern.match(part.strip()) for part in split_selectors(selector))


def selector_is_element(selector, element):
    pattern = re.compile(r'^' + element + r'(?:\[[^\]]+\])?$')
    return any(pattern.match(part.strip()) for part in split_selectors(selector))


def first_class_rule(rules, classnames, require_fill=False):
    for name in classnames:
        for selector, decls in rules:
            if selector_is_exactly_class(selector, name):
                if require_fill and not has_visual_fill(decls):
                    continue
                return name, selector, decls
        for selector, decls in rules:
            if selector_targets_class(selector, name) and not re.search(r'\s', selector.strip()):
                if require_fill and not has_visual_fill(decls):
                    continue
                return name, selector, decls
    return None


def first_element_rule(rules, selectors):
    for wanted in selectors:
        for selector, decls in rules:
            if any(part.strip() == wanted for part in split_selectors(selector)):
                return wanted, selector, decls
    return None


def state_rules(rules, classname, state):
    found = []
    pattern = re.compile(r'\.' + re.escape(classname) + r':' + state + r'\b')
    for selector, decls in rules:
        if any(pattern.search(part) for part in split_selectors(selector)):
            found.append((selector, decls))
    return found


def resolve_color(value, variables, depth=0):
    if not value or depth > 6:
        return None
    text = value.strip()
    direct = rgb(text)
    if direct:
        return direct
    match = re.fullmatch(r'rgba?\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)', text)
    if match:
        return tuple(int(float(match.group(i))) for i in range(1, 4))
    var = re.fullmatch(r'var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)', text, re.S)
    if var:
        name = var.group(1)[2:]
        if name in variables:
            return resolve_color(variables[name], variables, depth + 1)
        if var.group(2):
            return resolve_color(var.group(2), variables, depth + 1)
        return None
    # Gradients and layered backgrounds: try the first hex/var color.
    for token in re.findall(r'var\(--[\w-]+\)|#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)', text):
        found = resolve_color(token, variables, depth + 1)
        if found:
            return found
    return None


def first_color_from_decls(decls, variables, props):
    for wanted in props:
        for prop, value in decls:
            if prop == wanted:
                color = resolve_color(value, variables)
                if color:
                    return color
    return None


def load_html(item):
    return (DEMOS / f"{item['slug']}.html").read_text()


def titlebar_child(html, rules):
    """A .titlebar with no fill of its own (BeOS) draws its caption on its
    first filled child element (the yellow .tab). Use that child instead."""
    match = re.search(r'class="titlebar"[^>]*>\s*<\w+[^>]*class="([\w-]+)', html)
    if not match:
        return None
    return first_class_rule(rules, (match.group(1),), require_fill=True)


def body_overlay_layers(rules):
    """Gradient layers from a body::before overlay (CRT scanlines)."""
    for selector, decls in rules:
        if any(part.strip() in ('body::before', 'body::after') for part in split_selectors(selector)):
            for prop, value in decls:
                if prop in ('background', 'background-image') and 'gradient(' in value and 'url(' not in value:
                    return value
    return None


def load_demo(item):
    path = DEMOS / f"{item['slug']}.html"
    if not path.exists():
        raise FileNotFoundError(f'Missing vendored demo {path}')
    html = path.read_text()
    if re.search(r'@import|url\s*\(\s*["\']?\s*https?:', html, re.I):
        raise ValueError(f'External asset in {path}')
    css = extract_demo_css(html)
    rules = [(selector, parse_decls(body)) for selector, body in iter_rules(css)]
    return css, rules




def finalize_background(decls):
    """Do not let the background shorthand wipe a solid background-color."""
    color = None
    out = []
    for prop, value in decls:
        if prop == 'background-color':
            color = value
            continue
        if prop == 'background' and re.search(r'gradient\(|url\(', value):
            out.append(('background-image', value))
            continue
        out.append((prop, value))
    if color:
        out.append(('background-color', color))
    return out


def ensure_readable(decls, variables, fallback_bg=None):
    """Keep original fills, but guarantee AA text and a solid background-color
    so contrast checks (which read background-color, not images) stay honest."""
    decls = list(decls)
    bg = first_color_from_decls(decls, variables, ('background-color', 'background')) or fallback_bg
    if bg and not any(prop == 'background-color' for prop, _ in decls):
        decls.insert(0, ('background-color', hexcolor(bg)))
    elif not bg and fallback_bg:
        decls.insert(0, ('background-color', hexcolor(fallback_bg)))
        bg = fallback_bg
    fg = first_color_from_decls(decls, variables, ('color',))
    if bg and (fg is None or contrast(fg, bg) < 4.55):
        decls = [(prop, value) for prop, value in decls if prop != 'color']
        decls.append(('color', hexcolor(content_foreground(fg or high_contrast(bg), [bg]))))
    return finalize_background(decls), bg


def is_colorful(color, surface=None):
    """True when a color has visible chroma (not gray / ink / paper)."""
    if not color:
        return False
    return max(color) - min(color) >= 25


def pick_accent_color(variables, titlebar, primary, surface):
    """Prefer a distinctive original token or control fill, never a warn/red-pill."""
    named = (
        'aqua-blue', 'title', 'select', 'mx-hi', 'mx', 'player', 'gold',
        'hotpink', 'lime', 'yellow-hi', 'yellow', 'cyan-hi', 'wb-orange',
        'link', 'accent',
    )
    for key in named:
        color = rgb(variables[key]) if key in variables else None
        if color and is_colorful(color, surface):
            return color
    # Primary / default button when it is actually colorful (Aqua gel blue).
    if primary:
        name = primary[0] or ''
        if 'red' not in name and 'warn' not in name:
            candidate = first_color_from_decls(
                primary[2], variables,
                ('background-color', 'background', 'border-color', 'color'))
            if candidate and is_colorful(candidate, surface):
                return candidate
    if titlebar:
        candidate = first_color_from_decls(
            titlebar[2], variables,
            ('background-color', 'background', 'border-color', 'color'))
        if candidate and is_colorful(candidate, surface) and candidate != rgb(variables.get('ink', '')):
            return candidate
    return variables.get('_accent_rgb')


def fallback_window(ident, variables, surface, accent):
    """When a demo has no window class, still use its tokens for chrome."""
    border = accent if accent else high_contrast(surface)
    return (
        f'[data-atlas-theme="{ident}"] .atlas-retro-window,\n'
        f'[data-atlas-theme="{ident}"] .atlas-theme-chrome {{\n'
        f'  background: {hexcolor(surface)};\n'
        f'  color: {hexcolor(high_contrast(surface))};\n'
        f'  border: 1px solid {hexcolor(border)};\n}}'
    )


def fallback_button(ident, surface):
    """When a demo has no button class, use the window surface so a legal ring exists."""
    bg = surface or (255, 255, 255)
    fg = high_contrast(bg)
    return (
        f'[data-atlas-theme="{ident}"] .atlas-retro-button,\n'
        f'[data-atlas-theme="{ident}"] .atlas-theme-chrome .atlas-retro-button {{\n'
        f'  background: {hexcolor(bg)};\n'
        f'  background-color: {hexcolor(bg)};\n'
        f'  color: {hexcolor(fg)};\n'
        f'  border: 2px solid {hexcolor(fg)};\n}}'
    )


def fallback_ua_defaults(ident):
    """#47 Default Browser has no stylesheet; this is the documented UA look."""
    return (
        f'[data-atlas-theme="{ident}"] {{\n'
        f'  --atlas-font-ui: "Times New Roman", Times, serif;\n'
        f'  --atlas-user-bubble-bg: #0000ee;\n'
        f'  --atlas-user-bubble-fg: #ffffff;\n'
        f'  --atlas-accent-fill-bg: #0000ee;\n'
        f'  --atlas-accent-fill-fg: #ffffff;\n}}\n'
        f'[data-atlas-theme="{ident}"] body,\n'
        f'[data-atlas-theme="{ident}"] .atlas-theme-desktop,\n'
        f'[data-atlas-theme="{ident}"] .atlas-theme-backdrop {{\n'
        f'  background: #ffffff;\n'
        f'  color: #000000;\n'
        f'  font-family: "Times New Roman", Times, serif;\n}}\n'
        f'[data-atlas-theme="{ident}"] .atlas-retro-window,\n'
        f'[data-atlas-theme="{ident}"] .atlas-theme-chrome {{\n'
        f'  background: #ffffff;\n'
        f'  color: #000000;\n'
        f'  border: 1px solid #000000;\n}}\n'
        f'[data-atlas-theme="{ident}"] .atlas-retro-button,\n'
        f'[data-atlas-theme="{ident}"] .atlas-theme-accent-fill {{\n'
        f'  font-family: "Times New Roman", Times, serif;\n'
        f'  background: #c0c0c0;\n'
        f'  color: #000000;\n'
        f'  border: 2px outset #c0c0c0;\n}}\n'
        f'[data-atlas-theme="{ident}"] .user-message-bubble {{\n'
        f'  background: #0000ee;\n'
        f'  color: #ffffff;\n}}\n'
        f'[data-atlas-theme="{ident}"] .atlas-retro-input {{\n'
        f'  font-family: "Times New Roman", Times, serif;\n'
        f'  background: #ffffff;\n'
        f'  color: #000000;\n'
        f'  border: 2px inset #c0c0c0;\n}}'
    )


def pick_button(rules):
    found = first_class_rule(rules, BUTTON_CLASSES)
    if found:
        return found
    return first_element_rule(rules, (
        'button', 'input[type="submit"]', 'input[type="button"]',
    ))


def pick_primary_button(rules):
    found = first_class_rule(rules, PRIMARY_BUTTON_CLASSES)
    if found:
        return found
    return pick_button(rules)


def pick_input(rules):
    found = first_class_rule(rules, INPUT_CLASSES)
    if found:
        return found
    return first_element_rule(rules, (
        'input[type="text"]', 'input[type="email"]', 'textarea', 'input',
    ))


def pick_window(rules):
    found = first_class_rule(rules, WINDOW_CLASSES, require_fill=True)
    if found:
        return found
    return first_class_rule(rules, WINDOW_CLASSES)


def pick_titlebar(rules):
    return first_class_rule(rules, TITLEBAR_CLASSES, require_fill=True)


def body_decls(rules):
    for selector, decls in rules:
        if any(part.strip() == 'body' for part in split_selectors(selector)):
            return filter_decls(decls, extra=())
    return []


def emit_mapped(ident, atlas_selectors, decls, extra_comment=''):
    if not decls:
        return ''
    targets = ',\n'.join(f'[data-atlas-theme="{ident}"] {sel}' for sel in atlas_selectors)
    comment = f'/* {extra_comment} */\n' if extra_comment else ''
    return f'{comment}{targets} {{\n{format_decls(decls)}\n}}'


def map_component(ident, atlas_selectors, picked, *, extra=(), allow_size=False, comment='', variables=None, readable=False, fallback_bg=None):
    if not picked:
        return '', None
    name, _selector, decls = picked
    filtered = filter_decls(decls, extra=extra, allow_size=allow_size)
    if readable and variables is not None:
        filtered, _bg = ensure_readable(filtered, variables, fallback_bg)
    if not filtered:
        return '', name
    return emit_mapped(ident, atlas_selectors, filtered, comment or f'from .{name}' if name and not name.startswith(('button', 'input', 'textarea')) else f'from {name}'), name


def map_states(ident, atlas_selector, rules, classname, states=('hover', 'active', 'disabled')):
    parts = []
    if not classname:
        return parts
    for state in states:
        for _selector, decls in state_rules(rules, classname, state):
            filtered = filter_decls(decls)
            if filtered:
                parts.append(
                    f'[data-atlas-theme="{ident}"] {atlas_selector}:{state} {{\n'
                    f'{format_decls(filtered)}\n}}'
                )
    return parts


def demo_link_color(rules, variables):
    for selector, decls in rules:
        if any(re.match(r'^a$', part.strip()) or re.match(r'^a:link$', part.strip())
               for part in split_selectors(selector)):
            color = first_color_from_decls(decls, variables, ('color',))
            if color:
                return color
    if 'link' in variables:
        return rgb(variables['link'])
    return None


def demo_focus_color(rules, variables):
    for selector, decls in rules:
        if re.search(r':(?:focus|focus-visible)\b', selector):
            for prop, value in decls:
                if prop in ('outline-color', 'border-color', 'caret-color', 'color'):
                    color = resolve_color(value, variables)
                    if color:
                        return color
                if prop in ('outline', 'border', 'box-shadow'):
                    color = resolve_color(value, variables)
                    if color:
                        return color
    for token in ('aqua-blue', 'aqua-blue-deep', 'title', 'mx-hi', 'mx', 'ink'):
        if token in variables and rgb(variables[token]):
            return rgb(variables[token])
    return None


def content_chrome_blocks(ident, spec, rules, variables):
    """Emit CONTENT_CHROME roles; text colours are AA-checked like class maps."""
    targets = {
        'window': ['.atlas-retro-window', '.atlas-theme-chrome'],
        'retro_window': ['.atlas-retro-window', '.atlas-theme-option'],
        'close': ['.atlas-retro-window-close',
                  '.atlas-theme-titlebar > .atlas-theme-close'],
        'titlebar': ['.atlas-retro-window-titlebar', '.atlas-theme-titlebar'],
        'title': ['.atlas-retro-window-title', '.atlas-theme-titlebar h2'],
        'button': ['.atlas-retro-button', '.atlas-theme-chrome .atlas-retro-button'],
        'input': ['.atlas-retro-input'],
        'footer': ['.atlas-retro-window-footer'],
    }
    out = []
    for role, decls in spec.items():
        if role not in targets:
            continue
        note = f'{role} from the demo\'s text-drawn chrome'
        if isinstance(decls, tuple) and decls[0] == 'from':
            picked = first_class_rule(rules, (decls[1],))
            if not picked:
                continue
            decls = [d for d in filter_decls(picked[2])
                     if not d[0].startswith('padding') and d[0] != 'text-align']
            note = f'{role} from .{decls and picked[0]}'
        if role in ('window', 'retro_window', 'titlebar', 'button', 'input', 'close'):
            decls, _bg = ensure_readable(decls, variables, variables.get('_surface_rgb'))
        out.append(emit_mapped(ident, targets[role], decls, note))
    if spec.get('fold_body_overlay'):
        layer = body_overlay_layers(rules)
        if layer:
            out.append(emit_mapped(
                ident, ['body', '.atlas-theme-desktop', '.atlas-theme-backdrop'],
                [('background-image', layer)], 'body::before overlay folded into the fill'))
    return out


def generate_chrome(systems, variables_by_id):
    parts = [
        '/* Generated by build-retro-catalog.py; do not edit.',
        ' * Upstream demo CSS (NovusGFX Retro Design System '+SHA+', MIT)',
        ' * is mapped onto Atlas surfaces. No JavaScript, webfonts, external imports,',
        ' * remote resources, global resets, or animation is emitted.',
        ' */',
    ]
    surfaces = [
        '/* Desktop / body fill from each demo, scoped to the theme root and backdrop. */'
    ]
    accents = {}
    for item in systems:
        ident = entry_id(item)
        variables = variables_by_id[ident]
        _css, rules = load_demo(item)
        blocks = [f'/* #{item["id"]:02} — {item["name"]} · styles/{item["slug"]}/index.html */']

        window = pick_window(rules)
        titlebar = pick_titlebar(rules)
        html = load_html(item)
        titlebar_tab = False
        if not titlebar:
            child = titlebar_child(html, rules)
            if child:
                titlebar, titlebar_tab = child, True
        content = CONTENT_CHROME.get(ident, {})
        button = pick_button(rules)
        primary = pick_primary_button(rules)
        field = pick_input(rules)
        close = first_class_rule(rules, CLOSE_CLASSES)
        title_text = first_class_rule(rules, TITLE_TEXT_CLASSES)
        body = body_decls(rules)

        if body:
            body_visual = [(p, v) for p, v in body if p in BODY_PROPS]
            body_visual, _bg = ensure_readable(body_visual, variables)
            blocks.append(emit_mapped(
                ident,
                ['body', '.atlas-theme-desktop', '.atlas-theme-backdrop'],
                body_visual,
                'from body',
            ))
            font = None
            if 'font-sys' in variables:
                font = 'var(--font-sys)'
            else:
                font = next((v for p, v in body if p == 'font-family'), None)
                if not font and 'font' in variables:
                    font = 'var(--font)'
            if font:
                blocks.append(
                    f'[data-atlas-theme="{ident}"] {{\n'
                    f'  --atlas-font-ui: {font};\n}}'
                )

        if ident == 'default-browser' and not any(rules):
            blocks.append(fallback_ua_defaults(ident))
            parts.append('\n'.join(b for b in blocks if b))
            continue

        mapped_window = ''
        if window:
            name, _selector, decls = window
            filtered = [d for d in filter_decls(decls) if not d[0].startswith('padding') and d[0] != 'text-align']
            filtered, _bg = ensure_readable(filtered, variables, variables.get('_surface_rgb'))
            mapped_window = emit_mapped(ident, ['.atlas-retro-window', '.atlas-theme-chrome'], filtered,
                                 f'from .{name}' if name and not name[0].isupper() else f'from {name}')
            if mapped_window:
                blocks.append(mapped_window)
        if not mapped_window:
            surface = variables.get('_surface_rgb') or (255, 255, 255)
            accent = variables.get('_accent_rgb')
            blocks.append(fallback_window(ident, variables, surface, accent))
        if window and window[0].endswith('-inner'):
            # GeoCities .construction is a padded hazard-stripe box around
            # .construction-inner. Atlas has one element, so the outer
            # gradient becomes a border-image as wide as the outer padding.
            outer = first_class_rule(rules, (window[0][:-len('-inner')],))
            if outer:
                stripe = next((v for p_, v in outer[2]
                               if p_ in ('background', 'background-image') and 'gradient(' in v), None)
                pad = next((v for p_, v in outer[2] if p_ == 'padding'), None)
                if stripe and pad and re.fullmatch(r'\d+px', pad):
                    blocks.append(
                        f'/* frame from .{outer[0]} around .{window[0]} */\n'
                        f'[data-atlas-theme="{ident}"] .atlas-theme-chrome[role="dialog"] {{\n'
                        f'  border: {pad} solid transparent;\n'
                        f'  border-image: {stripe} {pad[:-2]};\n}}'
                    )

        mapped, _ = map_component(
            ident,
            ['.atlas-retro-window-titlebar', '.atlas-theme-titlebar'],
            titlebar,
            extra=TITLE_LAYOUT_PROPS,
            variables=variables,
            readable=True,
            fallback_bg=variables.get('_surface_rgb'),
        )
        if mapped:
            blocks.append(mapped)
            if titlebar_tab:
                # The caption is a left tab sized to its contents, not a full
                # bar; the window chrome shows through beside it (.filler).
                blocks.append(
                    f'/* .{titlebar[0]} is a tab inside .titlebar */\n'
                    f'[data-atlas-theme="{ident}"] .atlas-retro-window-titlebar,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-titlebar {{\n'
                    f'  width: max-content;\n  max-width: 100%;\n'
                    f'  justify-content: flex-start;\n}}\n'
                    f'[data-atlas-theme="{ident}"] .atlas-retro-window-title,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-titlebar h2 {{\n'
                    f'  font-size: inherit;\n  font-weight: inherit;\n  color: inherit;\n'
                    f'  line-height: inherit;\n}}'
                )
        elif window:
            # Use the window's border/background color as a caption treatment
            # when the demo has no titlebar class, still from original decls.
            fill = first_color_from_decls(window[2], variables,
                                          ('border-color', 'border', 'color', 'background-color', 'background'))
            if fill:
                fg = high_contrast(fill)
                blocks.append(
                    f'[data-atlas-theme="{ident}"] .atlas-retro-window-titlebar,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-titlebar {{\n'
                    f'  background: {hexcolor(fill)};\n'
                    f'  color: {hexcolor(fg)};\n'
                    f'  border-bottom-color: {hexcolor(fill)};\n}}'
                )

        if title_text:
            mapped, _ = map_component(
                ident,
                ['.atlas-retro-window-title', '.atlas-theme-titlebar h2'],
                title_text,
            )
            if mapped:
                blocks.append(mapped)

        mapped, button_name = map_component(
            ident,
            ['.atlas-retro-button', '.atlas-theme-chrome .atlas-retro-button'],
            button,
            variables=variables,
            readable=True,
            fallback_bg=variables.get('_surface_rgb'),
        )
        if mapped:
            blocks.append(mapped)
        else:
            blocks.append(fallback_button(ident, variables.get('_surface_rgb')))
        button_bg = first_color_from_decls(button[2], variables, ('background-color', 'background')) if button else None
        title_bg = first_color_from_decls(titlebar[2], variables, ('background-color', 'background')) if titlebar else None
        surface = variables.get('_surface_rgb')
        ring_src = variables.get('_accent_rgb') or (0, 0, 0)
        demo_focus = demo_focus_color(rules, variables)
        if demo_focus:
            ring_src = demo_focus
        try:
            ring = focus_color(ring_src, surface or (255, 255, 255), button_bg or surface or (255, 255, 255))
            if title_bg:
                ring = focus_color(ring, title_bg, button_bg or title_bg)
            blocks.append(
                f'[data-atlas-theme="{ident}"] {{\n'
                f'  --atlas-focus-ring: {hexcolor(ring)};\n}}'
            )
        except ValueError:
            pass
        if button_name and not button_name.startswith(('button', 'input')):
            blocks.extend(map_states(ident, '.atlas-retro-button', rules, button_name))

        mapped, _ = map_component(
            ident,
            ['.atlas-retro-input'],
            field,
            variables=variables,
            readable=True,
            fallback_bg=variables.get('_surface_rgb'),
        )
        if mapped:
            blocks.append(mapped)

        if close:
            mapped, close_name = map_component(
                ident,
                ['.atlas-retro-window-close',
                 '.atlas-theme-titlebar > button[aria-label="Close tools and settings"]',
                 '.atlas-theme-close'],
                close,
                extra=TITLE_LAYOUT_PROPS,
                allow_size=True,
            )
            if mapped:
                blocks.append(mapped)
            if close_name in ('closebox', 'close-box'):
                blocks.append(
                    f'[data-atlas-theme="{ident}"] .atlas-retro-window-titlebar,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-titlebar {{\n'
                    f'  position: relative;\n  justify-content: center;\n}}\n'
                    f'[data-atlas-theme="{ident}"] .atlas-retro-window-close,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-titlebar > button[aria-label="Close tools and settings"],\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-close {{\n'
                    f'  position: absolute; left: 7px; top: 50%; transform: translateY(-50%);\n'
                    f'  z-index: 2; padding: 0; color: inherit;\n}}\n'
                    f'[data-atlas-theme="{ident}"] .atlas-retro-window-symbol,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-retro-window-glyphs,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-titlebar h2 svg,\n'
                    f'[data-atlas-theme="{ident}"] .atlas-theme-close svg {{\n'
                    f'  display: none;\n}}'
                )

        traffic = first_class_rule(rules, ('traffic',))
        dot = first_class_rule(rules, ('dot',))
        if traffic or dot:
            t = f'[data-atlas-theme="{ident}"]'
            if traffic:
                decls = filter_decls(traffic[2], extra=TITLE_LAYOUT_PROPS, allow_size=True)
                decls = [('display', 'flex')] + [d for d in decls if d[0] != 'display']
                blocks.append(emit_mapped(ident, ['.atlas-retro-traffic'], decls, 'from .traffic'))
            else:
                blocks.append(
                    f'{t} .atlas-retro-traffic {{ display: flex; gap: 8px; }}'
                )
            dot_decls = filter_decls(dot[2], extra=TITLE_LAYOUT_PROPS, allow_size=True) if dot else []
            if dot:
                blocks.append(emit_mapped(
                    ident, ['.atlas-retro-traffic-dot'], dot_decls, 'from .dot',
                ))
            colors = {}
            for color_name, atlas in (('red', 'close'), ('yellow', 'min'), ('green', 'max')):
                colored = first_class_rule(rules, (color_name,))
                # Prefer .dot.red if present
                specific = None
                for selector, decls in rules:
                    if re.search(r'\.dot\.' + color_name + r'\b', selector):
                        specific = decls
                        break
                decls = specific or (colored[2] if colored else [])
                filtered = filter_decls(decls, allow_size=True)
                if filtered:
                    colors[atlas] = filtered
                    blocks.append(emit_mapped(
                        ident, [f'.atlas-retro-traffic-{atlas}'],
                        filtered, f'from .dot.{color_name}',
                    ))
            # Only treat the dot as the window's close control when the demo
            # actually puts it in the caption (Aqua .traffic, BeOS .tab);
            # decorative dots elsewhere (Memphis, Zen) keep the old mapping.
            in_caption = re.search(r'class="(?:titlebar|traffic|tab)"[^>]*>(?:(?!</div>).)*class="dot',
                                   html, re.S)
            if in_caption and not colors:
                # A single .dot (BeOS close box), not a three-light cluster.
                blocks.append(
                    f'{t} .atlas-retro-traffic-min,\n{t} .atlas-retro-traffic-max {{\n'
                    f'  display: none;\n}}'
                )
            # The demo's close light IS the close control. Render Atlas's real
            # close button as that light (first in the bar) and hide the
            # decorative copy, instead of showing both it and an Atlas X.
            close_look = dot_decls + colors.get('close', [])
            if not in_caption:
                close_look = None
            close_sel = (f'{t} .atlas-retro-window-titlebar > .atlas-retro-window-close,\n'
                         f'{t} .atlas-theme-titlebar > .atlas-theme-close')
            if close_look is not None:
                blocks.append(
                    f'/* Atlas close rendered as the demo close light */\n'
                    f'{t} .atlas-retro-window-titlebar,\n{t} .atlas-theme-titlebar {{\n'
                    f'  justify-content: flex-start;\n}}\n'
                    f'{t} .atlas-retro-window-symbol,\n{t} .atlas-retro-window-glyphs,\n'
                    f'{t} .atlas-theme-titlebar h2 > [aria-hidden="true"],\n'
                    f'{t} .atlas-theme-titlebar h2 svg,\n{t} .atlas-theme-close svg {{\n'
                    f'  display: none;\n}}\n'
                    f'{t} .atlas-theme-titlebar .atlas-retro-traffic-close,\n'
                    f'{t} .atlas-retro-window-titlebar:has(> .atlas-retro-window-close) .atlas-retro-traffic-close {{\n'
                    f'  display: none;\n}}\n'
                    + (f'{t} .atlas-theme-titlebar .atlas-retro-traffic,\n'
                       f'{t} .atlas-retro-window-titlebar:has(> .atlas-retro-window-close) .atlas-retro-traffic {{\n'
                       f'  display: none;\n}}\n' if not colors else '')
                    + f'{close_sel} {{\n'
                    f'  order: -1;\n  flex-shrink: 0;\n  display: inline-block;\n'
                    f'  min-width: 0;\n  min-height: 0;\n  padding: 0;\n  border: 0;\n'
                    f'  font-size: 0;\n  line-height: 0;\n  color: transparent;\n'
                    f'{format_decls(close_look)}\n}}\n'
                    f'{t} .atlas-retro-window-titlebar > .atlas-retro-window-close:focus-visible,\n'
                    f'{t} .atlas-theme-titlebar > .atlas-theme-close:focus-visible {{\n'
                    f'  outline: 3px solid var(--atlas-focus-ring);\n  outline-offset: 2px;\n}}'
                )
            if close_look is not None and title_text and any(p_ == 'flex' for p_, _ in title_text[2]):
                # .title { flex: 1; text-align: center } centres the caption
                # in the space right of the lights.
                blocks.append(
                    f'{t} .atlas-retro-window-title,\n{t} .atlas-theme-titlebar h2 {{\n'
                    f'  flex: 1;\n  justify-content: center;\n}}'
                )

        # Conversation + header accents from original tokens / primary controls.
        surface = variables.get('_surface_rgb')
        bubble_bg = pick_accent_color(variables, titlebar, primary, surface)
        if ident == 'mac7':
            # System 7 buttons are paper, not the striped ink caption.
            bubble_bg = rgb(variables.get('paper', '#ffffff')) or (255, 255, 255)
        if bubble_bg:
            bubble_fg = high_contrast(bubble_bg)
            if contrast(bubble_fg, bubble_bg) < 4.55:
                bubble_fg = content_foreground(bubble_fg, [bubble_bg])
            blocks.append(
                f'[data-atlas-theme="{ident}"] {{\n'
                f'  --atlas-user-bubble-bg: {hexcolor(bubble_bg)};\n'
                f'  --atlas-user-bubble-fg: {hexcolor(bubble_fg)};\n'
                f'  --atlas-accent-fill-bg: {hexcolor(bubble_bg)};\n'
                f'  --atlas-accent-fill-fg: {hexcolor(bubble_fg)};\n}}'
            )
            button_like = primary and primary[0] and not primary[0].startswith(('button', 'input', 'textarea', 'status', 'titlebar', 'head', 'bar'))
            # Only copy original *button* declarations onto accent fills.
            if button_like and is_colorful(first_color_from_decls(primary[2], variables, ('background-color', 'background')) or (0,0,0), surface):
                mapped_primary, _ = map_component(
                    ident,
                    ['.atlas-theme-accent-fill',
                     '[data-testid="send-button"].atlas-theme-accent-fill'],
                    primary,
                )
                if mapped_primary:
                    blocks.append(mapped_primary)
            blocks.append(
                f'[data-atlas-theme="{ident}"] .user-message-bubble,\n'
                f'[data-atlas-theme="{ident}"] .atlas-theme-accent-fill {{\n'
                f'  background: {hexcolor(bubble_bg)};\n'
                f'  color: {hexcolor(bubble_fg)};\n}}'
            )
            accents[ident] = bubble_bg

        # Header uses demo menubar if present, otherwise chrome surface (not a hardcoded blue).
        menubar = first_class_rule(rules, ('menubar', 'menu-bar'))
        if menubar:
            mapped, _ = map_component(ident, ['.atlas-theme-header'], menubar,
                                      extra=TITLE_LAYOUT_PROPS)
            if mapped:
                blocks.append(mapped)
        elif titlebar and item['id'] in (1, 2, 3, 4, 5, 6, 7, 9, 19, 20):
            # OS themes: header is a chrome strip, not the desktop.
            pass

        if content:
            blocks.extend(content_chrome_blocks(ident, content, rules, variables))

        blocks = [b for b in blocks if b]
        surface = variables.get('_surface_rgb') or (255, 255, 255)
        joined = '\n'.join(blocks)
        def last_fill(needle):
            matches = list(re.finditer(
                r'([^{}]*' + re.escape(needle) + r'(?![\w-])[^{}]*)\{([^}]*)\}',
                joined))
            for sel, body in reversed([(m.group(1), m.group(2)) for m in matches]):
                if re.search(r':(?:hover|active|focus|focus-visible|disabled)\b', sel):
                    continue
                color = first_color_from_decls(parse_decls(body), variables,
                                               ('background-color', 'background'))
                if color:
                    return color
            return None

        def legal_ring(win, btn, preference=None):
            candidates = []
            if preference:
                try:
                    candidates.append(focus_color(preference, win, btn))
                except ValueError:
                    pass
            candidates.extend(((0, 0, 0), (255, 255, 255)))
            usable = [c for c in candidates
                      if contrast(c, win) >= 3.1 and contrast(c, btn) >= 3.1]
            if usable:
                return usable[0]
            return None

        win_bg = last_fill(f'[data-atlas-theme="{ident}"] .atlas-retro-window') or surface
        btn_bg = last_fill(f'[data-atlas-theme="{ident}"] .atlas-retro-button') or win_bg
        ring_src = demo_focus_color(rules, variables) or variables.get('_accent_rgb') or high_contrast(win_bg)
        ring = legal_ring(win_bg, btn_bg, ring_src)
        if ring is None:
            # A very saturated button on a contrasting window can make every
            # outline fail one of the two 3:1 checks. Walk the button fill
            # toward the window until a legal ring exists.
            for step in range(1, 86):
                candidate = blend(btn_bg, win_bg, step / 100)
                ring = legal_ring(win_bg, candidate, ring_src)
                if ring is not None:
                    fg = high_contrast(candidate)
                    if contrast(fg, candidate) < 4.5:
                        ring = None
                        continue
                    blocks.append(
                        f'[data-atlas-theme="{ident}"] .atlas-retro-button {{\n'
                        f'  background-color: {hexcolor(candidate)};\n'
                        f'  color: {hexcolor(fg)};\n}}'
                    )
                    btn_bg = candidate
                    break
        if ring is None:
            ring = max(((0, 0, 0), (255, 255, 255)),
                       key=lambda c: min(contrast(c, win_bg), contrast(c, btn_bg)))
        blocks.append(
            f'[data-atlas-theme="{ident}"] {{\n'
            f'  --atlas-focus-ring: {hexcolor(ring)};\n}}'
        )
        parts.append('\n'.join(blocks))
        surfaces.append('\n'.join(b for b in blocks if 'atlas-theme-desktop' in b or '--atlas-font-ui' in b))

    return '\n\n'.join(p for p in parts if p).rstrip() + '\n', accents


def build():
    doc = json.loads((VENDOR/'manifest.json').read_text())
    systems = doc['systems']
    assert doc['count'] == len(systems) == 53, 'Expected full upstream catalog'
    assert sorted(item['id'] for item in systems) == list(range(1, 54))
    ids = [entry_id(item) for item in systems]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r'[a-z0-9][a-z0-9-]*', i) for i in ids)
    for item in systems:
        demo = DEMOS / f"{item['slug']}.html"
        if not demo.exists():
            raise FileNotFoundError(f'Missing vendored demo for {item["slug"]}')

    scopes = ['/* Vendored source tokens, scoped to registered IDs, generated offline.',
              ' * Retro Design System / NovusGFX, MIT, upstream '+SHA,
              ' * See vendor/retro-design-system/LICENSE. No scripts or external assets.',
              ' */']
    adapters = ['/* Generated semantic Atlas adapters from the complete RDS manifest.',
                ' * Upstream '+SHA+' (MIT). Tokens drive gray ramps and AA contrast.',
                ' * Demo CSS is mapped separately in retroChrome.generated.css.',
                ' * Explicit legacy gray ramps (Win95, Mac7, CRT) live in theme-presets.css.',
                ' */']
    catalog = []
    variables_by_id = {}
    link_accents = {}

    for item, ident in zip(systems, ids):
        token_file = VENDOR/'source-tokens'/f"{item['slug']}.css"
        source = token_file.read_text()
        matched = SOURCE_PATTERN.fullmatch(source)
        if not matched or any(x in source.lower() for x in ('@import', '@font-face', 'url(', 'expression(', '</style')):
            raise ValueError(f'Unexpected CSS format or external asset in {token_file}')
        variables = dict((key, value.strip()) for key, value in TOKEN_PATTERN.findall(matched.group(2)))
        residual = TOKEN_PATTERN.sub('', matched.group(2)).strip()
        if residual:
            raise ValueError(f'Unexpected non-token CSS in {token_file}: {residual[:80]}')
        scopes.append(source.replace(':root {', f'[data-atlas-theme="{ident}"] {{', 1).rstrip())

        bg, surface, fg, accent, header, dark = source_colors(item, variables)
        if item['id'] == 2:
            # Win95 chrome and the preexisting Atlas light-mode adaptation are light.
            dark = False
        if item['id'] == 6:
            # NeXTSTEP desktop is dark; window chrome is light gray.
            dark = luminance(surface) < .21
        preview_bg = bg
        preview_surface = surface
        preview_accent = accent
        if item['id'] == 1:
            preview_bg, preview_surface, fg, preview_accent = rgb('#a8a8a8'), rgb('#dddddd'), rgb('#000000'), rgb('#000000')
        if item['id'] == 2:
            preview_bg, preview_surface, fg, preview_accent = rgb('#008080'), rgb('#c0c0c0'), rgb('#000000'), rgb('#000080')
        if item['id'] == 9:
            preview_bg, preview_surface, fg, preview_accent = rgb('#001a00'), rgb('#003300'), rgb('#aaffaa'), rgb('#33ff33')
        era = item.get('era') or 'Retro'
        year = item.get('year')
        desc = ', '.join(item.get('tags', [])[:4]) or era
        catalog.append({
            'id': ident, 'name': item['name'],
            'era': f'{era} · {year}' if year else era,
            'description': desc,
            'mode': 'dark' if dark else 'light', 'reference': item['slug'],
            'tags': item.get('tags', []),
            'preview': {'background': hexcolor(preview_bg),
                        'surface': hexcolor(preview_surface),
                        'foreground': hexcolor(fg), 'accent': hexcolor(preview_accent)},
        })
        variables['_accent_rgb'] = accent
        variables['_surface_rgb'] = surface
        variables_by_id[ident] = variables

        demo_css, demo_rules = load_demo(item)
        link = demo_link_color(demo_rules, variables)
        if link:
            link_accents[ident] = hexcolor(link)

        if item['id'] in LEGACY_IDS:
            continue
        control = blend(surface, high_contrast(surface), .07)
        border = blend(surface, high_contrast(surface), .45)
        header_foreground = high_contrast(header)
        demo_focus = demo_focus_color(demo_rules, variables) or accent
        radii = '0' if era in ('Terminal', 'Desktop OS', 'Technical') or item['id'] in (40, 47) else (
            '.7rem' if 'glass' in desc or 'clay' in desc else '.35rem')
        font = ('var(--font-sys, var(--font, Georgia, serif))' if item['id'] not in (40, 47)
                else ('"Times New Roman", Times, serif'))
        if item['id'] == 40:
            font = '"Times New Roman", Times, serif'
        lines = [
            f'[data-atlas-theme="{ident}"] {{',
            f'  color-scheme: {"dark" if dark else "light"};',
            f'  --atlas-font-ui: {font};',
            f'  --atlas-body-fg: {hexcolor(high_contrast(bg) if contrast(high_contrast(bg), bg) >= 4.55 else high_contrast(surface))};',
            f'  --atlas-retro-window-fg: {hexcolor(high_contrast(surface))};',
            f'  --atlas-radius: {radii};',
            f'  --atlas-accent: {hexcolor(accent)};',
            f'  --atlas-focus-ring: {hexcolor(focus_color(demo_focus, surface, control))};',
            f'  --atlas-retro-window-bg: {hexcolor(surface)};',
            f'  --atlas-retro-header-bg: {hexcolor(header if contrast(header, high_contrast(header)) >= 3 else surface)};',
            f'  --atlas-retro-header-fg: {hexcolor(header_foreground)};',
            f'  --atlas-retro-control-bg: {hexcolor(control)};',
            f'  --atlas-retro-control-fg: {hexcolor(high_contrast(control))};',
            f'  --atlas-retro-input-bg: {hexcolor(bg if contrast(high_contrast(bg), bg) >= 4.55 else surface)};',
            f'  --atlas-retro-input-fg: {hexcolor(high_contrast(bg) if contrast(high_contrast(bg), bg) >= 4.55 else high_contrast(surface))};',
            f'  --atlas-retro-control-border: {hexcolor(border)};',
        ]
        # Input/window surfaces should follow the window chrome, not the desktop.
        input_bg = surface if contrast(high_contrast(surface), surface) >= 4.55 else bg
        lines = [
            f'[data-atlas-theme="{ident}"] {{',
            f'  color-scheme: {"dark" if dark else "light"};',
            f'  --atlas-font-ui: {font};',
            f'  --atlas-body-fg: {hexcolor(high_contrast(surface))};',
            f'  --atlas-retro-window-fg: {hexcolor(high_contrast(surface))};',
            f'  --atlas-radius: {radii};',
            f'  --atlas-accent: {hexcolor(accent)};',
            f'  --atlas-focus-ring: {hexcolor(focus_color(demo_focus, surface, control))};',
            f'  --atlas-retro-window-bg: {hexcolor(surface)};',
            f'  --atlas-retro-header-bg: {hexcolor(header)};',
            f'  --atlas-retro-header-fg: {hexcolor(header_foreground)};',
            f'  --atlas-retro-control-bg: {hexcolor(control)};',
            f'  --atlas-retro-control-fg: {hexcolor(high_contrast(control))};',
            f'  --atlas-retro-input-bg: {hexcolor(input_bg)};',
            f'  --atlas-retro-input-fg: {hexcolor(high_contrast(input_bg))};',
            f'  --atlas-retro-control-border: {hexcolor(border)};',
        ]
        grays = {900: bg if dark else surface,
                 800: surface,
                 700: utility_surface(surface, .07, [text_shade(surface, 7.4), high_contrast(surface)]),
                 600: utility_surface(surface, .22, [high_contrast(surface)]),
                 500: text_shade(surface, 4.6), 400: text_shade(surface, 5.2),
                 300: text_shade(surface, 6.4), 200: text_shade(surface, 7.4),
                 100: text_shade(surface, 9.1), 50: high_contrast(surface)}
        if not dark:
            # Light themes: gray-900 is the deepest *surface* (window), desktop stays --bg.
            grays[900] = blend(surface, (0, 0, 0), .08) if luminance(surface) > luminance(bg) else surface
        lines.extend(f'  --gray-{key}: {css_rgb(value)};' for key, value in sorted(grays.items(), reverse=True))
        lines.append('}')
        adapters.append('\n'.join(lines))

    chrome_css, _accents = generate_chrome(systems, variables_by_id)

    catalog_file = ROOT/'retroCatalog.generated.js'
    registry_data = json.dumps(catalog, ensure_ascii=False, indent=2)
    catalog_js = (
        '// Generated by frontend/scripts/build-retro-catalog.py; do not edit.\n'
        '// Pinned NovusGFX Retro Design System '+SHA+' (MIT).\n'
        'export const RETRO_CATALOG = Object.freeze(\n'
        '  '+registry_data.replace('\n', '\n  ')+'.map(entry => Object.freeze({\n'
        '    ...entry, preview: Object.freeze(entry.preview), tags: Object.freeze(entry.tags),\n'
        '  }))\n'
        ')\n'
    )
    adapter_css = '\n\n'.join(adapters).rstrip()+'\n\n'+content_adapters(catalog, '\n\n'.join(adapters), link_accents)+'\n'
    return {
        catalog_file: catalog_js,
        VENDOR/'tokens.css': '\n\n'.join(scopes).rstrip()+'\n',
        ROOT/'retroCatalog.generated.css': adapter_css,
        ROOT.parents[1]/'public/theme-bootstrap.js': theme_bootstrap(catalog),
        ROOT/'retroChrome.generated.css': chrome_css,
    }


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--check', action='store_true', help='Fail if generated assets are stale')
    check = args.parse_args().check
    results = build()
    for path, content in results.items():
        if check:
            if not path.exists() or path.read_text() != content:
                print(f'STALE {path}', file=sys.stderr)
                sys.exit(1)
        else:
            path.write_text(content)
        print(('OK ' if check else 'WROTE ')+str(path.relative_to(ROOT.parents[1])))
    print('53 upstream designs + 2 Atlas defaults, mapped from vendored demo CSS')
