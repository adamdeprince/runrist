# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Highlighting.

This is Emacs's font-lock, a line at a time.  A buffer's ``FontLock`` has a
``Syntax``, which finds strings and comments, and a list of *keywords*: a
matcher and the faces to give the parts of what it matches.  Each line is
scanned for strings and comments first; then every keyword is tried on it,
and by default colors only text nothing has colored yet, which is what keeps
keywords inside strings and comments plain.

What is worked out for a line is kept beside the line (see
``runrist.chars.LineInfo``), with the scanner's state at each end of it.
After an edit the lines from there on are looked at again only as they are
needed, and only until a line is found to start in the state it started in
before -- typing inside a function costs one line, opening a string recolors
what follows.

A matcher is a compiled regular expression or a function
``f(line, position, context)`` returning a match or None; ``context`` is
``(buffer, line number)``.  A face is a name, a tuple of names laid one over
another, or a function of the match.
"""

from .chars import LineInfo
from .variables import defvar

defvar('font_lock', None, 'The FontLock that highlights this buffer, or None.', local=True)
defvar('font_lock_mode', True, 'Whether buffers are highlighted.')

_generation = 0


class Highlight:
    """Give ``face`` to group ``group`` of a match.

    ``override`` is None to color only what is not yet colored, ``'t'`` to
    color regardless, ``'keep'`` to fill the gaps, and ``'prepend'`` or
    ``'append'`` to combine with what is there.  ``groups`` are the Python
    groups that may hold the text; the first that took part is used.
    """

    __slots__ = ('groups', 'face', 'override', 'lax')

    def __init__(self, group, face, override=None, lax=True):
        self.groups = group if isinstance(group, tuple) else (group,)
        self.face = face
        self.override = override
        self.lax = lax


class Anchored:
    """After the keyword matches, search on from there and highlight each match found.

    The search starts at the end of the keyword's match, or at the start or
    end of one of its groups if ``start`` is ``('b', group)`` or
    ``('e', group)``; it stops at the end of the line, or at the end of group
    ``limit``.
    """

    __slots__ = ('matcher', 'highlights', 'start', 'limit')

    def __init__(self, matcher, highlights, start=None, limit=None):
        self.matcher = matcher
        self.highlights = highlights
        self.start = start
        self.limit = limit


class Keyword:
    __slots__ = ('matcher', 'highlights', 'is_function')

    def __init__(self, matcher, highlights):
        self.matcher = matcher
        self.highlights = highlights
        self.is_function = not hasattr(matcher, 'search')


def keyword(pattern, *highlights, flags=0):
    """A keyword from a Python regular expression.

    Each highlight is ``(group, face)`` or ``(group, face, override)``; a bare
    face name highlights the whole match."""
    import re
    built = []
    for item in highlights:
        if isinstance(item, (str, tuple)) and not (isinstance(item, tuple)
                                                    and isinstance(item[0], int)):
            built.append(Highlight(0, item))
        elif isinstance(item, (Highlight, Anchored)):
            built.append(item)
        else:
            built.append(Highlight(*item))
    return Keyword(re.compile(pattern, flags | re.MULTILINE), built)


class FontLock:
    def __init__(self, syntax, keywords=(), keywords_only=False):
        global _generation
        _generation += 1
        self.generation = _generation
        self.syntax = syntax
        self.keywords = list(keywords)
        self.keywords_only = keywords_only

    def changed(self):
        """Call after changing the syntax or the keywords of a FontLock in use."""
        global _generation
        _generation += 1
        self.generation = _generation

    # -- one line ----------------------------------------------------------------------

    @staticmethod
    def _apply(faces, match, highlight):
        start = -1
        for group in highlight.groups:
            try:
                start = match.start(group)
            except IndexError:
                continue
            if start >= 0:
                end = match.end(group)
                break
        if start < 0 or start == end:
            return
        face = highlight.face
        if callable(face):
            face = face(match)
        if not face:
            return
        override = highlight.override
        if override is None:
            if any(faces[start:end]):
                return
            faces[start:end] = [face] * (end - start)
        elif override == 't':
            faces[start:end] = [face] * (end - start)
        elif override == 'keep':
            for k in range(start, end):
                if faces[k] is None:
                    faces[k] = face
        else:
            for k in range(start, end):
                old = faces[k]
                if old is None:
                    faces[k] = face
                elif override == 'prepend':
                    faces[k] = (*old, face) if isinstance(old, tuple) else (old, face)
                else:
                    faces[k] = (face, *old) if isinstance(old, tuple) else (face, old)

    def spans(self, line, regions, context=None):
        """Faces of ``line`` as ``[(start, end, face)]``, given its strings and comments."""
        n = len(line)
        if n == 0:
            return ()
        faces = [None] * n
        if not self.keywords_only:
            for start, end, _, face in regions:
                if face:
                    faces[start:end] = [face] * (end - start)
        apply = self._apply
        for kw in self.keywords:
            matcher = kw.matcher
            pos = 0
            while pos <= n:
                match = matcher(line, pos, context) if kw.is_function \
                    else matcher.search(line, pos)
                if match is None:
                    break
                end = match.end()
                for highlight in kw.highlights:
                    if type(highlight) is Highlight:
                        apply(faces, match, highlight)
                    else:
                        at, stop = end, n
                        if highlight.start is not None:
                            side, group = highlight.start
                            where = match.start(group) if side == 'b' else match.end(group)
                            if where >= 0:
                                at = where
                        if highlight.limit is not None and match.end(highlight.limit) >= 0:
                            stop = match.end(highlight.limit)
                        inner = highlight.matcher
                        while at <= stop:
                            found = inner.search(line, at, stop)
                            if found is None:
                                break
                            for sub in highlight.highlights:
                                apply(faces, found, sub)
                            at = found.end() if found.end() > at else at + 1
                pos = end if end > pos else pos + 1
        spans = []
        k = 0
        while k < n:
            face = faces[k]
            if face is None:
                k += 1
                continue
            j = k + 1
            while j < n and faces[j] == face:
                j += 1
            spans.append((k, j, face))
            k = j
        return spans


def _changed(buffer, start, end, old_length):
    line = buffer.text.pos_to_line(min(start, buffer.size))
    if line < buffer.syntax_valid:
        buffer.syntax_valid = line


def enable(buffer, definition):
    """Highlight ``buffer`` with ``definition`` (a FontLock, or None for none)."""
    buffer.set('font_lock', definition)
    buffer.syntax = definition.syntax if definition is not None else None
    buffer.syntax_valid = 0
    if _changed not in buffer.after_change:
        buffer.after_change.append(_changed)
    buffer.modiff += 1


def fontify(buffer, first, last):
    """Make sure lines ``first`` to ``last`` have their faces worked out."""
    definition = buffer.locals.get('font_lock')
    if definition is None:
        return
    text = buffer.text
    last = min(last, text.line_count - 1)
    if last < first:
        return
    generation = definition.generation
    if buffer.fontlock_generation != generation:
        buffer.fontlock_generation = generation
        buffer.syntax_valid = 0
    valid = buffer.syntax_valid
    low = min(first, valid)
    state = None
    if low > 0:
        before = text.meta(low - 1)
        state = before.state_out if before is not None else None
    syntax = definition.syntax
    scan = syntax.scan_line
    for i, lines, metas, j in text.walk(low, last + 1):
        info = metas[j]
        if info is None:
            info = metas[j] = LineInfo()
        raw = lines[j]
        line = raw[:-1] if raw.endswith('\n') else raw
        if info.gen != generation or info.state_in != state or info.syntax is None:
            info.syntax, info.state_out = scan(line, state, (buffer, i))
            info.state_in = state
            info.gen = generation
            info.spans = None
            info.mm = None
        if i >= first and info.spans is None:
            info.spans = definition.spans(line, info.syntax, (buffer, i))
        state = info.state_out
    if last + 1 > valid:
        buffer.syntax_valid = last + 1


def syntax_at(buffer, line):
    """The strings and comments of a line: ``[(start, end, kind, face)]``."""
    if buffer.locals.get('font_lock') is None:
        return ()
    fontify(buffer, line, line)
    info = buffer.text.meta(line)
    return info.syntax if info is not None and info.syntax else ()


def in_string_or_comment(buffer, pos):
    """``'s'`` or ``'c'`` if ``pos`` is inside a string or a comment, else None."""
    line, column = buffer.text.pos_to_linecol(pos)
    for start, end, kind, _ in syntax_at(buffer, line):
        if start < column < end or (start < column and end == column
                                    and column == buffer.text.line_length(line)
                                    and kind == 'c'):
            return kind
    return None


def code_line(buffer, line):
    """Line ``line`` with its strings turned to ``x`` and its comments to
    spaces, so that brackets and keywords found in it are really code."""
    text = buffer.text.line(line)
    regions = syntax_at(buffer, line)
    if not regions:
        return text
    chars = list(text)
    for start, end, kind, _ in regions:
        chars[start:end] = (' ' if kind == 'c' else 'x') * (end - start)
    return ''.join(chars)
