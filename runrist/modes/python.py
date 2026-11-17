# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Python.

The keywords, builtins, exception names and syntax table come from Emacs's
``python.el`` by way of ``runrist/modes/data``.  This file adds what that
mode does in Lisp: strings that open with three quotes, docstrings, the
names being assigned to, escapes and interpolations inside strings, and
indentation.  It also makes a buffer of Python a place to work on the editor:
``C-M-x`` evaluates the definition around point in the running editor.
"""

import re

from .. import fontlock
from ..command import command
from ..editor import ed
from ..errors import EditorError
from ..fontlock import Highlight, Keyword, keyword
from ..mode import major_mode, major_modes
from ..variables import defvar
from . import emacs

defvar('python_indent_offset', 4, 'Columns of indentation for each level of Python code.',
       local=True)

_BLOCK = re.compile(r'[ \t]*(?:async[ \t]+)?(?:def|class)\b')
_DEDENT_AFTER = re.compile(r'[ \t]*(?:return|pass|break|continue|raise)\b')
_DEDENTER = re.compile(r'[ \t]*(?:else|elif|except|finally|case)\b')
_OPENER = re.compile(r'[ \t]*(?:if|elif|else|for|while|try|except|finally|with|match|case|'
                     r'def|class|async)\b')
_NAME = r'[^\W\d]\w*'
_TARGET = rf'\*?(?:{_NAME}\.)*{_NAME}(?:\[[^\]]*\])*'
_ASSIGNMENT = re.compile(
    rf'^[ \t]*(?P<lhs>{_TARGET}(?:[ \t]*,[ \t]*{_TARGET})*)[ \t]*'
    rf'(?::[^=\n]*?)?[ \t]*(?:=(?!=)|(?:[-+*/%&|^@]|//|\*\*|>>|<<)=)')
_LAST_NAME = re.compile(rf'(?:{_NAME}\.)*({_NAME})(?:\[[^\]]*\])*')
_ESCAPE = re.compile(r'\\(?:[\\\'"abfnrtv]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}'
                     r'|N\{[^}]*\}|[0-7]{1,3})')
_FIELD = re.compile(r'\{\{|\}\}|(\{)([^{}]*)(\})')
_NUMBER = (r'(?<![\w.])(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+'
           r'|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][-+]?\d[\d_]*)?[jJ]?)(?![\w])')


def _significant(buffer, line):
    """The nearest line above ``line`` that is not blank or only a comment."""
    text = buffer.text
    for above in range(line - 1, -1, -1):
        stripped = text.line(above).strip()
        if stripped and not stripped.startswith('#'):
            return above
    return None


def _indent_of(text):
    return len(text) - len(text.lstrip(' \t'))


def docstring_face(context, text, start, delimiter):
    """Call a string a docstring when it opens a module, a class or a function."""
    if context is None or text[:start].strip().lower() not in ('', 'r', 'u'):
        return None
    buffer, line = context
    above = _significant(buffer, line)
    if above is None:
        return 'font-lock-doc-face'
    if not buffer.text.line(above).split('#')[0].rstrip().endswith(':'):
        return None
    indent = _indent_of(text)
    for head in range(above, -1, -1):
        candidate = buffer.text.line(head)
        if candidate.strip() and _indent_of(candidate) < indent:
            return 'font-lock-doc-face' if _BLOCK.match(candidate) else None
    return None


def assignment(line, position, context):
    """Match, one at a time, the names on the left of an assignment."""
    statement = _ASSIGNMENT.match(line)
    if statement is None or position > statement.end('lhs'):
        return None
    return _LAST_NAME.search(line, max(position, statement.start('lhs')), statement.end('lhs'))


def _strings(context):
    if context is None:
        return ()
    info = context[0].text.meta(context[1])
    return [r for r in (info.syntax or ()) if r[2] == 's'] if info is not None else ()


def escapes(line, position, context):
    """Match escape sequences, inside strings only."""
    for start, end, _, _ in _strings(context):
        if end <= position:
            continue
        found = _ESCAPE.search(line, max(position, start), end)
        if found:
            return found
    return None


@emacs.matcher('python--font-lock-f-strings')
def f_strings(line, position, context):
    """Match the ``{...}`` fields of f-strings that open on this line."""
    for start, end, _, _ in _strings(context):
        if end <= position:
            continue
        prefix = re.search(r'[A-Za-z]*$', line[:start]).group().lower()
        if 'f' not in prefix:
            continue
        at = max(position, start)
        while True:
            found = _FIELD.search(line, at, end)
            if found is None:
                break
            if found.group(1):
                return found
            at = found.end()
    return None


@emacs.refine('python-mode')
def refine(definition):
    syntax = definition.syntax
    for quote in ('"', "'"):
        syntax.remove_string(quote)
    for quote in ('"""', "'''"):
        syntax.add_string(quote, multiline=True)
    for quote in ('"', "'"):
        syntax.add_string(quote, multiline=False)
    syntax.string_face = docstring_face
    keywords = definition.fontlock.keywords
    # The f-string matcher came from Emacs with nothing to color; give it its faces.
    keywords[:] = [k for k in keywords if k.matcher is not f_strings]
    keywords.insert(0, Keyword(f_strings, [Highlight(1, 'font-lock-escape-face', 't'),
                                           Highlight(2, 'default', 't'),
                                           Highlight(3, 'font-lock-escape-face', 't')]))
    keywords.append(Keyword(assignment, [Highlight(1, 'font-lock-variable-name-face')]))
    keywords.append(keyword(_NUMBER, 'font-lock-number-face'))
    keywords.append(keyword(rf'({_NAME})(?=\()', (1, 'font-lock-function-call-face')))
    keywords.append(Keyword(escapes, [Highlight(0, 'font-lock-escape-face', 't')]))


