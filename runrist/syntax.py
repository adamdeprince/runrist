# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Syntax: which characters make words, strings, comments and brackets.

A ``Syntax`` is what an Emacs syntax table is: for each character a class
(word, symbol, punctuation, open, close, string quote, comment start ...),
and flags that build comment delimiters out of two characters.  It can be
made from an Emacs table as written out by ``tools/emacs-extract.el``, and
adjusted afterwards the way ``modify-syntax-entry`` would.

It also knows things a table cannot say, which Emacs handles with code: that
Python's strings may open with three quotes, for instance.  A mode states
such delimiters outright with ``add_string``.

``scan_line`` finds the strings and comments of one line given the state the
line starts in -- inside a string, inside a comment, or neither -- and
returns the state it ends in.  Carrying that state from line to line is all
that is needed to know, anywhere in a buffer, what is code and what is not.
"""

import re

from .eregex import char_class, class_escape


class _String:
    __slots__ = ('open', 'close', 'escape', 'multiline', 'closer')

    def __init__(self, open_, close, escape, multiline):
        self.open = open_
        self.close = close
        self.escape = escape
        self.multiline = multiline
        if escape:
            self.closer = re.compile(f'(?P<esc>{re.escape(escape)}.)|{re.escape(close)}')
        else:
            self.closer = re.compile(re.escape(close))


class _Comment:
    __slots__ = ('open', 'close', 'nested', 'closer')

    def __init__(self, open_, close, nested):
        self.open = open_
        self.close = close
        self.nested = nested
        if nested:
            self.closer = re.compile(f'(?P<open>{re.escape(open_)})|{re.escape(close)}')
        else:
            self.closer = re.compile(re.escape(close))


class Syntax:
    def __init__(self):
        self.classes = {}           # character -> class: one of " .w_()'\"$\\/<>!|"
        self.flags = {}             # character -> flag letters
        self.pairs = {}             # bracket -> its partner
        self.strings = []
        self.comments = []          # comments that have a closing delimiter
        self.line_comments = []     # delimiters of comments that run to the end of the line
        self.escape = None
        self.string_face = None     # f(context, text, start, delimiter) -> face or None
        self.comment_face = 'font-lock-comment-face'
        self._bodies = {}
        self._opener = None

    # -- building ----------------------------------------------------------------------

    @classmethod
    def from_emacs(cls, table):
        """Make a syntax from ``{character code: [class, flags, partner]}``."""
        self = cls()
        for code, (klass, flags, partner) in table.items():
            char = chr(int(code))
            self.classes[char] = klass
            if flags:
                self.flags[char] = flags
            if partner and klass in '()':
                self.pairs[char] = partner
        self._derive()
        return self

    def copy(self):
        other = Syntax()
        other.classes = dict(self.classes)
        other.flags = dict(self.flags)
        other.pairs = dict(self.pairs)
        other.strings = list(self.strings)
        other.comments = list(self.comments)
        other.line_comments = list(self.line_comments)
        other.escape = self.escape
        other.string_face = self.string_face
        other.comment_face = self.comment_face
        return other

    def modify(self, char, klass, flags='', partner=''):
        """Change one character's entry, as ``modify-syntax-entry`` does."""
        self.classes[char] = klass
        if flags:
            self.flags[char] = flags
        else:
            self.flags.pop(char, None)
        if partner:
            self.pairs[char] = partner
        self._derive()

    def _derive(self):
        """Work out the delimiters the table implies."""
        classes, flags = self.classes, self.flags
        escapes = [c for c, k in classes.items() if k == '\\']
        self.escape = escapes[0] if escapes else None
        self.strings = [_String(c, c, self.escape, True) for c, k in classes.items() if k == '"']
        enders = [c for c, k in classes.items() if k == '>']
        self.line_comments = []
        self.comments = []
        for c, k in classes.items():
            if k == '<':
                style_b = 'b' in flags.get(c, '')
                ends = [e for e in enders if ('b' in flags.get(e, '')) == style_b]
                if not ends or '\n' in ends:
                    self.line_comments.append(c)
                else:
                    self.comments.append(_Comment(c, ends[0], 'n' in flags.get(c, '')))
        firsts = [c for c, f in flags.items() if '1' in f]
        seconds = [c for c, f in flags.items() if '2' in f]
        thirds = [c for c, f in flags.items() if '3' in f]
        fourths = [c for c, f in flags.items() if '4' in f]
        for first in firsts:
            for second in seconds:
                style_b = 'b' in flags.get(second, '')
                style_c = 'c' in flags.get(second, '')
                nested = 'n' in flags.get(first, '') or 'n' in flags.get(second, '')
                closing = None
                for third in thirds:
                    if ('b' in flags.get(third, '')) != style_b:
                        continue
                    for fourth in fourths:
                        if ('c' in flags.get(fourth, '')) == style_c or not style_c:
                            closing = third + fourth
                            break
                    if closing:
                        break
                single = [e for e in enders if ('b' in flags.get(e, '')) == style_b]
                if '\n' in single and (closing is None or style_b):
                    self.line_comments.append(first + second)
                elif closing:
                    self.comments.append(_Comment(first + second, closing, nested))
        self._bodies = {}
        self._opener = None

    def add_string(self, open_, close=None, escape='\\', multiline=True):
        """Declare a string delimiter of any length."""
        self.strings = [s for s in self.strings if s.open != open_]
        self.strings.append(_String(open_, close or open_, escape, multiline))
        self._opener = None

    def remove_string(self, open_):
        self.strings = [s for s in self.strings if s.open != open_]
        self._opener = None

    def add_comment(self, open_, close=None, nested=False):
        """Declare a comment delimiter; with no ``close`` it runs to the end of the line."""
        if close is None:
            if open_ not in self.line_comments:
                self.line_comments.append(open_)
        else:
            self.comments = [c for c in self.comments if c.open != open_]
            self.comments.append(_Comment(open_, close, nested))
        self._opener = None

    # -- character classes for regular expressions ------------------------------------------

    def chars(self, code):
        """``(ASCII members, whether non-ASCII characters belong)`` of a syntax class."""
        if code == '-':
            code = ' '
        cached = self._bodies.get(code)
        if cached is None:
            members = frozenset(c for c, k in self.classes.items() if k == code and ord(c) < 128)
            if code == 'w':
                if not members:
                    members = frozenset('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                                        'abcdefghijklmnopqrstuvwxyz')
                cached = (members, True)        # letters beyond ASCII are words, as in Emacs
            elif code == ' ' and not members:
                cached = (frozenset(' \t\r\f'), False)
            else:
                cached = (members, False)
            self._bodies[code] = cached
        return cached

    def char_class(self, char):
        """The syntax class of one character."""
        klass = self.classes.get(char)
        if klass is not None:
            return klass
        if char.isalnum() or ord(char) > 127:
            return 'w'
        return ' ' if char.isspace() else '.'

    @property
    def symbol_pattern(self):
        """A compiled pattern matching one whole symbol."""
        word, more = self.chars('w')
        return re.compile(char_class(word | self.chars('_')[0], more) + '+')

    @property
    def word_pattern(self):
        return re.compile(char_class(*self.chars('w')) + '+')

    # -- scanning ---------------------------------------------------------------------------

    def _compile(self):
        parts = []
        strings = sorted(enumerate(self.strings), key=lambda item: -len(item[1].open))
        comments = sorted(enumerate(self.comments), key=lambda item: -len(item[1].open))
        lines = sorted(self.line_comments, key=len, reverse=True)
        for index, string in strings:
            if len(string.open) > 1:
                parts.append(f'(?P<s{index}>{re.escape(string.open)})')
        for index, comment in comments:
            if len(comment.open) > 1:
                parts.append(f'(?P<c{index}>{re.escape(comment.open)})')
        many = [d for d in lines if len(d) > 1]
        if many:
            parts.append('(?P<l>' + '|'.join(re.escape(d) for d in many) + ')')
        if self.escape:
            parts.append(f'(?P<e>{re.escape(self.escape)}.)')
        for index, string in strings:
            if len(string.open) == 1:
                parts.append(f'(?P<s{index}>{re.escape(string.open)})')
        for index, comment in comments:
            if len(comment.open) == 1:
                parts.append(f'(?P<c{index}>{re.escape(comment.open)})')
        single = [d for d in lines if len(d) == 1]
        if single:
            parts.append('(?P<l1>[' + ''.join(class_escape(d) for d in single) + '])')
        self._opener = re.compile('|'.join(parts)) if parts else re.compile('(?!)')

    def scan_line(self, text, state, context=None):
        """Find the strings and comments of a line.

        ``state`` is None, ``('s', index, face)`` inside string ``index``, or
        ``('c', index, depth)`` inside comment ``index``.  Returns
        ``(regions, state at the end of the line)``; a region is
        ``(start, end, kind, face)`` with kind ``'s'`` or ``'c'``.
        """
        if self._opener is None:
            self._compile()
        regions = []
        n = len(text)
        pos = 0
        start = 0
        while True:
            if state is None:
                match = self._opener.search(text, pos)
                if match is None:
                    return regions, None
                kind = match.lastgroup
                if kind == 'e':
                    pos = match.end()
                    continue
                start = match.start()
                if kind[0] == 'l':
                    regions.append((start, n, 'c', self.comment_face))
                    return regions, None
                index = int(kind[1:])
                pos = match.end()
                if kind[0] == 'c':
                    state = ('c', index, 1)
                else:
                    face = None
                    if self.string_face is not None:
                        face = self.string_face(context, text, start, self.strings[index])
                    state = ('s', index, face or 'font-lock-string-face')
            if state[0] == 's':
                string = self.strings[state[1]]
                while True:
                    match = string.closer.search(text, pos)
                    if match is None:
                        regions.append((start, n, 's', state[2]))
                        if string.multiline:
                            return regions, state
                        if string.escape and (len(text) - len(text.rstrip(string.escape))) % 2:
                            return regions, state       # the line ends in a continuation
                        return regions, None
                    pos = match.end()
                    if match.lastgroup != 'esc':
                        regions.append((start, pos, 's', state[2]))
                        state = None
                        break
            else:
                comment = self.comments[state[1]]
                depth = state[2]
                while True:
                    match = comment.closer.search(text, pos)
                    if match is None:
                        regions.append((start, n, 'c', self.comment_face))
                        return regions, ('c', state[1], depth)
                    pos = match.end()
                    if match.lastgroup == 'open':
                        depth += 1
                        continue
                    depth -= 1
                    if depth == 0:
                        regions.append((start, pos, 'c', self.comment_face))
                        state = None
                        break
            start = pos


def standard():
    """A plain syntax: words, the usual brackets, double-quoted strings."""
    syntax = Syntax()
    for open_, close in ('()', '[]', '{}'):
        syntax.classes[open_] = '('
        syntax.classes[close] = ')'
        syntax.pairs[open_] = close
        syntax.pairs[close] = open_
    syntax.classes['_'] = '_'
    syntax.classes['"'] = '"'
    syntax.classes['\\'] = '\\'
    for c in ' \t\r\f':
        syntax.classes[c] = ' '
    syntax._derive()
    return syntax
