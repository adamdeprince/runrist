# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Emacs regular expressions, translated to Python's.

The language modes taken from Emacs describe their keywords as Emacs
regexps, which differ from Python's in spelling and in a few meanings:

* Grouping and alternation are ``\\(``, ``\\)`` and ``\\|``; plain parentheses,
  braces and ``|`` are ordinary characters.
* ``\\(?3:`` gives a group an explicit number, and a number may be given to
  more than one group.  Python numbers groups in order, so ``translate``
  also returns which Python groups stand for each Emacs group.
* ``\\sw``, ``\\s-``, ``\\_<`` and their kin ask about a character's *syntax
  class*, which depends on the mode.  They are expanded from the ``Syntax``
  the translation is given.
* Inside brackets a backslash is an ordinary character, and classes such as
  ``[:alpha:]`` may appear.
* ``^`` and ``$`` are anchors only where an anchor makes sense.
"""

import re

_ASCII = frozenset(map(chr, range(128)))
_LETTERS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz')
_DIGITS = frozenset('0123456789')

# POSIX classes as (ASCII members, whether every non-ASCII character is a member).
_POSIX = {
    'alpha': (_LETTERS, True),
    'alnum': (_LETTERS | _DIGITS, True),
    'digit': (_DIGITS, False),
    'xdigit': (_DIGITS | frozenset('abcdefABCDEF'), False),
    'upper': (frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ'), False),
    'lower': (frozenset('abcdefghijklmnopqrstuvwxyz'), False),
    'blank': (frozenset(' \t'), False),
    'punct': (frozenset(c for c in map(chr, range(33, 127)) if not c.isalnum()), False),
    'cntrl': (frozenset(map(chr, (*range(32), 127))), False),
    'graph': (frozenset(map(chr, range(33, 127))), True),
    'print': (frozenset(map(chr, range(32, 127))), True),
    'ascii': (_ASCII, False),
    'nonascii': (frozenset(), True),
    'multibyte': (frozenset(), True),
    'unibyte': (_ASCII, False),
}

_CLASS_SPECIAL = set('\\]^[-&|~')


def class_escape(char):
    """``char`` as it must be written inside a Python character class."""
    if char in _CLASS_SPECIAL:
        return '\\' + char
    if char == '\n':
        return '\\n'
    if char == '\t':
        return '\\t'
    if ord(char) < 32 or ord(char) == 127:
        return f'\\x{ord(char):02x}'
    return char


def _ranges(chars):
    """The inside of a character class matching exactly the ASCII ``chars``."""
    codes = sorted(map(ord, chars))
    out = []
    i = 0
    while i < len(codes):
        j = i
        while j + 1 < len(codes) and codes[j + 1] == codes[j] + 1:
            j += 1
        if j - i >= 2:
            out.append(class_escape(chr(codes[i])) + '-' + class_escape(chr(codes[j])))
        else:
            out.extend(class_escape(chr(c)) for c in codes[i:j + 1])
        i = j + 1
    return ''.join(out)


def char_class(chars, nonascii=False, negated=False, extra=''):
    """A pattern matching one character from a set.

    The set is the ASCII characters ``chars``, every non-ASCII character if
    ``nonascii``, and whatever ``extra`` (the inside of a class) adds.  A set
    holding all non-ASCII characters is written as what it leaves out, which
    Python compiles far faster than the range itself.
    """
    chars = frozenset(chars) & _ASCII
    if nonascii:
        rest = _ASCII - chars
        if negated:
            return f'[{_ranges(rest)}]' if rest else '(?!)'
        return f'[^{_ranges(rest)}]' if rest else '[\\s\\S]'
    body = _ranges(chars) + extra
    if not body:
        return '[\\s\\S]' if negated else '(?!)'
    return f'[^{body}]' if negated else f'[{body}]'


class DefaultSyntax:
    """The classes used when a translation is not given a mode's syntax."""

    _sets = {
        'w': (_LETTERS | _DIGITS, True),
        '_': (frozenset('_'), False),
        ' ': (frozenset(' \t\r\f'), False),
        '.': (frozenset('!#$%&*+,-./:;<=>?@\\^`|~'), False),
        '(': (frozenset('([{'), False),
        ')': (frozenset(')]}'), False),
        '"': (frozenset('"\''), False),
        '\\': (frozenset('\\'), False),
        '>': (frozenset('\n'), False),
    }

    def chars(self, code):
        return self._sets.get(' ' if code == '-' else code, (frozenset(), False))


_DEFAULT = DefaultSyntax()


def _bracket(pattern, i, syntax):
    """Translate the bracket expression starting at ``pattern[i]``."""
    n = len(pattern)
    j = i + 1
    negated = False
    if j < n and pattern[j] == '^':
        negated = True
        j += 1
    chars = set()
    nonascii = False
    extra = []
    first = True

    def add(lo, hi=None):
        nonlocal nonascii
        hi = lo if hi is None else hi
        if ord(hi) < 128:
            chars.update(map(chr, range(ord(lo), ord(hi) + 1)))
        elif ord(lo) < 128:
            chars.update(map(chr, range(ord(lo), 128)))
            extra.append('\\x80-' + class_escape(hi))
        else:
            extra.append(class_escape(lo) + ('-' + class_escape(hi) if hi != lo else ''))

    while j < n:
        c = pattern[j]
        if c == ']' and not first:
            break
        first = False
        if c == '[' and pattern.startswith(':', j + 1):
            close = pattern.find(':]', j + 2)
            if close > 0:
                name = pattern[j + 2:close]
                if name == 'word':
                    members, more = syntax.chars('w')
                elif name == 'space':
                    members, more = syntax.chars(' ')
                    members = members or frozenset(' \t')
                else:
                    members, more = _POSIX.get(name, (frozenset(), False))
                chars |= members
                nonascii = nonascii or more
                j = close + 2
                continue
        if pattern.startswith('-', j + 1) and j + 2 < n and pattern[j + 2] != ']':
            add(c, pattern[j + 2])
            j += 3
        else:
            add(c)
            j += 1
    return char_class(chars, nonascii, negated, ''.join(extra)), j + 1


def translate(pattern, syntax=None):
    """Translate an Emacs regexp.

    Returns ``(python pattern, groups)`` where ``groups`` maps each Emacs
    group number to the tuple of Python group numbers that stand for it.
    """
    syntax = syntax or _DEFAULT
    word_set = syntax.chars('w')
    symbol_set = (word_set[0] | syntax.chars('_')[0], word_set[1] or syntax.chars('_')[1])
    word, not_word = char_class(*word_set), char_class(*word_set, negated=True)
    symbol = char_class(*symbol_set)
    out = []
    groups = {}
    python_index = 0
    last_number = 0
    n = len(pattern)
    i = 0
    at_start = True                 # a ^ or a repetition operator here is at the start of a branch
    while i < n:
        c = pattern[i]
        was_start, at_start = at_start, False
        if c == '\\':
            i += 1
            if i >= n:
                out.append('\\\\')
                break
            d = pattern[i]
            i += 1
            if d == '(':
                at_start = True
                if pattern.startswith('?:', i):
                    out.append('(?:')
                    i += 2
                    continue
                explicit = re.match(r'\?(\d+):', pattern[i:])
                python_index += 1
                if explicit:
                    number = int(explicit.group(1))
                    i += explicit.end()
                    last_number = max(last_number, number)
                else:
                    last_number += 1
                    number = last_number
                groups[number] = groups.get(number, ()) + (python_index,)
                out.append('(')
            elif d == ')':
                out.append(')')
            elif d == '|':
                out.append('|')
                at_start = True
            elif d == '{':
                close = pattern.find('\\}', i)
                if close < 0:
                    out.append('\\{')
                else:
                    out.append('{' + pattern[i:close] + '}')
                    i = close + 2
            elif d == '<':
                out.append(f'(?<!{word})(?={word})')
            elif d == '>':
                out.append(f'(?<={word})(?!{word})')
            elif d == '_' and i < n and pattern[i] in '<>':
                if pattern[i] == '<':
                    out.append(f'(?<!{symbol})(?={symbol})')
                else:
                    out.append(f'(?<={symbol})(?!{symbol})')
                i += 1
            elif d == 'w':
                out.append(word)
            elif d == 'W':
                out.append(not_word)
            elif d in 'sS' and i < n:
                out.append(char_class(*syntax.chars(pattern[i]), negated=d == 'S'))
                i += 1
            elif d in 'cC' and i < n:
                # Character categories are about scripts; none of them is ASCII.
                i += 1
                out.append('[^\\x00-\\x7f]' if d == 'c' else '[\\x00-\\x7f]')
            elif d == 'b':
                out.append(f'(?:(?<!{word})(?={word})|(?<={word})(?!{word}))')
            elif d == 'B':
                out.append(f'(?:(?<={word})(?={word})|(?<!{word})(?!{word}))')
            elif d == '`':
                out.append('\\A')
            elif d == "'":
                out.append('\\Z')
            elif d == '=':
                pass                    # "at point" means nothing when matching a line
            elif d.isdigit():
                indexes = groups.get(int(d))
                out.append(f'(?:\\{indexes[0]})' if indexes else '(?!)')
            else:
                out.append(re.escape(d))
        elif c == '[':
            text, i = _bracket(pattern, i, syntax)
            out.append(text)
        elif c == '^':
            out.append('^' if was_start else '\\^')
            at_start = was_start
            i += 1
        elif c == '$':
            i += 1
            anchor = i == n or pattern.startswith('\\)', i) or pattern.startswith('\\|', i)
            out.append('$' if anchor else '\\$')
        elif c in '*+?':
            out.append('\\' + c if was_start else c)
            i += 1
        elif c == '.':
            out.append('.')
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return ''.join(out), groups


def compile(pattern, syntax=None, case_fold=False):         # noqa: A001
    """Compile an Emacs regexp.  Returns ``(compiled, groups)``."""
    text, groups = translate(pattern, syntax)
    flags = re.MULTILINE | (re.IGNORECASE if case_fold else 0)
    return re.compile(text, flags), groups