# -- indentation ---------------------------------------------------------------------

_code = fontlock.code_line


def open_bracket(buffer, line):
    """``(line, column)`` of the bracket still open at the start of ``line``, or None."""
    depth = 0
    for above in range(line - 1, -1, -1):
        code = _code(buffer, above)
        for column in range(len(code) - 1, -1, -1):
            c = code[column]
            if c in ')]}':
                depth += 1
            elif c in '([{':
                if depth == 0:
                    return above, column
                depth -= 1
        if depth == 0 and code.strip() and not code[:1].isspace():
            return None                     # a statement at the left margin: nothing is open
    return None


def indentation_for(buffer, line):
    """The column Python code on ``line`` should start in."""
    offset = int(buffer.get('python_indent_offset'))
    text = buffer.text
    current = text.line(line)
    fontlock.fontify(buffer, max(0, line - 1), line)
    info = buffer.text.meta(line)
    if info is not None and info.state_in is not None and info.state_in[0] == 's':
        return _indent_of(current)          # inside a string: leave it be
    bracket = open_bracket(buffer, line)
    if bracket is not None:
        opener_line, column = bracket
        code = _code(buffer, opener_line)
        base = buffer.current_indentation(text.line_start(opener_line))
        if current.lstrip()[:1] in ')]}' and current.lstrip()[:1]:
            return base if not code[column + 1:].strip() else column
        if code[column + 1:].strip():
            return column + 1               # line up under what follows the bracket
        return base + offset
    above = _significant(buffer, line)
    if above is None:
        return 0
    # Indent from the line that starts the statement, not from its continuation.
    start = above
    while True:
        opener = open_bracket(buffer, start)
        if opener is None:
            break
        start = opener[0]
    base = buffer.current_indentation(text.line_start(start))
    opens_block = _code(buffer, above).rstrip().endswith(':')
    if _DEDENTER.match(current):
        # else, except and the like sit one level out from the body before them.
        return max(0, base if opens_block else base - offset)
    if opens_block:
        return base + offset
    if _DEDENT_AFTER.match(text.line(start)):
        return max(0, base - offset)
    return base


