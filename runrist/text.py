# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Buffer text.

The text is a sequence of lines.  Every line but the last ends with its
newline; the last never does, and may be empty.  Lines are kept in blocks so
that an edit touches one short list however large the buffer is, and each
line has a metadata slot beside it which is cleared whenever that line's text
changes.  Highlighting, widths and the minimap keep their per-line caches
there, so inserting and deleting lines moves the caches with the text and a
changed line never shows a stale one.

Positions are zero-based code point offsets and ranges are half-open, as
with Python strings.

Nothing here limits the size of the text.  ``TARGET`` and ``LIMIT`` only say
how the lines are grouped.
"""

import re
from bisect import bisect_right
from itertools import accumulate, chain
from operator import attrgetter

from .errors import ArgsOutOfRange

TARGET = 256        # lines in a block made by a split
LIMIT = 512         # a block longer than this is split
SMALL = 64          # a block shorter than this joins a neighbour

_LINE = re.compile(r'[^\n]*\n|[^\n]+')
_NCHARS = attrgetter('nchars')
_LINES = attrgetter('lines')


def split_lines(s):
    """Split ``s`` into lines that keep their newline.

    The result always ends with a line that has none, empty if ``s`` is empty
    or ends with a newline.  Only ``\\n`` separates lines, which is why this is
    not ``str.splitlines``.
    """
    lines = _LINE.findall(s)
    if not lines or lines[-1].endswith('\n'):
        lines.append('')
    return lines


class _Block:
    __slots__ = ('lines', 'metas', 'nchars', '_ends')

    def __init__(self, lines, metas=None):
        self.lines = lines
        self.metas = [None] * len(lines) if metas is None else metas
        self.nchars = sum(map(len, lines))
        self._ends = None

    def ends(self):
        """Offsets, from the start of the block, just past each line."""
        ends = self._ends
        if ends is None:
            ends = self._ends = list(accumulate(map(len, self.lines)))
        return ends

    def changed(self):
        self._ends = None


class Text:
    """The characters of a buffer."""

    def __init__(self, s=''):
        self.version = 0
        self._snap = None
        self._snap_used = False
        self._load(split_lines(s))

    def _load(self, lines):
        self._blocks = [_Block(lines[i:i + TARGET]) for i in range(0, len(lines), TARGET)]
        self.length = sum(b.nchars for b in self._blocks)
        self._cum_chars = self._cum_lines = None

    # -- indexes ---------------------------------------------------------

    def _index(self):
        if self._cum_chars is None:
            self._cum_chars = list(accumulate(map(_NCHARS, self._blocks)))
            self._cum_lines = list(accumulate(map(len, map(_LINES, self._blocks))))
        return self._cum_chars, self._cum_lines

    def _locate(self, pos):
        """Block index, line index within it and column of ``pos``."""
        cum, _ = self._index()
        b = bisect_right(cum, pos)
        if b == len(cum):
            b -= 1
        off = pos - (cum[b - 1] if b else 0)
        ends = self._blocks[b].ends()
        j = bisect_right(ends, off)
        if j == len(ends):
            j -= 1
        return b, j, off - (ends[j - 1] if j else 0)

    def _locate_line(self, i):
        _, cum = self._index()
        if not 0 <= i < cum[-1]:
            raise ArgsOutOfRange(i)
        b = bisect_right(cum, i)
        return b, i - (cum[b - 1] if b else 0)

    def _check(self, pos):
        if not 0 <= pos <= self.length:
            raise ArgsOutOfRange(pos)

    # -- reading ---------------------------------------------------------

    def __len__(self):
        return self.length

    @property
    def line_count(self):
        return self._index()[1][-1]

    def line_raw(self, i):
        """Line ``i`` with its newline, if it has one."""
        b, j = self._locate_line(i)
        return self._blocks[b].lines[j]

    def line(self, i):
        """Line ``i`` without its newline."""
        s = self.line_raw(i)
        return s[:-1] if s.endswith('\n') else s

    def line_length(self, i):
        s = self.line_raw(i)
        return len(s) - 1 if s.endswith('\n') else len(s)

    def line_start(self, i):
        b, j = self._locate_line(i)
        cum, _ = self._index()
        ends = self._blocks[b].ends()
        return (cum[b - 1] if b else 0) + (ends[j - 1] if j else 0)

    def line_end(self, i):
        """Position after the last character of line ``i``, before its newline."""
        return self.line_start(i) + self.line_length(i)

    def pos_to_line(self, pos):
        self._check(pos)
        b, j, _ = self._locate(pos)
        _, cum = self._index()
        return (cum[b - 1] if b else 0) + j

    def pos_to_linecol(self, pos):
        self._check(pos)
        b, j, col = self._locate(pos)
        _, cum = self._index()
        return (cum[b - 1] if b else 0) + j, col

    def linecol_to_pos(self, line, col):
        """Position of column ``col`` of ``line``, kept within that line."""
        return self.line_start(line) + max(0, min(col, self.line_length(line)))

    def char_at(self, pos):
        """The character at ``pos``, or '' at the end of the text."""
        if not 0 <= pos < self.length:
            return ''
        b, j, col = self._locate(pos)
        return self._blocks[b].lines[j][col]

    def substring(self, a, b):
        self._check(a)
        self._check(b)
        if a >= b:
            return ''
        if self._snap is not None:
            self._snap_used = True
            return self._snap[a:b]
        ba, ja, ca = self._locate(a)
        bb, jb, cb = self._locate(b)
        first = self._blocks[ba].lines[ja]
        if ba == bb and ja == jb:
            return first[ca:cb]
        parts = [first[ca:]]
        if ba == bb:
            parts.extend(self._blocks[ba].lines[ja + 1:jb])
        else:
            parts.extend(self._blocks[ba].lines[ja + 1:])
            for k in range(ba + 1, bb):
                parts.extend(self._blocks[k].lines)
            parts.extend(self._blocks[bb].lines[:jb])
        parts.append(self._blocks[bb].lines[jb][:cb])
        return ''.join(parts)

    def snapshot(self):
        """All the text as one string.

        Joined when first asked for.  While searches and edits alternate the
        string is kept up to date by the edits, which costs a copy each; once
        an edit finds it was not read since the edit before, it is dropped, so
        plain typing does not pay for it.
        """
        if self._snap is None:
            self._snap = ''.join(chain.from_iterable(b.lines for b in self._blocks))
        self._snap_used = True
        return self._snap

    def __str__(self):
        return self.snapshot()

    def lines_raw(self, start=0, stop=None):
        """Iterate the lines from ``start`` to ``stop``, with their newlines."""
        count = self.line_count
        stop = count if stop is None else min(stop, count)
        if start >= stop:
            return
        b, j = self._locate_line(start)
        remaining = stop - start
        blocks = self._blocks
        while remaining > 0:
            lines = blocks[b].lines
            take = lines[j:j + remaining]
            yield from take
            remaining -= len(take)
            b += 1
            j = 0

    # -- per-line metadata -----------------------------------------------

    def slot(self, i):
        """``(lines, metas, j)`` for line ``i``: the two parallel lists of its
        block and its index in them."""
        b, j = self._locate_line(i)
        block = self._blocks[b]
        return block.lines, block.metas, j

    def meta(self, i):
        b, j = self._locate_line(i)
        return self._blocks[b].metas[j]

    def set_meta(self, i, value):
        b, j = self._locate_line(i)
        self._blocks[b].metas[j] = value

    def walk(self, start=0, stop=None):
        """Iterate ``(index, lines, metas, j)`` so a caller can read line ``j``
        of a block and assign its metadata slot without locating it again."""
        count = self.line_count
        stop = count if stop is None else min(stop, count)
        if start >= stop:
            return
        b, j = self._locate_line(start)
        blocks = self._blocks
        i = start
        while i < stop:
            block = blocks[b]
            lines, metas = block.lines, block.metas
            for j in range(j, min(len(lines), j + stop - i)):
                yield i, lines, metas, j
                i += 1
            b += 1
            j = 0

    def clear_metas(self):
        for block in self._blocks:
            block.metas = [None] * len(block.lines)

    # -- editing ---------------------------------------------------------

    def _edited(self, pos, removed, inserted):
        self.version += 1
        self._cum_chars = self._cum_lines = None
        if self._snap is not None:
            if self._snap_used:
                snap = self._snap
                self._snap = snap[:pos] + inserted + snap[pos + removed:]
                self._snap_used = False
            else:
                self._snap = None

    def insert(self, pos, s):
        self._check(pos)
        if not s:
            return
        b, j, col = self._locate(pos)
        block = self._blocks[b]
        line = block.lines[j]
        if '\n' not in s:
            block.lines[j] = line[:col] + s + line[col:]
            block.metas[j] = None
        else:
            new = _LINE.findall(line[:col] + s + line[col:])
            if not line.endswith('\n') and (not new or new[-1].endswith('\n')):
                new.append('')      # ``line`` was the last line, and still must not end in a newline
            block.lines[j:j + 1] = new
            block.metas[j:j + 1] = [None] * len(new)
        block.nchars += len(s)
        block.changed()
        self.length += len(s)
        if len(block.lines) > LIMIT:
            self._split(b)
        self._edited(pos, 0, s)

    def delete(self, a, b):
        """Remove ``[a, b)`` and return what was there."""
        self._check(a)
        self._check(b)
        if a >= b:
            return ''
        removed = self.substring(a, b)
        ba, ja, ca = self._locate(a)
        bb, jb, cb = self._locate(b)
        blocks = self._blocks
        first = blocks[ba]
        merged = first.lines[ja][:ca] + blocks[bb].lines[jb][cb:]
        if ba == bb:
            first.lines[ja:jb + 1] = [merged]
            first.metas[ja:jb + 1] = [None]
            first.nchars -= b - a
        else:
            last = blocks[bb]
            del first.lines[ja + 1:], first.metas[ja + 1:]
            first.lines[ja] = merged
            first.metas[ja] = None
            first.nchars = sum(map(len, first.lines))
            del last.lines[:jb + 1], last.metas[:jb + 1]
            last.nchars = sum(map(len, last.lines))
            last.changed()
            del blocks[ba + 1:bb if last.lines else bb + 1]
        first.changed()
        self.length -= b - a
        self._join(ba)
        self._edited(a, b - a, '')
        return removed

    def replace(self, a, b, s):
        removed = self.delete(a, b)
        self.insert(a, s)
        return removed

    def _split(self, b):
        block = self._blocks[b]
        lines, metas = block.lines, block.metas
        self._blocks[b:b + 1] = [_Block(lines[i:i + TARGET], metas[i:i + TARGET])
                                 for i in range(0, len(lines), TARGET)]

    def _join(self, b):
        blocks = self._blocks
        if len(blocks) < 2 or len(blocks[b].lines) >= SMALL:
            return
        other = b + 1 if b + 1 < len(blocks) else b - 1
        lo, hi = min(b, other), max(b, other)
        if len(blocks[lo].lines) + len(blocks[hi].lines) > LIMIT:
            return
        blocks[lo:hi + 1] = [_Block(blocks[lo].lines + blocks[hi].lines,
                                    blocks[lo].metas + blocks[hi].metas)]
