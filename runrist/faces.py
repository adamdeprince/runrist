# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Faces: how text looks.

A face is a named set of attributes.  Those it leaves out come from the faces
it inherits from, and in the end from ``default``:

    fg, bg          colors, ``#rrggbb``
    weight          100 to 900; ``bold=True`` means 700
    italic          bool
    underline       'line', 'double', 'curly', 'dotted' or 'dashed'
    ul_color        color of the underline
    strike, inverse bool
    font            a font role of the terminal, such as 'serif'
    inherit         a face name or a list of them

Text is drawn with a *style*: the attributes left after laying faces over one
another.  Styles are plain tuples and every step from face names to escape
sequence is cached; ``generation`` changes whenever a face does, and the
caches go with it.

Weights and font roles need Silkie's font protocol.  Without it a weight of
600 or more is drawn bold and roles are ignored.
"""

FIELDS = ('fg', 'bg', 'weight', 'italic', 'underline', 'ul_color', 'strike', 'inverse', 'font')
_INDEX = {name: i for i, name in enumerate(FIELDS)}
_UNDERLINES = {'line': 1, 'double': 2, 'curly': 3, 'dotted': 4, 'dashed': 5}


class Caps:
    """What the terminal can draw."""

    def __init__(self):
        self.truecolor = True
        self.styled_underline = True
        self.fonts = frozenset()        # font roles the terminal offers; empty without the protocol
        self.font_weights = False


caps = Caps()
generation = 0

_defaults = {}
_docs = {}
_specs = {}
_resolved = {}
_styles = {}
_merged = {}
_sgr = {}
themes = {}
current_theme = None


def _changed():
    global generation
    generation += 1
    _resolved.clear()
    _styles.clear()
    _merged.clear()
    _sgr.clear()


def refresh():
    """Forget cached styles, after the terminal's abilities change."""
    _changed()


def _normalize(attrs):
    attrs = dict(attrs)
    bold = attrs.pop('bold', None)
    if bold is not None and 'weight' not in attrs:
        attrs['weight'] = 700 if bold else 400
    if attrs.get('underline') is True:
        attrs['underline'] = 'line'
    return attrs


def defface(name, doc='', **attrs):
    """Define a face, or redefine its default look."""
    attrs = _normalize(attrs)
    _defaults[name] = attrs
    _docs[name] = doc
    theme = themes.get(current_theme)
    override = theme.faces.get(name) if theme else None
    _specs[name] = {**attrs, **override} if override else attrs
    _changed()
    return name


def set_face(name, **attrs):
    """Change attributes of a face now; None removes an attribute."""
    spec = dict(_specs.get(name, {}))
    for key, value in _normalize(attrs).items():
        if value is None:
            spec.pop(key, None)
        else:
            spec[key] = value
    _specs[name] = spec
    _changed()


def face_names():
    return sorted(_specs)


def face_doc(name):
    return _docs.get(name, '')


def resolve(name, _seen=()):
    """Attributes of face ``name`` with inheritance applied."""
    done = _resolved.get(name)
    if done is not None:
        return done
    spec = _specs.get(name)
    if spec is None:
        return {}
    out = {}
    parents = spec.get('inherit')
    if parents and name not in _seen:
        for parent in ((parents,) if isinstance(parents, str) else parents):
            out.update(resolve(parent, _seen + (name,)))
    out.update((k, v) for k, v in spec.items() if k != 'inherit')
    if not _seen:
        _resolved[name] = out
    return out


def style(*names):
    """Style of text drawn in ``names``, later ones laid over earlier ones,
    all over ``default``."""
    done = _styles.get(names)
    if done is not None:
        return done
    attrs = dict.fromkeys(FIELDS)
    attrs.update(resolve('default'))
    for name in names:
        if name:
            attrs.update(resolve(name))
    done = _styles[names] = tuple(attrs.get(field) for field in FIELDS)
    return done


def merge(base, name):
    """``base`` with face ``name`` laid over it."""
    key = (base, name)
    done = _merged.get(key)
    if done is None:
        if isinstance(name, tuple):             # several faces, the later over the earlier
            done = base
            for part in name:
                done = merge(done, part)
            _merged[key] = done
            return done
        attrs = resolve(name)
        if attrs:
            out = list(base)
            for field, value in attrs.items():
                index = _INDEX.get(field)
                if index is not None:
                    out[index] = value
            done = tuple(out)
        else:
            done = base
        _merged[key] = done
    return done