def python_indent_line():
    """Indent the current line as Python; again to step through the other levels."""
    buffer = ed.current_buffer
    line = buffer.line_at()
    offset = int(buffer.get('python_indent_offset'))
    wanted = indentation_for(buffer, line)
    current = buffer.current_indentation()
    if ed.last_command == 'indent-for-tab-command' and ed.this_command == ed.last_command \
            and getattr(ed, '_python_tab_line', None) == (buffer, line):
        wanted = current - offset if current >= offset else indentation_for(buffer, line)
    ed._python_tab_line = (buffer, line)
    within = buffer.pt - buffer.bol() <= len(buffer.text.line(line)) - len(
        buffer.text.line(line).lstrip(' \t'))
    if wanted != current:
        buffer.indent_line_to(max(0, wanted), line)
    if within:
        buffer.back_to_indentation()


def _electric_colon():
    """Pull ``else:`` and its kin back to their block's level as the colon is typed,
    and a closing bracket alone on its line back to the line that opened it."""
    buffer = ed.current_buffer
    if buffer.major_mode is None or not buffer.major_mode.derived_from('python-mode'):
        return
    if ed.last_event is None or not buffer.eolp():
        return
    key = ed.last_event.key
    line = buffer.line_at()
    content = buffer.text.line(line)
    if not ((key == ':' and _DEDENTER.match(content)) or (key in ')]}' and
                                                         content.strip() == key)):
        return
    wanted = indentation_for(buffer, line)
    if wanted < buffer.current_indentation():
        column = buffer.pt - buffer.bol() - buffer.current_indentation()
        buffer.indent_line_to(wanted, line)
        buffer.goto_char(buffer.bol() + wanted + column)


def block_end(buffer, line):
    """Last line of the Python block that ``line`` opens."""
    from .. import fold
    text = buffer.text.line(line)
    if not _OPENER.match(text) and not text.rstrip().endswith(':'):
        return None
    return fold.indent_block(buffer, line)


@emacs.port('python-mode')
def python(buffer, definition):
    buffer.set('indent_line_function', python_indent_line)
    buffer.set('fold_block_function', block_end)
    buffer.set('defun_start_regexp', r'^[ \t]*(?:async[ \t]+)?(?:def|class)\b')
    buffer.set('comment_start', '# ')


@major_mode('python-interaction-mode', 'Python Interaction', parent='python-mode')
def python_interaction_mode(buffer):
    """For ``*scratch*``: Python, with ``C-j`` to evaluate the expression before point
    and insert its value."""


@command('p')
def python_indent_shift_left(n=1):
    """Shift the lines of the region left by one indentation level."""
    _shift(-n)


@command('p')
def python_indent_shift_right(n=1):
    """Shift the lines of the region right by one indentation level."""
    _shift(n)


def _shift(levels):
    buffer = ed.current_buffer
    offset = int(buffer.get('python_indent_offset')) * levels
    start, end = buffer.region() if buffer.region_active() else (buffer.pt, buffer.pt)
    first, last = buffer.line_at(start), buffer.line_at(max(start, end - 1))
    for line in range(first, last + 1):
        if buffer.text.line(line).strip():
            at = buffer.text.line_start(line)
            if buffer.current_indentation(at) + offset < 0:
                raise EditorError('Cannot shift further left')
    for line in range(first, last + 1):
        if buffer.text.line(line).strip():
            at = buffer.text.line_start(line)
            buffer.indent_line_to(buffer.current_indentation(at) + offset, line)
    ed.keep_mark_active = True


from ..variables import add_hook  # noqa: E402

add_hook('post_self_insert_hook', _electric_colon)

_map = major_modes['python-mode'].keymap
for _keys, _name in (('C-M-x', 'eval-defun'), ('C-c C-c', 'eval-buffer'),
                     ('C-c C-r', 'eval-region'), ('C-c C-e', 'eval-last-sexp'),
                     ('C-c C-l', 'live-reload'),
                     ('C-c <', 'python-indent-shift-left'),
                     ('C-c >', 'python-indent-shift-right')):
    _map.bind(_keys, _name)
major_modes['python-interaction-mode'].keymap.bind('C-j', 'eval-print-last-sexp')
