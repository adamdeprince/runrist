# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The mode line.

A mode line is built from *segments*: functions of ``(editor, window)`` that
return a list of items.  An item is ``(text, face)`` for plain text, or a
``Pill`` for text on a rounded badge.  ``left_segments`` are laid out from the
left edge and ``right_segments`` against the right; both are ordinary lists,
so adding a segment is appending a function::

    from runrist import modeline
    modeline.right_segments.insert(0, lambda ed, w: [(time.strftime('%H:%M'), 'mode-line-dim')])
"""

from . import faces
from .chars import clip, text_width
from .variables import defvar

defvar('mode_line_separators', 'round',
       "How badges in the mode line are capped: 'round', 'slant', 'arrow' or None.  The "
       "caps are powerline glyphs, which Silkie and kitty draw themselves.")

_CAPS = {'round': ('', ''), 'slant': ('', ''),
         'arrow': ('', '')}


class Pill:
    """Text shown on a badge in ``face``."""

    __slots__ = ('text', 'face')

    def __init__(self, text, face):
        self.text = text
        self.face = face


def state(ed, w):
    buf = w.buffer
    if buf.read_only:
        return [Pill('RO', 'mode-line-read-only')]
    if buf.modified and not buf.temporary and (buf.filename or not buf.name.startswith('*')):
        return [Pill('●', 'mode-line-modified')]
    return [Pill('◦', 'mode-line-state')]


def buffer_name(ed, w):
    items = [(' ' + w.buffer.name, 'mode-line-buffer-id')]
    if w.buffer.narrowed:
        items.append((' ‹narrow›', 'mode-line-dim'))
    return items


def position(ed, w):
    buf = w.buffer
    point = ed.window_point(w)
    line, _ = buf.text.pos_to_linecol(min(point, buf.size))
    column = buf.current_column(min(point, buf.size))
    total = max(1, buf.line_count)
    if not w.rows:
        where = ''
    else:
        shown = [row[0] for row in w.rows if row[0] >= 0]
        top = shown[0] if shown else 0
        bottom = shown[-1] if shown else 0
        if top == 0 and bottom >= total - 1:
            where = 'All'
        elif top == 0:
            where = 'Top'
        elif bottom >= total - 1:
            where = 'Bot'
        else:
            where = f'{100 * top // total}%'
    return [(f'   {line + 1}:{column}', 'mode-line-emphasis'), (f'  {where}', 'mode-line-dim')]


def recording(ed, w):
    items = []
    if ed.defining_macro is not None and w is ed.active_window():
        items.append(Pill('● REC', 'mode-line-recording'))
        items.append((' ', None))
    if ed.recursion_depth > ed.minibuffer_depth and w is ed.active_window():
        items.append((f'[{ed.recursion_depth - ed.minibuffer_depth}] ', 'mode-line-dim'))
    return items


def minor_modes(ed, w):
    lighters = [mode.lighter for mode in ed.active_minor_modes(w.buffer) if mode.lighter]
    return [(' '.join(lighters) + '  ', 'mode-line-dim')] if lighters else []


def major_mode(ed, w):
    mode = w.buffer.major_mode
    return [((mode.title if mode else 'Fundamental') + '  ', 'mode-line-mode')]


def encoding(ed, w):
    buf = w.buffer
    eol = {'\n': '', '\r\n': ' CRLF', '\r': ' CR'}.get(buf.eol_type, '')
    return [(f'{buf.encoding}{eol} ', 'mode-line-dim')]


left_segments = [state, buffer_name, position]
right_segments = [recording, minor_modes, major_mode, encoding]


def _runs(items, base, active, caps):
    runs = []
    for item in items:
        if isinstance(item, Pill):
            if not active:
                runs.append((base, f' {item.text} '))
                continue
            body = faces.style(item.face)
            if caps:
                cap = (body[1], *base[1:])          # the cap is drawn in the badge's color
                runs.append((cap, caps[0]))
                runs.append((body, item.text))
                runs.append((cap, caps[1]))
            else:
                runs.append((body, f' {item.text} '))
        else:
            text, face = item
            runs.append((faces.merge(base, face) if face and active else base, text))
    return runs


def render(ed, w, width):
    """Runs for the mode line of window ``w``, exactly ``width`` cells wide."""
    active = w is ed.active_window()
    base = faces.style('mode-line' if active else 'mode-line-inactive')
    caps = _CAPS.get(ed.var('mode_line_separators')) if ed.tty.caps.kitty_keyboard \
        or ed.tty.caps.graphics or ed.tty.is_fake else None
    left = [(base, ' ')]
    for segment in left_segments:
        left.extend(_runs(segment(ed, w), base, active, caps))
    right = []
    for segment in right_segments:
        right.extend(_runs(segment(ed, w), base, active, caps))
    used = sum(text_width(text) for _, text in left)
    room = width - used
    right_width = sum(text_width(text) for _, text in right)
    out = []
    if room >= right_width + 1:
        out = left + [(base, ' ' * (room - right_width))] + right
    else:
        cells = 0
        for style_, text in left:
            piece, taken = clip(text, width - cells)
            if piece:
                out.append((style_, piece))
            cells += taken
            if cells >= width:
                break
        if cells < width:
            out.append((base, ' ' * (width - cells)))
    return out