def attribute(name, field, default=None):
    """One attribute of a face, falling back to the default face."""
    value = resolve(name).get(field)
    if value is None:
        value = resolve('default').get(field, default)
    return default if value is None else value


# -- colors ------------------------------------------------------------------

def rgb(color):
    """``(r, g, b)`` of ``#rrggbb`` or ``#rgb``."""
    color = color.lstrip('#')
    if len(color) == 3:
        color = ''.join(c * 2 for c in color)
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def hexcolor(r, g, b):
    return f'#{max(0, min(255, round(r))):02x}{max(0, min(255, round(g))):02x}' \
           f'{max(0, min(255, round(b))):02x}'


def blend(a, b, t):
    """The color a fraction ``t`` of the way from ``a`` to ``b``."""
    ar, ag, ab = rgb(a)
    br, bg, bb = rgb(b)
    return hexcolor(ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t)


_CUBE = (0, 95, 135, 175, 215, 255)


def _to_256(color):
    r, g, b = rgb(color)

    def nearest(v):
        return min(range(6), key=lambda i: abs(_CUBE[i] - v))

    ri, gi, bi = nearest(r), nearest(g), nearest(b)
    cube = (_CUBE[ri], _CUBE[gi], _CUBE[bi])
    gray_level = round((r + g + b) / 3)
    gi_index = max(0, min(23, round((gray_level - 8) / 10)))
    gray = 8 + 10 * gi_index

    def distance(c):
        return (c[0] - r) ** 2 + (c[1] - g) ** 2 + (c[2] - b) ** 2

    if distance((gray, gray, gray)) < distance(cube):
        return 232 + gi_index
    return 16 + 36 * ri + 6 * gi + bi


def _color(color, base):
    if color is None:
        return str(base + 9)
    if caps.truecolor:
        r, g, b = rgb(color)
        return f'{base + 8};2;{r};{g};{b}'
    return f'{base + 8};5;{_to_256(color)}'


def sgr(style_):
    """The escape sequence that selects a style, from any previous state."""
    done = _sgr.get(style_)
    if done is not None:
        return done
    fg, bg, weight, italic, underline, ul_color, strike, inverse, font = style_
    parts = ['0', _color(fg, 30), _color(bg, 40)]
    use_fonts = bool(caps.fonts)
    if weight is not None and weight >= 600 and not (use_fonts and caps.font_weights):
        parts.append('1')
    if italic:
        parts.append('3')
    if underline:
        if caps.styled_underline:
            parts.append(f'4:{_UNDERLINES.get(underline, 1)}')
            if ul_color:
                r, g, b = rgb(ul_color)
                parts.append(f'58:2::{r}:{g}:{b}')
        else:
            parts.append('4')
    if strike:
        parts.append('9')
    if inverse:
        parts.append('7')
    out = '\x1b[' + ';'.join(parts) + 'm'
    if use_fonts:
        fields = []
        if font and font in caps.fonts:
            fields.append(f'role={font}')
        if caps.font_weights and weight is not None and weight != 400:
            fields.append(f'weight={max(100, min(900, int(weight)))}')
        if fields:
            out += '\x1b]777;font;v=1:op=select:' + ':'.join(fields) + '\x1b\\'
    _sgr[style_] = out
    return out


# -- themes ------------------------------------------------------------------

class Theme:
    def __init__(self, name, faces, doc='', dark=True):
        self.name = name
        self.faces = {face: _normalize(attrs) for face, attrs in faces.items()}
        self.doc = doc
        self.dark = dark


def deftheme(name, faces, doc='', dark=True):
    """Define a theme: face attributes that replace the defaults while it is loaded."""
    themes[name] = Theme(name, faces, doc, dark)
    if current_theme == name:
        load_theme(name)
    return name


def load_theme(name):
    global current_theme
    if name is not None and name not in themes:
        raise KeyError(name)
    current_theme = name
    overrides = themes[name].faces if name else {}
    _specs.clear()
    for face, attrs in _defaults.items():
        _specs[face] = {**attrs, **overrides[face]} if face in overrides else attrs
    for face, attrs in overrides.items():
        _specs.setdefault(face, attrs)
    _changed()
