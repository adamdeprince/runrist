# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""How characters occupy the screen, and the per-line cache record.

A line of printable ASCII is *simple*: character ``k`` is in column ``k``.
Anything else is measured a grapheme cluster at a time, so that a base
character and its combining marks, or the code points of an emoji sequence,
take the cells the terminal gives them.  Tabs advance to the next tab stop
counted from the start of the line.  Characters with no printable form are
shown as an escape, ``^A`` or ``\\u202e``.
"""

import re
from itertools import accumulate

from wcwidth import iter_graphemes, wcwidth, width as _width

_NOT_SIMPLE = re.compile(r'[^\x20-\x7e]')

# Characters that reorder or hide text.  Showing them as escapes keeps source
# code from reading differently than it parses.
_SHOWN_AS_ESCAPE = frozenset(map(chr, (
    0x061c, 0x200e, 0x200f, 0x2028, 0x2029, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e,
    0x2066, 0x2067, 0x2068, 0x2069, 0xfeff)))


def is_simple(s):
    """True when every character of ``s`` fills exactly one cell."""
    return _NOT_SIMPLE.search(s) is None


def escape_of(c):
    """The text shown in place of ``c``, or None if ``c`` is shown as itself."""
    o = ord(c)
    if o < 0x20:
        return None if c == '\t' else '^' + chr(o + 64)
    if o < 0x7f:
        return None
    if o == 0x7f:
        return '^?'
    if o < 0xa0:
        return f'\\x{o:02x}'
    if 0xd800 <= o <= 0xdfff:
        # A lone low surrogate is a byte that was not valid in the file's encoding.
        return f'\\x{o - 0xdc00:02x}' if 0xdc80 <= o <= 0xdcff else f'\\u{o:04x}'
    if c in _SHOWN_AS_ESCAPE or wcwidth(c) < 0:
        return f'\\u{o:04x}' if o <= 0xffff else f'\\U{o:08x}'
    return None


class LineInfo:
    """What has been worked out about one line of text.

    One of these sits in the line's metadata slot and is discarded when the
    line changes.  Each group of fields belongs to one subsystem, which also
    records what its values depended on so it can tell when they are stale.

    Layout: ``tabw`` is the tab width ``widths`` was measured with, or None if
    not measured.  ``widths`` is None for a simple line, else the cells each
    character takes; ``cols`` then gives the column each character starts in,
    with one more entry for the end of the line; ``escapes`` maps the index of
    each character shown as an escape to its text.

    Wrapping: ``breaks`` are the indexes at which the screen rows of the line
    start, for the width and mode in ``wrap_key``.

    Syntax: ``state_in`` and ``state_out`` are the scanner's state at the
    start and the end of the line, ``syntax`` the comment and string regions
    found, ``spans`` the faces, and ``gen`` the highlighter generation they
    were computed under.

    Minimap: ``mm`` is the row of palette indices and ``mm_key`` what it was
    drawn for.
    """

    __slots__ = ('tabw', 'widths', 'cols', 'escapes', 'wrap_key', 'breaks',
                 'state_in', 'state_out', 'syntax', 'spans', 'gen',
                 'mm', 'mm_key')

    def __init__(self):
        self.tabw = None
        self.widths = self.cols = self.escapes = None
        self.wrap_key = self.breaks = None
        self.state_in = self.state_out = None
        self.syntax = self.spans = None
        self.gen = -1
        self.mm = self.mm_key = None


def measure(info, line, tab_width):
    """Fill in the layout fields of ``info`` for ``line`` (without its newline)."""
    info.tabw = tab_width
    info.wrap_key = info.breaks = None
    if is_simple(line):
        info.widths = info.cols = info.escapes = None
        return info
    widths = []
    escapes = {}
    col = 0
    append = widths.append
    for cluster in iter_graphemes(line):
        c = cluster[0]
        if len(cluster) == 1:
            if c == '\t':
                w = tab_width - col % tab_width
            else:
                shown = escape_of(c)
                if shown is None:
                    w = max(0, wcwidth(c))
                else:
                    escapes[len(widths)] = shown
                    w = len(shown)
            append(w)
        else:
            w = max(0, _width(cluster))
            append(w)
            widths.extend([0] * (len(cluster) - 1))
        col += w
    info.widths = widths
    info.cols = [0, *accumulate(widths)]
    info.escapes = escapes
    return info


def text_width(s, tab_width=8, col=0):
    """Cells ``s`` takes when it starts in column ``col``."""
    if is_simple(s):
        return len(s)
    total = col
    for cluster in iter_graphemes(s):
        c = cluster[0]
        if len(cluster) > 1:
            total += max(0, _width(cluster))
        elif c == '\t':
            total += tab_width - total % tab_width
        else:
            shown = escape_of(c)
            total += max(0, wcwidth(c)) if shown is None else len(shown)
    return total - col


def clip(s, cells):
    """The longest prefix of ``s`` that fits in ``cells``, and the cells it takes."""
    if is_simple(s):
        s = s[:max(0, cells)]
        return s, len(s)
    used = 0
    end = 0
    for cluster in iter_graphemes(s):
        w = max(0, _width(cluster))
        if used + w > cells:
            break
        used += w
        end += len(cluster)
    return s[:end], used
