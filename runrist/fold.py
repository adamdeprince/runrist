# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Folds: hiding the body of a block behind its first line.

A fold is an overlay of kind ``fold`` running from the end of the block's
first line, its *head*, to the end of its last line.  The head stays on
screen with a badge after it; the lines after it are hidden from redisplay,
from the minimap and from line motion.  This module is the model.  The
commands are in ``runrist.commands.folding``.

What counts as a block is up to the major mode: a buffer may set
``fold_block_function`` to a function of ``(buffer, line)`` returning the last
line of the block headed by ``line`` or None.  Without one, a block is a line
followed by lines indented more deeply, which serves for most languages.
"""

import re
from bisect import bisect_right

from . import variables

variables.defvar('fold_block_function', None,
                 'Function of (buffer, line) giving the last line of the block that '
                 'starts on LINE, or None if no block starts there.', local=True)

_INDENT = re.compile(r'[ \t]*')


class Hidden:
    """Which lines of a buffer are folded away."""

    __slots__ = ('ranges', 'firsts')

    def __init__(self, ranges):
        self.ranges = ranges                    # [(first hidden, last hidden, overlay)], merged
        self.firsts = [r[0] for r in ranges]

    def __bool__(self):
        return bool(self.ranges)

    def _range(self, line):
        i = bisect_right(self.firsts, line) - 1
        if i >= 0 and self.ranges[i][1] >= line:
            return self.ranges[i]
        return None

    def is_hidden(self, line):
        return self._range(line) is not None

    def head_of(self, line):
        """The visible line whose fold hides ``line``; ``line`` itself if visible."""
        found = self._range(line)
        return found[0] - 1 if found else line

    def next(self, line):
        """The next visible line after ``line``."""
        found = self._range(line + 1)
        return found[1] + 1 if found else line + 1

    def prev(self, line):
        """The visible line before ``line``."""
        found = self._range(line - 1)
        return found[0] - 1 if found else line - 1

    def fold_headed_by(self, line):
        """``(first hidden, last hidden, overlay)`` of the fold whose head is ``line``."""
        i = bisect_right(self.firsts, line + 1) - 1
        if i >= 0 and self.ranges[i][0] == line + 1:
            return self.ranges[i]
        return None

    def visible_between(self, a, b):
        """How many visible lines there are in ``[a, b)``."""
        n = b - a
        for first, last, _ in self.ranges:
            lo, hi = max(first, a), min(last, b - 1)
            if lo <= hi:
                n -= hi - lo + 1
        return max(0, n)


_NOTHING = Hidden([])


def hidden(buffer):
    """The folded lines of ``buffer``."""
    key = (buffer.overlay_tick, buffer.modiff)
    cached = getattr(buffer, '_hidden', None)
    if cached is not None and cached[0] == key:
        return cached[1]
    found = []
    text = buffer.text
    for overlay in buffer.overlays:
        if overlay.props.get('kind') != 'fold':
            continue
        first = text.pos_to_line(overlay.start) + 1
        last = text.pos_to_line(overlay.end)
        if last >= first:
            found.append((first, last, overlay))
    if not found:
        result = _NOTHING
    else:
        found.sort(key=lambda r: (r[0], -r[1]))
        merged = []
        for item in found:
            if merged and item[0] <= merged[-1][1]:
                # Inside the fold before it, or overlapping it: one hidden range.
                before = merged[-1]
                merged[-1] = (before[0], max(before[1], item[1]), before[2])
            else:
                merged.append(item)
        result = Hidden(merged)
    buffer._hidden = (key, result)
    return result


def folds(buffer):
    return [o for o in buffer.overlays if o.props.get('kind') == 'fold']


def indentation(line):
    """Columns of leading whitespace, with tabs to multiples of eight; None for a blank line."""
    if not line.strip():
        return None
    return len(line[:_INDENT.match(line).end()].expandtabs(8))


def indent_block(buffer, line):
    """Last line of the block of more deeply indented lines under ``line``."""
    text = buffer.text
    depth = indentation(text.line(line))
    if depth is None:
        return None
    last = None
    for i, raw in enumerate(text.lines_raw(line + 1), line + 1):
        inner = indentation(raw)
        if inner is None:
            continue
        if inner <= depth:
            break
        last = i
    return last


def block_end(buffer, line):
    """Last line of the block headed by ``line``, or None if none starts there."""
    function = buffer.get('fold_block_function')
    if function is not None:
        return function(buffer, line)
    return indent_block(buffer, line)


def enclosing_block(buffer, line):
    """``(head, last)`` of the innermost block containing or headed by ``line``."""
    last = block_end(buffer, line)
    if last is not None:
        return line, last
    text = buffer.text
    depth = indentation(text.line(line))
    for head in range(line - 1, -1, -1):
        inner = indentation(text.line(head))
        if inner is None or (depth is not None and inner >= depth):
            continue
        last = block_end(buffer, head)
        if last is not None and last >= line:
            return head, last
        depth = inner
    return None


def fold_lines(buffer, head, last):
    """Fold lines ``head + 1`` to ``last`` behind ``head``."""
    if last <= head:
        return None
    text = buffer.text
    start, end = text.line_end(head), text.line_end(last)
    for overlay in folds(buffer):
        if overlay.start == start and overlay.end == end:
            return overlay
    return buffer.make_overlay(start, end, kind='fold', invisible=True, evaporate=True)


def fold_containing(buffer, pos):
    """The outermost fold hiding ``pos``, or headed by its line."""
    text = buffer.text
    line = text.pos_to_line(pos)
    state = hidden(buffer)
    found = state._range(line) or state.fold_headed_by(line)
    return found[2] if found else None


def unfold_at(buffer, pos):
    """Open every fold that hides ``pos``.  Returns whether any was open."""
    opened = False
    for overlay in folds(buffer):
        if overlay.start < pos <= overlay.end:
            overlay.delete()
            opened = True
    return opened


def unfold_all(buffer):
    n = 0
    for overlay in folds(buffer):
        overlay.delete()
        n += 1
    return n
