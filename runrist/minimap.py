# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The minimap: the buffer from far away.

With ``minimap-mode`` on, a strip down the right of a window shows the buffer
zoomed out until every character is a dot the color of its syntax
highlighting, so the shape of the code -- its indentation, its comments, its
long lines -- can be seen whole.  A band marks the part the window shows and
a line marks point.  Clicking or dragging in the strip moves the window
there.

The strip is one image, drawn with the kitty graphics protocol.  Each line of
text is first reduced to a row of small numbers, one per character: zero for
blank, otherwise the number of the character's face.  The row is kept beside
the line (see ``LineInfo``), so after an edit only the changed line is
reduced again.  On a terminal with Silkie's palette images those numbers are
sent as they are, with a table giving each face's color; elsewhere they are
expanded to RGBA first.

The image has a pixel for every pixel of the strip, so it is as tall as the
window and holds as many lines as fit; a longer buffer scrolls through it in
proportion.  Nothing about the buffer's length is fixed.  The palette formats
have 256 slots, which the two shades of each face share, so at most 126 faces
get colors of their own in one strip; any beyond that are drawn in the
default color.
"""

import base64
import zlib

from . import display, faces, fold, fontlock
from .command import command, defalias
from .editor import ed
from .variables import defvar

defvar('minimap_char_width', None,
       'Pixels across for each character in the minimap; None chooses from the cell size.')
defvar('minimap_line_height', None,
       'Pixels down for each line in the minimap; None is twice the character width.')
defvar('minimap_line_gap', 1,
       'Blank pixel rows left under each line of the minimap, when lines are tall enough.')
defvar('minimap_viewport_alpha', 30,
       'Opacity, 0 to 255, of the band marking the visible part of the buffer.')
defvar('minimap_point_alpha', 150, 'Opacity, 0 to 255, of the line marking point.')

_SLOTS = 127                    # faces per shade; see the module documentation
_BASE_ID = 0x52520000           # image ids used by the editor: 'RR' and a window number
_BLANK = bytes.maketrans(bytes(range(256)), bytes(1 for _ in range(256)))
_SPACES = bytearray(_BLANK)
for _char in b' \t\r\n\f\v':
    _SPACES[_char] = 0
_SPACES = bytes(_SPACES)

_states = {}                    # window -> _Strip
_serial = [0]
_face_tables = {}


class _Palette:
    """Numbers for faces, and the colors they stand for."""

    def __init__(self):
        self.generation = -1
        self.numbers = {}
        self.colors = []

    def number(self, face):
        """The number of a face.  A face keeps its number until the theme changes,
        so rows made earlier stay right as more faces are met."""
        if self.generation != faces.generation:
            self.generation = faces.generation
            self.numbers = {}
            self.colors = [None, faces.attribute('default', 'fg')]
        if isinstance(face, tuple):
            face = face[-1]
        found = self.numbers.get(face)
        if found is None:
            color = faces.resolve(face).get('fg')
            if color is None or len(self.colors) >= _SLOTS:
                found = 1
            else:
                found = len(self.colors)
                self.colors.append(color)
            self.numbers[face] = found
        return found

    def table(self):
        """The 256 RGBA slots: each face plain, then each face inside the band."""
        self.number('default')
        band = faces.attribute('minimap-viewport', 'bg') or '#ffffff'
        alpha = max(0, min(255, int(ed.var('minimap_viewport_alpha'))))
        point = faces.attribute('minimap-cursor-line', 'bg') or band
        point_alpha = max(0, min(255, int(ed.var('minimap_point_alpha'))))
        out = bytearray(1024)
        for index, color in enumerate(self.colors[:_SLOTS]):
            if color is not None:
                out[index * 4:index * 4 + 4] = bytes((*faces.rgb(color), 255))
                lit = faces.blend(color, band, alpha / 255 * 0.6)
                out[(index + 128) * 4:(index + 128) * 4 + 4] = bytes((*faces.rgb(lit), 255))
        out[128 * 4:128 * 4 + 4] = bytes((*faces.rgb(band), alpha))
        out[127 * 4:127 * 4 + 4] = bytes((*faces.rgb(point), point_alpha))
        return bytes(out)


_palette = _Palette()
_IN_BAND = bytes((i + 128 if i < 127 else i) for i in range(256))
_POINT = bytes((127 if i in (0, 128) else i) for i in range(256))


def _face_table(number):
    table = _face_tables.get(number)
    if table is None:
        table = _face_tables[number] = bytes([0] + [number] * 255)
    return table


def line_row(buffer, line, width):
    """The row of face numbers for a line: one byte per character, ``width`` long."""
    lines, metas, j = buffer.text.slot(line)
    info = metas[j]
    key = (width, faces.generation)
    if info is not None and info.mm is not None and info.mm_key == key:
        return info.mm
    info = buffer.line_info(line)
    if info.spans is None:
        fontlock.fontify(buffer, line, line)
    raw = lines[j]
    text = raw[:-1] if raw.endswith('\n') else raw
    cols = info.cols
    if cols is None:
        shown = text[:width]
        row = bytearray(shown.encode('ascii', 'replace').translate(_SPACES))
    else:
        row = bytearray(min(width, cols[-1]))
        for k, char in enumerate(text):
            if cols[k] >= width:
                break
            if not char.isspace():
                row[cols[k]:min(width, cols[k + 1])] = b'\x01' * (
                    min(width, cols[k + 1]) - cols[k])
    for start, end, face in info.spans or ():
        if cols is not None:
            start, end = cols[start], cols[end]
        if start >= len(row):
            break
        number = _palette.number(face)
        if number != 1:
            end = min(end, len(row))
            row[start:end] = row[start:end].translate(_face_table(number))
    if len(row) < width:
        row.extend(bytes(width - len(row)))
    info.mm = bytes(row)
    info.mm_key = key
    return info.mm


class _Strip:
    def __init__(self):
        _serial[0] += 1
        self.image = _BASE_ID + _serial[0]
        self.key = None
        self.top = 0
        self.lines = 0
        self.scale = (1, 2)
        self.rect = None


def _scale(cell):
    width = ed.var('minimap_char_width')
    if not width:
        width = max(1, round(cell[0] / 7))
    height = ed.var('minimap_line_height') or 2 * width
    return max(1, int(width)), max(1, int(height))


def _ordinal(hidden, line):
    """How many visible lines come before ``line``."""
    return hidden.visible_between(0, line) if hidden else line


def _line_at(hidden, ordinal, count):
    """The line that is the ``ordinal``-th visible one."""
    if not hidden:
        return ordinal
    line = ordinal
    for first, last, _ in hidden.ranges:
        if first > line:
            break
        line += last - first + 1
    return min(line, count)


def _build(window, strip, cell):
    """The image for a window's strip: ``(key, width, height, pixels)``."""
    buffer = window.buffer
    text = buffer.text
    pw, ph = strip.scale = _scale(cell)
    width = window.minimap_w * cell[0]
    height = window.body_h * cell[1]
    chars = max(1, width // pw)
    fit = max(1, height // ph)
    hidden = fold.hidden(buffer)
    count = text.line_count
    visible = count - (sum(r[1] - r[0] + 1 for r in hidden.ranges) if hidden else 0)
    shown = [row[0] for row in window.rows if row[0] >= 0]
    first = shown[0] if shown else 0
    last = shown[-1] if shown else 0
    first_ordinal, last_ordinal = _ordinal(hidden, first), _ordinal(hidden, last)
    span = max(1, last_ordinal - first_ordinal + 1)
    if visible <= fit:
        top = 0
    else:
        top = round(first_ordinal / max(1, visible - span) * (visible - fit))
        top = max(0, min(top, visible - fit))
    strip.top, strip.lines = top, fit
    point_line = text.pos_to_line(min(ed.window_point(window), buffer.size))
    point_ordinal = _ordinal(hidden, hidden.head_of(point_line) if hidden else point_line)
    key = (id(buffer), buffer.modiff, text.version, top, first_ordinal, last_ordinal,
           point_ordinal, width, height, pw, ph, faces.generation,
           buffer.fontlock_generation, ed.var('minimap_viewport_alpha'))
    if key == strip.key:
        return None
    start_line = _line_at(hidden, top, count - 1)
    end_line = _line_at(hidden, min(visible, top + fit) - 1, count - 1) if visible else 0
    fontlock.fontify(buffer, start_line, end_line)
    gap = int(ed.var('minimap_line_gap'))
    gap = gap if ph - gap >= 1 and ph >= 3 else 0
    blank = bytes(width)
    pad = bytes(width - chars * pw)
    rows = []
    line = start_line
    for ordinal in range(top, min(visible, top + fit)):
        row = line_row(buffer, line, chars)
        if ordinal == point_ordinal:
            row = row.translate(_POINT)
        elif first_ordinal <= ordinal <= last_ordinal:
            row = row.translate(_IN_BAND)
        if pw > 1:
            wide = bytearray(len(row) * pw)
            for k in range(pw):
                wide[k::pw] = row
            row = bytes(wide)
        row += pad
        rows.append(row * (ph - gap))
        if gap:
            inside = first_ordinal <= ordinal < last_ordinal
            rows.append((b'\x80' * width if inside else blank) * gap)
        line = hidden.next(line) if hidden else line + 1
    pixels = b''.join(rows)
    if len(pixels) < width * height:
        pixels += bytes(width * height - len(pixels))
    strip.key = key
    return width, height, pixels


def _to_rgba(pixels, table):
    out = bytearray(len(pixels) * 4)
    for channel in range(4):
        out[channel::4] = pixels.translate(table[channel::4])
    return bytes(out)


def _transmit(image, width, height, columns, rows, pixels, table):
    """Graphics protocol commands that show an image over a rectangle of cells."""
    if ed.tty.caps.palette_images:
        payload, fmt = table + pixels, 408
    else:
        payload, fmt = _to_rgba(pixels, table), 32
    data = base64.standard_b64encode(zlib.compress(payload, 1)).decode('ascii')
    # q=1: the terminal stays quiet on success and reports failures, which are logged.
    head = f'a=T,f={fmt},o=z,i={image},p=1,s={width},v={height},c={columns},r={rows},C=1,q=1'
    out = []
    # 4096 base64 characters to a packet is the protocol's limit, not ours.
    for at in range(0, len(data), 4096):
        more = 1 if at + 4096 < len(data) else 0
        out.append(f'\x1b_G{head},m={more};{data[at:at + 4096]}\x1b\\')
        head = 'q=1'
    return ''.join(out)


def draw(_display, out, full):
    """Redisplay hook: bring the strips of all windows up to date."""
    tty = ed.tty
    caps = tty.caps
    live = set()
    if caps.graphics and caps.cell:
        for window in ed.frame.windows():
            if not window.minimap_w or window.body_h <= 0:
                continue
            live.add(window)
            strip = _states.get(window)
            if strip is None:
                strip = _states[window] = _Strip()
            rect = (window.x + window.w - window.minimap_w, window.y, window.minimap_w,
                    window.body_h)
            if full or rect != strip.rect:
                strip.key = None
            strip.rect = rect
            built = _build(window, strip, caps.cell)
            if built is None:
                continue
            width, height, pixels = built
            out.append(tty.move(rect[1], rect[0]))
            out.append(_transmit(strip.image, width, height, rect[2], rect[3], pixels,
                                 _palette.table()))
    for window in list(_states):
        if window not in live:
            out.append(f'\x1b_Ga=d,d=I,i={_states.pop(window).image},q=2\x1b\\')


def available():
    caps = ed.tty.caps
    return bool(caps.graphics and caps.cell)


def line_for_row(window, y):
    """The buffer line shown at screen row ``y`` of a window's strip."""
    strip = _states.get(window)
    cell = ed.tty.caps.cell
    if strip is None or not cell:
        return None
    buffer = window.buffer
    hidden = fold.hidden(buffer)
    pixel = (y - window.y) * cell[1] + cell[1] // 2
    ordinal = strip.top + pixel // strip.scale[1]
    return max(0, min(_line_at(hidden, ordinal, buffer.text.line_count - 1),
                      buffer.text.line_count - 1))


def mouse_drag(window, event):
    """Move the window to where the mouse is in its strip, and follow it while held."""
    if window is not ed.selected_window and not window.is_minibuffer:
        ed.select_window(window)
    buffer = window.buffer
    while True:
        line = line_for_row(window, event.y)
        if line is not None:
            buffer.goto_char(buffer.text.line_start(line))
            window.recenter = max(0, window.body_h // 2)
        event = ed.read_event(record=False)
        if event.key != '<mouse-movement>':
            if not event.is_mouse:
                ed.unread.appendleft(event)
            return


@command('P')
def minimap_mode(arg=None):
    """Toggle the minimap in this buffer: the text zoomed out, one dot to a character."""
    buffer = ed.current_buffer
    on = not buffer.get('minimap_mode') if arg is None else ed.prefix_numeric_value(arg) > 0
    buffer.set('minimap_mode', on)
    buffer.modiff += 1
    if on and not available():
        ed.message('Minimap is on, but this terminal has no graphics protocol to draw it with',
                   log=False)
    else:
        ed.message('Minimap ' + ('enabled' if on else 'disabled') + ' in this buffer', log=False)


@command('P')
def global_minimap_mode(arg=None):
    """Toggle the minimap in every buffer that has not chosen for itself."""
    from . import variables
    on = not variables.default_value('minimap_mode') if arg is None \
        else ed.prefix_numeric_value(arg) > 0
    variables.set_default('minimap_mode', on)
    ed.display.invalidate()
    ed.message('Minimap ' + ('enabled' if on else 'disabled') + ' everywhere', log=False)


def _graphics_reply(report):
    """Note a failure the terminal reports about one of the editor's images."""
    payload = getattr(report, 'payload', '') or ''
    if payload and payload != 'OK':
        problem = f'The terminal refused a minimap image: {payload}'
        if getattr(ed, '_minimap_problem', None) != problem:
            ed._minimap_problem = problem
            ed.message(problem)


ed.protocol_handlers['graphics'] = _graphics_reply

defalias('zoom-out-mode', 'minimap-mode')

if draw not in display.after_draw:
    display.after_draw.append(draw)
