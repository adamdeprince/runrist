# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Buffers.

A buffer is text with a point, a mark, markers that follow the text as it
changes, a record of changes that can be undone, overlays, and values of its
own for editor variables.  Everything that changes text goes through
``insert_at`` and ``delete_region``, which is where markers move, undo is
recorded and listeners hear of it.
"""

import itertools
import re
import weakref
from bisect import bisect_right
from contextlib import contextmanager

from . import variables
from .chars import LineInfo, measure
from .errors import (ArgsOutOfRange, BeginningOfBuffer, BufferReadOnly, EditorError,
                     EndOfBuffer, SearchFailed)
from .text import Text

_state_ids = itertools.count(1)

# While true, read-only buffers accept changes.  See ``inhibit_read_only()``.
_inhibit_read_only = 0


@contextmanager
def inhibit_read_only():
    global _inhibit_read_only
    _inhibit_read_only += 1
    try:
        yield
    finally:
        _inhibit_read_only -= 1


class Marker:
    """A position that stays with its text.

    Text inserted exactly at a marker goes after it, unless ``insertion_type``
    is true, in which case the marker moves past the insertion.
    """

    __slots__ = ('buffer', 'pos', 'insertion_type', '__weakref__')

    def __init__(self, buffer, pos, insertion_type=False):
        self.buffer = buffer
        self.pos = max(0, min(int(pos), len(buffer.text)))
        self.insertion_type = insertion_type
        buffer._markers.add(self)

    def set(self, pos):
        self.pos = max(0, min(int(pos), len(self.buffer.text)))
        return self

    def delete(self):
        self.buffer._markers.discard(self)

    def __index__(self):
        return self.pos

    __int__ = __index__

    def __repr__(self):
        return f'<Marker {self.pos} in {self.buffer.name}>'


class Overlay:
    """A range of a buffer carrying properties such as ``face`` or ``invisible``.

    Higher ``priority`` wins where overlays overlap.  An overlay with
    ``evaporate`` set deletes itself once it is empty.
    """

    __slots__ = ('buffer', '_start', '_end', 'props')

    def __init__(self, buffer, start, end, *, front_advance=False, rear_advance=False, **props):
        self.buffer = buffer
        start, end = sorted((int(start), int(end)))
        self._start = Marker(buffer, start, front_advance)
        self._end = Marker(buffer, end, rear_advance)
        self.props = props
        buffer.overlays.append(self)
        buffer.overlay_tick += 1

    @property
    def start(self):
        return self._start.pos

    @property
    def end(self):
        return self._end.pos

    def move(self, start, end):
        start, end = sorted((int(start), int(end)))
        self._start.set(start)
        self._end.set(end)
        self.buffer.overlay_tick += 1

    def get(self, name, default=None):
        return self.props.get(name, default)

    def put(self, name, value):
        self.props[name] = value
        self.buffer.overlay_tick += 1

    def delete(self):
        try:
            self.buffer.overlays.remove(self)
        except ValueError:
            return
        self._start.delete()
        self._end.delete()
        self.buffer.overlay_tick += 1

    def __repr__(self):
        return f'<Overlay {self.start}-{self.end} {self.props!r}>'


class UndoGroup:
    """The changes one command made, oldest first.

    ``kind`` is 'normal', or 'undo' or 'redo' for a group made by undoing the
    group at index ``ref``.  ``before`` and ``after`` identify the contents of
    the buffer on either side of the group; undoing a group brings back the
    contents named by its ``before``, which is how the buffer knows it is
    unmodified again after enough undoing.
    """

    __slots__ = ('entries', 'kind', 'ref', 'before', 'after', 'point')

    def __init__(self, kind, ref, before, after, point):
        self.entries = []
        self.kind = kind
        self.ref = ref
        self.before = before
        self.after = after
        self.point = point


_BLANK = re.compile(r'[ \t]*')

variables.defvar('tab_width', 8, 'Distance between tab stops, in columns.', local=True)
variables.defvar('indent_tabs_mode', False,
                 'Whether indentation may use tab characters.', local=True)
variables.defvar('mark_ring_max', 16,
                 'How many earlier marks a buffer remembers; None keeps them all.')


class Buffer:
    """Text being edited."""

    def __init__(self, name, text=''):
        self.name = name
        self.text = Text(text)
        self.pt = 0
        self._markers = weakref.WeakSet()
        self._narrow = None
        self.mark = None
        self.mark_active = False
        self.mark_ring = []
        self.overlays = []
        self.overlay_tick = 0
        self.locals = {}
        self.read_only = False
        self.modiff = 0
        self.after_change = []          # called as f(buffer, start, end, old_length)
        self.before_change = []         # called as f(buffer, start, end)

        self.undo_enabled = True
        self._groups = []
        self._open = None
        self._pending = None
        self._generating = None         # (kind, ref, after) while an undo is being applied
        self._state = next(_state_ids)
        self._saved_state = self._state

        self.filename = None
        self.encoding = 'utf-8'
        self.eol_type = '\n'
        self.bom = False
        self.file_mtime = None
        self.auto_save_path = None
        self.auto_saved_state = None
        self.backed_up = False
        self.directory = None           # default directory for commands run here

        self.major_mode = None
        self.minor_modes = []
        self.keymap = None              # the major mode's keymap
        self.syntax = None              # the Syntax of the buffer's mode, if it has one
        self.syntax_valid = 0           # lines before this have their scanner state known
        self.fontlock_generation = -1
        self.temporary = name.startswith(' ')
        self.minibuffer = False
        self.live = True                # false once killed

    def __repr__(self):
        return f'<Buffer {self.name}>'

    # -- variables ---------------------------------------------------------

    def get(self, name):
        """Value of variable ``name`` in this buffer."""
        return variables.get(name, self)

    def set(self, name, value):
        variables.set_local(name, value, self)

    # -- extent ------------------------------------------------------------

    @property
    def size(self):
        return len(self.text)

    @property
    def point_min(self):
        return self._narrow[0].pos if self._narrow else 0

    @property
    def point_max(self):
        return self._narrow[1].pos if self._narrow else len(self.text)

    def narrow(self, start, end):
        start, end = sorted((self._clamp_all(start), self._clamp_all(end)))
        self._narrow = (Marker(self, start), Marker(self, end, True))
        self.pt = max(start, min(self.pt, end))
        self.modiff += 1

    def widen(self):
        if self._narrow:
            for marker in self._narrow:
                marker.delete()
            self._narrow = None
            self.modiff += 1

    @property
    def narrowed(self):
        return self._narrow is not None

    @contextmanager
    def save_restriction(self):
        saved = None
        if self._narrow:
            saved = (Marker(self, self._narrow[0].pos), Marker(self, self._narrow[1].pos, True))
        try:
            yield
        finally:
            self.widen()
            if saved:
                self._narrow = saved
                self.pt = max(saved[0].pos, min(self.pt, saved[1].pos))

    def _clamp_all(self, pos):
        return max(0, min(int(pos), len(self.text)))

    def clamp(self, pos):
        return max(self.point_min, min(int(pos), self.point_max))

    # -- point -------------------------------------------------------------

    def goto_char(self, pos):
        self.pt = self.clamp(pos)
        return self.pt

    def bobp(self):
        return self.pt <= self.point_min

    def eobp(self):
        return self.pt >= self.point_max

    def bolp(self):
        return self.pt <= self.point_min or self.text.char_at(self.pt - 1) == '\n'

    def eolp(self):
        return self.pt >= self.point_max or self.text.char_at(self.pt) == '\n'

    def char_after(self, pos=None):
        pos = self.pt if pos is None else pos
        return self.text.char_at(pos) if self.point_min <= pos < self.point_max else ''

    def char_before(self, pos=None):
        pos = self.pt if pos is None else pos
        return self.text.char_at(pos - 1) if self.point_min < pos <= self.point_max else ''

    def substring(self, start, end):
        start, end = sorted((self._clamp_all(start), self._clamp_all(end)))
        return self.text.substring(start, end)

    def string(self):
        """The accessible text."""
        return self.text.substring(self.point_min, self.point_max)

    @contextmanager
    def save_excursion(self):
        """Restore point afterwards, following the text it was in."""
        saved = Marker(self, self.pt)
        try:
            yield
        finally:
            self.pt = self.clamp(saved.pos)
            saved.delete()

    # -- lines -------------------------------------------------------------

    def line_at(self, pos=None):
        """Index of the line containing ``pos``."""
        return self.text.pos_to_line(self.pt if pos is None else self._clamp_all(pos))

    @property
    def line_count(self):
        return self.text.line_count

    def line_text(self, i):
        return self.text.line(i)

    def bol(self, pos=None):
        pos = self.pt if pos is None else self._clamp_all(pos)
        return max(self.point_min, self.text.line_start(self.text.pos_to_line(pos)))

    def eol(self, pos=None):
        pos = self.pt if pos is None else self._clamp_all(pos)
        return min(self.point_max, self.text.line_end(self.text.pos_to_line(pos)))

    def line_beginning_position(self, n=1):
        line = max(0, min(self.line_at() + n - 1, self.text.line_count - 1))
        return max(self.point_min, self.text.line_start(line))

    def line_end_position(self, n=1):
        line = max(0, min(self.line_at() + n - 1, self.text.line_count - 1))
        return min(self.point_max, self.text.line_end(line))

    def forward_line(self, n=1):
        """Move to the start of the line ``n`` lines on; return how many of
        those lines could not be moved over."""
        text = self.text
        first = text.pos_to_line(self.point_min)
        last = text.pos_to_line(self.point_max)
        target = text.pos_to_line(self.pt) + n
        if target > last:
            # As in Emacs, moving onto the end of a last line that has no
            # newline counts as having moved over that line.
            old = self.pt
            self.pt = self.point_max
            shortage = target - last
            if self.pt != old and self.char_before() != '\n':
                shortage -= 1
            return shortage
        if target < first:
            self.pt = self.point_min
            return target - first
        self.pt = max(self.point_min, text.line_start(target))
        return 0

    def goto_line(self, n):
        """Go to the start of line ``n``, counting from one."""
        line = max(0, min(n - 1, self.text.line_count - 1))
        self.pt = self.clamp(self.text.line_start(line))

    def line_number(self, pos=None):
        """Line number of ``pos``, counting from one."""
        return self.line_at(pos) + 1

    def count_lines(self, start, end):
        start, end = sorted((self._clamp_all(start), self._clamp_all(end)))
        if start == end:
            return 0
        n = self.text.pos_to_line(end) - self.text.pos_to_line(start)
        return n + (0 if self.text.char_at(end - 1) == '\n' else 1)

    def line_info(self, i):
        """The cache record of line ``i``, with its layout measured."""
        lines, metas, j = self.text.slot(i)
        info = metas[j]
        if info is None:
            info = metas[j] = LineInfo()
        tabw = self.get('tab_width')
        if info.tabw != tabw:
            line = lines[j]
            measure(info, line[:-1] if line.endswith('\n') else line, tabw)
        return info

    def current_column(self, pos=None):
        """Screen column of ``pos`` counted from the start of its line."""
        line, col = self.text.pos_to_linecol(self.pt if pos is None else self._clamp_all(pos))
        info = self.line_info(line)
        return col if info.widths is None else info.cols[col]

    def column_to_index(self, line, column):
        """Index in ``line`` of the character occupying screen ``column``."""
        info = self.line_info(line)
        length = self.text.line_length(line)
        if info.widths is None:
            return max(0, min(column, length))
        return max(0, min(bisect_right(info.cols, column) - 1, length))

    def move_to_column(self, column, force=False):
        """Move to ``column`` of the current line, or as near as it reaches.

        With ``force``, pad the line with spaces to reach it.
        """
        line = self.line_at()
        self.pt = self.clamp(self.text.line_start(line) + self.column_to_index(line, column))
        reached = self.current_column()
        if force and reached < column:
            self.insert(' ' * (column - reached))
            reached = column
        return reached

    def current_indentation(self, pos=None):
        """Column of the first non-blank character of the line."""
        line = self.line_at(pos)
        text = self.text.line(line)
        blank = _BLANK.match(text).end()
        info = self.line_info(line)
        return blank if info.widths is None else info.cols[blank]

    def back_to_indentation(self):
        line = self.line_at()
        self.pt = self.clamp(self.text.line_start(line) + _BLANK.match(self.text.line(line)).end())

    def indent_line_to(self, column, line=None):
        """Make the indentation of the line exactly ``column`` columns."""
        line = self.line_at() if line is None else line
        start = self.text.line_start(line)
        end = start + _BLANK.match(self.text.line(line)).end()
        if self.get('indent_tabs_mode'):
            tabw = self.get('tab_width')
            new = '\t' * (column // tabw) + ' ' * (column % tabw)
        else:
            new = ' ' * column
        if self.text.substring(start, end) != new:
            moved = self.pt <= end and self.pt >= start
            self.replace_region(start, end, new)
            if moved:
                self.pt = start + len(new)

    # -- changing text -----------------------------------------------------

    def _check_writable(self):
        if self.read_only and not _inhibit_read_only:
            raise BufferReadOnly(self.name)

    def _record(self, entry):
        self.modiff += 1
        if not self.undo_enabled:
            self._state = next(_state_ids)
            return
        group = self._open
        if group is None:
            if self._generating:
                kind, ref, after = self._generating
            else:
                kind, ref, after = 'normal', None, next(_state_ids)
            group = self._open = UndoGroup(kind, ref, self._state, after, self.pt)
            self._groups.append(group)
            self._state = after
        entries = group.entries
        if entries:
            last = entries[-1]
            if entry[0] == 'i' and last[0] == 'i' and last[1] + last[2] == entry[1]:
                entries[-1] = ('i', last[1], last[2] + entry[2])
                return
            if entry[0] == 'd' and last[0] == 'd':
                if entry[1] == last[1]:                     # deleting forward
                    entries[-1] = ('d', last[1], last[2] + entry[2])
                    return
                if entry[1] + len(entry[2]) == last[1]:     # deleting backward
                    entries[-1] = ('d', entry[1], entry[2] + last[2])
                    return
        entries.append(entry)

    def insert_at(self, pos, s, *, before_markers=False):
        """Insert ``s`` at ``pos``.  Point moves only if it is after ``pos``."""
        if not s:
            return
        pos = int(pos)
        if not self.point_min <= pos <= self.point_max:
            raise ArgsOutOfRange(pos)
        self._check_writable()
        for listener in self.before_change:
            listener(self, pos, pos)
        point = self.pt
        self.text.insert(pos, s)
        n = len(s)
        for marker in self._markers:
            p = marker.pos
            if p > pos or (p == pos and (marker.insertion_type or before_markers)):
                marker.pos = p + n
        self._record(('i', pos, n))
        if point > pos:
            self.pt = point + n
        for listener in self.after_change:
            listener(self, pos, pos + n, 0)

    def insert(self, *strings):
        """Insert at point, leaving point after the insertion."""
        for s in strings:
            if s:
                pos = self.pt
                self.insert_at(pos, s)
                self.pt = pos + len(s)

    def delete_region(self, start, end):
        """Delete the text between two positions and return it."""
        start, end = sorted((int(start), int(end)))
        if start == end:
            return ''
        if start < self.point_min or end > self.point_max:
            raise ArgsOutOfRange(start, end)
        self._check_writable()
        for listener in self.before_change:
            listener(self, start, end)
        point = self.pt
        n = end - start
        removed = self.text.delete(start, end)
        for marker in self._markers:
            p = marker.pos
            if p > end:
                marker.pos = p - n
            elif p > start:
                marker.pos = start
        self._record(('d', start, removed))
        if point > end:
            self.pt = point - n
        elif point > start:
            self.pt = start
        for listener in self.after_change:
            listener(self, start, start, n)
        return removed

    def replace_region(self, start, end, s):
        """Replace the text between two positions with ``s``."""
        start, end = sorted((int(start), int(end)))
        if self.text.substring(start, end) == s:
            return
        self.delete_region(start, end)
        self.insert_at(start, s)

    def delete_char(self, n=1):
        """Delete ``n`` characters after point, or before it if negative."""
        target = self.pt + n
        if target < self.point_min:
            raise BeginningOfBuffer()
        if target > self.point_max:
            raise EndOfBuffer()
        return self.delete_region(self.pt, target)

    def erase(self):
        self.widen()
        self.delete_region(0, len(self.text))

    # -- modification state ------------------------------------------------

    @property
    def modified(self):
        return self._state != self._saved_state

    def set_modified(self, flag):
        self._saved_state = -1 if flag else self._state
        self.modiff += 1

    # -- undo --------------------------------------------------------------

    def undo_boundary(self):
        """End the group of changes that one undo step will take back."""
        self._open = None

    def disable_undo(self):
        self.undo_enabled = False
        self._groups = []
        self._open = None

    def clear_undo(self):
        self._groups = []
        self._open = None
        self._pending = None

    def _revert(self, index, kind):
        group = self._groups[index]
        self.undo_boundary()
        self._generating = (kind, index, group.before)
        try:
            for entry in reversed(group.entries):
                if entry[0] == 'i':
                    self.delete_region(entry[1], entry[1] + entry[2])
                else:
                    self.insert_at(entry[1], entry[2])
        finally:
            self._generating = None
            self.undo_boundary()
        self.pt = self.clamp(group.point)

    def undo(self, continuing=False, only=False):
        """Take back one group of changes.

        A run of undo commands walks back through the record.  Any other
        command ends the run, after which the undoing itself is part of the
        record and can be undone.  With ``only``, changes that were already
        undone and redone are passed over rather than replayed.
        """
        self._check_writable()
        self.undo_boundary()
        groups = self._groups
        if not continuing or self._pending is None:
            self._pending = len(groups)
        i = self._pending - 1
        if only:
            while i >= 0 and groups[i].kind != 'normal':
                i = groups[i].ref - 1
        if i < 0:
            raise EditorError('No further undo information')
        self._revert(i, 'undo')
        self._pending = i

    def redo(self, continuing=False):
        """Undo the most recent undo."""
        self._check_writable()
        self.undo_boundary()
        groups = self._groups
        i = (self._pending if continuing and self._pending is not None else len(groups)) - 1
        while i >= 0 and groups[i].kind == 'redo':
            i = groups[i].ref - 1
        if i < 0 or groups[i].kind != 'undo':
            raise EditorError('No further redo information')
        self._revert(i, 'redo')
        self._pending = i

    # -- mark and region ---------------------------------------------------

    def mark_pos(self):
        return self.mark.pos if self.mark is not None else None

    def set_mark(self, pos):
        if self.mark is None:
            self.mark = Marker(self, pos)
        else:
            self.mark.set(pos)

    def push_mark(self, pos=None, activate=False):
        """Set the mark at ``pos`` (point if None), saving the old mark on the ring."""
        if self.mark is not None:
            self.mark_ring.insert(0, Marker(self, self.mark.pos))
            limit = self.get('mark_ring_max')
            if limit is not None:
                for old in self.mark_ring[limit:]:
                    old.delete()
                del self.mark_ring[limit:]
        self.set_mark(self.pt if pos is None else pos)
        if activate:
            self.mark_active = True

    def pop_mark(self):
        """Make the newest ring entry the mark, moving the mark to the ring's end."""
        if self.mark is None:
            return
        if self.mark_ring:
            self.mark_ring.append(Marker(self, self.mark.pos))
            newest = self.mark_ring.pop(0)
            self.mark.set(newest.pos)
            newest.delete()
        self.mark_active = False

    def region(self):
        """``(start, end)`` of the region; an error if there is no mark."""
        if self.mark is None:
            raise EditorError('The mark is not set now, so there is no region')
        return tuple(sorted((self.clamp(self.mark.pos), self.pt)))

    def region_active(self):
        return self.mark_active and self.mark is not None

    # -- searching ---------------------------------------------------------

    def _bound(self, bound, forward):
        if bound is None:
            return self.point_max if forward else self.point_min
        return self.clamp(bound)

    def search_forward(self, string, bound=None, *, noerror=False, count=1, fold=False):
        """Find ``string`` after point and move past it; return the new point."""
        text = self.text.snapshot()
        limit = self._bound(bound, True)
        pos = self.pt
        if fold:
            pattern = re.compile(re.escape(string), re.IGNORECASE)
        for _ in range(count):
            if fold:
                match = pattern.search(text, pos, limit)
                found = match.start() if match else -1
            else:
                found = text.find(string, pos, limit)
            if found < 0:
                if noerror:
                    return None
                raise SearchFailed(string)
            pos = found + len(string)
        self.pt = pos
        return pos

    def _last_match(self, pattern, limit, pos):
        """The match in ``[limit, pos)`` that starts latest, looking in a
        window that widens until one is found."""
        text = self.text.snapshot()
        reach = 4096
        while True:
            lo = max(limit, pos - reach)
            match = None
            for match in pattern.finditer(text, lo, pos):
                pass
            if match is not None or lo == limit:
                return match
            reach *= 8

    def search_backward(self, string, bound=None, *, noerror=False, count=1, fold=False):
        """Find ``string`` before point and move to its start; return the new point."""
        text = self.text.snapshot()
        limit = self._bound(bound, False)
        pos = self.pt
        if fold:
            pattern = re.compile(re.escape(string), re.IGNORECASE)
        for _ in range(count):
            if fold:
                match = self._last_match(pattern, limit, pos)
                found = match.start() if match else -1
            else:
                found = text.rfind(string, limit, pos)
            if found < 0:
                if noerror:
                    return None
                raise SearchFailed(string)
            pos = found
        self.pt = pos
        return pos

    def re_search_forward(self, pattern, bound=None, *, noerror=False, count=1, flags=0):
        """Find a match for ``pattern`` after point; move to its end and return it."""
        if isinstance(pattern, str):
            pattern = re.compile(pattern, flags | re.MULTILINE)
        text = self.text.snapshot()
        limit = self._bound(bound, True)
        pos = self.pt
        match = None
        for _ in range(count):
            match = pattern.search(text, pos, limit)
            if match is None:
                if noerror:
                    return None
                raise SearchFailed(pattern.pattern)
            pos = match.end() if match.end() > match.start() or count == 1 else match.end() + 1
        self.pt = match.end()
        return match

    def re_search_backward(self, pattern, bound=None, *, noerror=False, count=1, flags=0):
        """Find a match for ``pattern`` that ends at or before point, starting as
        late as possible; move to its start and return it."""
        if isinstance(pattern, str):
            pattern = re.compile(pattern, flags | re.MULTILINE)
        limit = self._bound(bound, False)
        pos = self.pt
        match = None
        for _ in range(count):
            match = self._last_match(pattern, limit, pos)
            if match is None:
                if noerror:
                    return None
                raise SearchFailed(pattern.pattern)
            pos = match.start()
        self.pt = match.start()
        return match

    def looking_at(self, pattern, flags=0):
        """Match ``pattern`` at point without moving."""
        if isinstance(pattern, str):
            pattern = re.compile(pattern, flags | re.MULTILINE)
        return pattern.match(self.text.snapshot(), self.pt, self.point_max)

    def looking_back(self, pattern, limit=None, flags=0):
        """Match ``pattern`` ending exactly at point, starting no earlier than
        ``limit`` (the start of the line if None)."""
        source = pattern if isinstance(pattern, str) else pattern.pattern
        anchored = re.compile(f'(?:{source})\\Z', flags | re.MULTILINE)
        start = self.bol() if limit is None else self.clamp(limit)
        return anchored.search(self.text.snapshot(), start, self.pt)

    def skip_chars_forward(self, chars, limit=None):
        """Move over characters in the class ``chars``, written as the inside
        of a regular expression bracket; return the distance moved."""
        match = re.compile(f'[{chars}]*').match(
            self.text.snapshot(), self.pt, self._bound(limit, True))
        moved = match.end() - self.pt
        self.pt = match.end()
        return moved

    def skip_chars_backward(self, chars, limit=None):
        text = self.text.snapshot()
        lo = self._bound(limit, False)
        allowed = re.compile(f'[{chars}]')
        pos = self.pt
        while pos > lo and allowed.match(text, pos - 1):
            pos -= 1
        moved = pos - self.pt
        self.pt = pos
        return moved

    # -- overlays ----------------------------------------------------------

    def make_overlay(self, start, end, **props):
        return Overlay(self, start, end, **props)

    def overlays_in(self, start, end, kind=None):
        """Overlays overlapping ``[start, end)``, and empty ones inside it."""
        found = []
        evaporated = []
        for overlay in self.overlays:
            s, e = overlay._start.pos, overlay._end.pos
            if s == e and overlay.props.get('evaporate'):
                evaporated.append(overlay)
                continue
            if (s < end and e > start) or (s == e and start <= s <= end):
                if kind is None or overlay.props.get('kind') == kind:
                    found.append(overlay)
        for overlay in evaporated:
            overlay.delete()
        return found

    def overlays_at(self, pos, kind=None):
        return [o for o in self.overlays
                if o._start.pos <= pos < o._end.pos
                and (kind is None or o.props.get('kind') == kind)]

    def remove_overlays(self, start=None, end=None, kind=None, **match):
        start = 0 if start is None else start
        end = len(self.text) if end is None else end
        for overlay in list(self.overlays):
            if overlay._start.pos > end or overlay._end.pos < start:
                continue
            if kind is not None and overlay.props.get('kind') != kind:
                continue
            if any(overlay.props.get(k) != val for k, val in match.items()):
                continue
            overlay.delete()

