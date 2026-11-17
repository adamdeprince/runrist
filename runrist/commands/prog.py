# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Commands for code: balanced expressions, definitions, matching brackets,
comments, filling, completion from the buffer, and the shell."""

import re

from .. import fontlock
from ..buffer import inhibit_read_only
from ..command import command
from ..editor import ed, global_map
from ..errors import EditorError
from ..minibuffer import read_from_minibuffer
from ..mode import minor_mode
from ..variables import add_hook, defvar

defvar('defun_start_regexp', None,
       'Regular expression matching the first line of a definition, or None to treat '
       'any unindented line with an indented body as one.', local=True)
defvar('show_paren_mode', True, 'Whether the bracket matching the one at point is highlighted.')
defvar('show_paren_distance', 200000,
       'How many characters away a matching bracket is looked for when highlighting; '
       'None for no bound.  The commands that move over brackets are never bounded.')
defvar('comment_column', 32, 'Column at which a comment added to a line of code starts.',
       local=True)
defvar('dabbrev_case_fold_search', True, 'Whether dabbrev ignores case when matching.')
defvar('shell_file_name', None, 'The shell that runs shell commands; None for $SHELL.')

_OPEN, _CLOSE = '([{', ')]}'
_BRACKET = re.compile(r'[()\[\]{}]')
_PARTNER = dict(zip(_OPEN + _CLOSE, _CLOSE + _OPEN))


def buf():
    return ed.current_buffer


# -- balanced expressions ------------------------------------------------------------------

def scan_brackets(buffer, pos, forward, depth=0, limit=None):
    """Move over brackets from ``pos`` until the depth comes back to zero.

    With ``depth`` zero, the first bracket met must open (close, going
    backward) and the position just past its partner is returned; with a
    positive ``depth``, the position past the bracket that closes that many
    enclosing levels.  Returns None if the text ends first.  Brackets inside
    strings and comments do not count.
    """
    text = buffer.text
    line, column = text.pos_to_linecol(pos)
    count = text.line_count
    travelled = 0
    if forward:
        while line < count:
            code = fontlock.code_line(buffer, line)
            for found in _BRACKET.finditer(code, column):
                if found.group() in _OPEN:
                    depth += 1
                else:
                    depth -= 1
                    if depth < 0:
                        return None
                    if depth == 0:
                        return text.line_start(line) + found.end()
            travelled += len(code) - column + 1
            if limit is not None and travelled > limit:
                return None
            line += 1
            column = 0
    else:
        while line >= 0:
            code = fontlock.code_line(buffer, line)
            matches = list(_BRACKET.finditer(code, 0, column if column is not None else len(code)))
            for found in reversed(matches):
                if found.group() in _CLOSE:
                    depth += 1
                else:
                    depth -= 1
                    if depth < 0:
                        return None
                    if depth == 0:
                        return text.line_start(line) + found.start()
            travelled += (column if column is not None else len(code)) + 1
            if limit is not None and travelled > limit:
                return None
            line -= 1
            column = None
    return None


def _symbol_pattern(buffer):
    syntax = buffer.syntax
    return syntax.symbol_pattern if syntax is not None else re.compile(r'[\w.]+')


def _skip_space(buffer, forward):
    """Move over whitespace and comments."""
    while True:
        before = buffer.pt
        if forward:
            buffer.skip_chars_forward(' \t\n\r\f')
            line, column = buffer.text.pos_to_linecol(buffer.pt)
            for start, end, kind, _ in fontlock.syntax_at(buffer, line):
                if kind == 'c' and start <= column < end:
                    buffer.goto_char(buffer.text.line_start(line) + end)
        else:
            buffer.skip_chars_backward(' \t\n\r\f')
            line, column = buffer.text.pos_to_linecol(buffer.pt)
            for start, end, kind, _ in fontlock.syntax_at(buffer, line):
                if kind == 'c' and start < column <= end:
                    buffer.goto_char(buffer.text.line_start(line) + start)
        if buffer.pt == before:
            return


def _starts_expression(buffer, forward):
    """Whether the character next to point, in the direction of travel, begins
    something: a bracket, a quote or part of a symbol.  Punctuation does not."""
    char = buffer.char_after() if forward else buffer.char_before()
    if not char or char in _OPEN or char in _CLOSE:
        return True
    if _symbol_pattern(buffer).match(char):
        return True
    line, column = buffer.text.pos_to_linecol(buffer.pt)
    for start, end, kind, _ in fontlock.syntax_at(buffer, line):
        if kind == 's' and (start <= column < end if forward else start < column <= end):
            return True
    return False


def _skip_to_expression(buffer, forward):
    """Move over whitespace, comments and punctuation, as Emacs does between expressions."""
    _skip_space(buffer, forward)
    while not _starts_expression(buffer, forward):
        buffer.goto_char(buffer.pt + (1 if forward else -1))
        _skip_space(buffer, forward)


def _forward_one_sexp(buffer):
    _skip_to_expression(buffer, True)
    if buffer.eobp():
        raise EditorError('No expression follows')
    line, column = buffer.text.pos_to_linecol(buffer.pt)
    for start, end, kind, _ in fontlock.syntax_at(buffer, line):
        if kind == 's' and start <= column < end:
            target = buffer.text.line_start(line) + end
            # A string may run over several lines.
            while line + 1 < buffer.text.line_count and end == buffer.text.line_length(line):
                info = buffer.text.meta(line)
                if info is None or info.state_out is None:
                    break
                line += 1
                regions = fontlock.syntax_at(buffer, line)
                if not regions or regions[0][0] != 0:
                    break
                end = regions[0][1]
                target = buffer.text.line_start(line) + end
            buffer.goto_char(target)
            return
    char = buffer.char_after()
    if char in _OPEN:
        target = scan_brackets(buffer, buffer.pt, True)
        if target is None:
            raise EditorError('Unbalanced parentheses')
        buffer.goto_char(target)
    elif char in _CLOSE:
        raise EditorError('Containing expression ends prematurely')
    else:
        found = _symbol_pattern(buffer).match(buffer.text.snapshot(), buffer.pt, buffer.point_max)
        buffer.goto_char(found.end() if found and found.end() > buffer.pt else buffer.pt + 1)


def _backward_one_sexp(buffer):
    _skip_to_expression(buffer, False)
    if buffer.bobp():
        raise EditorError('No expression precedes')
    line, column = buffer.text.pos_to_linecol(buffer.pt)
    for start, end, kind, _ in fontlock.syntax_at(buffer, line):
        if kind == 's' and start < column <= end:
            target = buffer.text.line_start(line) + start
            while start == 0 and line > 0:
                info = buffer.text.meta(line)
                if info is None or info.state_in is None:
                    break
                line -= 1
                regions = fontlock.syntax_at(buffer, line)
                if not regions:
                    break
                start = regions[-1][0]
                target = buffer.text.line_start(line) + start
            buffer.goto_char(target)
            return
    char = buffer.char_before()
    if char in _CLOSE:
        target = scan_brackets(buffer, buffer.pt, False)
        if target is None:
            raise EditorError('Unbalanced parentheses')
        buffer.goto_char(target)
    elif char in _OPEN:
        raise EditorError('Containing expression ends prematurely')
    else:
        start = buffer.bol()
        text = buffer.text.snapshot()
        last = None
        for found in _symbol_pattern(buffer).finditer(text, start, buffer.pt):
            last = found
        if last is not None and last.end() == buffer.pt:
            buffer.goto_char(last.start())
        else:
            buffer.goto_char(buffer.pt - 1)


@command('^p')
def forward_sexp(n=1):
    """Move forward across one balanced expression."""
    b = buf()
    for _ in range(abs(n)):
        (_forward_one_sexp if n > 0 else _backward_one_sexp)(b)


@command('^p')
def backward_sexp(n=1):
    """Move backward across one balanced expression."""
    forward_sexp(-n)


@command('^p')
def forward_list(n=1):
    """Move forward across one group of brackets."""
    b = buf()
    for _ in range(abs(n)):
        target = scan_brackets(b, b.pt, n > 0)
        if target is None:
            raise EditorError('No bracketed group ' + ('follows' if n > 0 else 'precedes'))
        b.goto_char(target)


@command('^p')
def backward_list(n=1):
    """Move backward across one group of brackets."""
    forward_list(-n)


@command('^p')
def backward_up_list(n=1):
    """Move backward out of one level of brackets."""
    b = buf()
    for _ in range(abs(n)):
        target = scan_brackets(b, b.pt, n < 0, depth=1)
        if target is None:
            raise EditorError('At top level')
        b.goto_char(target)


@command('^p')
def up_list(n=1):
    """Move forward out of one level of brackets."""
    backward_up_list(-n)


@command('^p')
def down_list(n=1):
    """Move forward into one level of brackets."""
    b = buf()
    for _ in range(abs(n)):
        text = b.text
        line, column = text.pos_to_linecol(b.pt)
        while line < text.line_count:
            found = re.compile(r'[(\[{]').search(fontlock.code_line(b, line), column)
            if found:
                b.goto_char(text.line_start(line) + found.end())
                break
            line += 1
            column = 0
        else:
            raise EditorError('No bracketed group follows')


@command('p')
def kill_sexp(n=1):
    """Kill the balanced expression after point."""
    from .basic import _kill
    b = buf()
    start = b.pt
    forward_sexp(n)
    end = b.pt
    b.goto_char(start)
    _kill(start, end)


@command('p')
def backward_kill_sexp(n=1):
    """Kill the balanced expression before point."""
    kill_sexp(-n)


@command('p')
def mark_sexp(n=1):
    """Set the mark at the end of the balanced expression after point."""
    b = buf()
    here = b.pt
    if b.region_active() and ed.last_command == 'mark-sexp':
        b.goto_char(b.mark.pos)
    forward_sexp(n)
    target = b.pt
    b.goto_char(here)
    if b.region_active() and ed.last_command == 'mark-sexp':
        b.mark.set(target)
    else:
        b.push_mark(target, activate=True)
    ed.keep_mark_active = True


@command('*')
def transpose_sexps():
    """Interchange the balanced expressions around point."""
    b = buf()
    origin = b.pt
    try:
        _forward_one_sexp(b)
        second_end = b.pt
        _backward_one_sexp(b)
        second_start = b.pt
        _backward_one_sexp(b)
        first_start = b.pt
        _forward_one_sexp(b)
        first_end = b.pt
    except EditorError:
        b.goto_char(origin)
        raise EditorError("Don't have two things to transpose") from None
    first, second = b.substring(first_start, first_end), b.substring(second_start, second_end)
    b.replace_region(second_start, second_end, first)
    b.replace_region(first_start, first_end, second)
    b.goto_char(second_end)


# -- matching brackets -----------------------------------------------------------------------

_paren_cache = [None, ()]


def _paren_positions(buffer):
    """``[(position, face)]`` for the bracket at point and its partner."""
    key = (buffer, buffer.pt, buffer.modiff)
    if _paren_cache[0] == key:
        return _paren_cache[1]
    result = ()
    limit = ed.var('show_paren_distance')
    before, after = buffer.char_before(), buffer.char_after()
    if before and before in _CLOSE and not fontlock.in_string_or_comment(buffer, buffer.pt - 1) \
            and fontlock.in_string_or_comment(buffer, buffer.pt) != 's':
        partner = scan_brackets(buffer, buffer.pt, False, limit=limit)
        if partner is not None and buffer.text.char_at(partner) == _PARTNER[before]:
            result = ((buffer.pt - 1, 'show-paren-match'), (partner, 'show-paren-match'))
        elif partner is not None or limit is None:
            result = ((buffer.pt - 1, 'show-paren-mismatch'),)
    elif after and after in _OPEN and not fontlock.in_string_or_comment(buffer, buffer.pt + 1) \
            and fontlock.in_string_or_comment(buffer, buffer.pt) is None:
        partner = scan_brackets(buffer, buffer.pt, True, limit=limit)
        if partner is not None and buffer.text.char_at(partner - 1) == _PARTNER[after]:
            result = ((buffer.pt, 'show-paren-match'), (partner - 1, 'show-paren-match'))
        elif partner is not None or limit is None:
            result = ((buffer.pt, 'show-paren-mismatch'),)
    _paren_cache[0], _paren_cache[1] = key, result
    return result


def show_paren(window, buffer, line, bol, ltext):
    """Highlighter: the bracket at point and the one that matches it."""
    if window is not ed.selected_window or not ed.var('show_paren_mode'):
        return None
    found = [(pos - bol, pos - bol + 1, face, 90) for pos, face in _paren_positions(buffer)
             if bol <= pos < bol + len(ltext)]
    return found or None


if show_paren not in ed.highlighters:
    ed.highlighters.append(show_paren)


# -- definitions -------------------------------------------------------------------------------

def _defun_starts(buffer, line):
    """Whether a definition starts on ``line``."""
    from .. import fold
    text = buffer.text.line(line)
    pattern = buffer.get('defun_start_regexp')
    if pattern:
        return re.match(pattern, text) is not None
    return bool(text) and not text[0].isspace() and text[0] not in '#;/*)]}' \
        and fold.indent_block(buffer, line) is not None


def _defun_end(buffer, line):
    """Last line of the definition starting on ``line``."""
    from .. import fold
    last = fold.block_end(buffer, line)
    if last is None:
        last = fold.indent_block(buffer, line)
    if last is None:
        return line
    # A closing bracket on a line of its own belongs to the definition.
    count = buffer.text.line_count
    if last + 1 < count and buffer.text.line(last + 1).strip() in ('}', '};', ')', ']', 'end'):
        last += 1
    return last


@command('^p')
def beginning_of_defun(n=1):
    """Move backward to the beginning of a definition."""
    if n < 0:
        return end_of_defun(-n)
    b = buf()
    line = b.line_at()
    if b.pt == b.bol() or b.pt <= b.text.line_start(line) + len(b.text.line(line)) - len(
            b.text.line(line).lstrip()):
        line -= 1
    for _ in range(n):
        while line >= 0 and not _defun_starts(b, line):
            line -= 1
        if line < 0:
            b.goto_char(b.point_min)
            return
        target = line
        line -= 1
    b.goto_char(b.text.line_start(target))
    if b.get('defun_start_regexp'):
        b.back_to_indentation()


@command('^p')
def end_of_defun(n=1):
    """Move forward to the end of a definition."""
    if n < 0:
        return beginning_of_defun(-n)
    b = buf()
    text = b.text
    for _ in range(n):
        line = b.line_at()
        start = line
        while start >= 0 and not _defun_starts(b, start):
            start -= 1
        if start < 0 or _defun_end(b, start) < line or (
                _defun_end(b, start) == line and b.eolp()):
            start = line + 1
            while start < text.line_count and not _defun_starts(b, start):
                start += 1
            if start >= text.line_count:
                b.goto_char(b.point_max)
                return
        last = _defun_end(b, start)
        b.goto_char(text.line_start(last + 1) if last + 1 < text.line_count else b.point_max)


@command
def mark_defun():
    """Put the region around the definition point is in."""
    b = buf()
    end_of_defun(1)
    end = b.pt
    beginning_of_defun(1)
    b.goto_char(b.bol())
    b.push_mark(end, activate=True)
    ed.keep_mark_active = True


# -- comments ----------------------------------------------------------------------------------

def _comment_syntax(buffer):
    start = buffer.get('comment_start')
    if not start:
        raise EditorError('No comment syntax is defined for this mode')
    return start.rstrip() + ' ', (buffer.get('comment_end') or '').strip()


def _lines_of(buffer, start, end):
    return buffer.line_at(start), buffer.line_at(max(start, end - 1) if end > start else start)


def comment_lines(buffer, first, last):
    """Comment out lines ``first`` to ``last``."""
    opener, closer = _comment_syntax(buffer)
    text = buffer.text
    indents = [len(text.line(i)) - len(text.line(i).lstrip(' \t'))
               for i in range(first, last + 1) if text.line(i).strip()]
    column = min(indents, default=0)
    for line in range(first, last + 1):
        content = text.line(line)
        if not content.strip():
            continue
        at = text.line_start(line)
        if closer:
            buffer.insert_at(at + len(content), ' ' + closer)
        buffer.insert_at(at + column, opener)


def uncomment_lines(buffer, first, last):
    opener, closer = _comment_syntax(buffer)
    bare = opener.strip()
    pattern = re.compile(r'([ \t]*)' + re.escape(bare) + ' ?')
    for line in range(first, last + 1):
        content = buffer.text.line(line)
        found = pattern.match(content)
        if not found:
            continue
        at = buffer.text.line_start(line)
        if closer and content.rstrip().endswith(closer):
            tail = len(content.rstrip())
            cut = tail - len(closer)
            while cut > 0 and content[cut - 1] == ' ':
                cut -= 1
            buffer.delete_region(at + cut, at + tail)
        buffer.delete_region(at + found.end(1), at + found.end())


def _all_commented(buffer, first, last):
    bare = _comment_syntax(buffer)[0].strip()
    lines = [buffer.text.line(i) for i in range(first, last + 1) if buffer.text.line(i).strip()]
    return bool(lines) and all(line.lstrip().startswith(bare) for line in lines)


@command('*r')
def comment_region(start, end):
    """Comment out each line of the region."""
    b = buf()
    comment_lines(b, *_lines_of(b, start, end))


@command('*r')
def uncomment_region(start, end):
    """Remove comment markers from each line of the region."""
    b = buf()
    uncomment_lines(b, *_lines_of(b, start, end))


@command('*r')
def comment_or_uncomment_region(start, end):
    """Comment out the region, or uncomment it if it is all comments."""
    b = buf()
    first, last = _lines_of(b, start, end)
    if _all_commented(b, first, last):
        uncomment_lines(b, first, last)
    else:
        comment_lines(b, first, last)


@command('*P')
def comment_dwim(arg=None):
    """Do what is wanted with comments here.

    With an active region, comment or uncomment it.  On a blank line, start a
    comment.  Otherwise go to the comment on this line, adding one if it has none."""
    b = buf()
    if b.region_active():
        comment_or_uncomment_region(*b.region())
        return
    opener, closer = _comment_syntax(b)
    line = b.line_at()
    content = b.text.line(line)
    if not content.strip():
        from .basic import indent_according_to_mode
        if b.get('indent_line_function'):
            indent_according_to_mode()
        b.goto_char(b.eol())
        b.insert(opener)
        if closer:
            here = b.pt
            b.insert(' ' + closer)
            b.goto_char(here)
        return
    for start, end, kind, _ in fontlock.syntax_at(b, line):
        if kind == 'c':
            b.goto_char(b.text.line_start(line) + start)
            b.skip_chars_forward(re.escape(opener.strip()) + ' ', b.eol())
            return
    b.goto_char(b.eol())
    column = b.current_column()
    target = max(int(b.get('comment_column')), column + 1)
    b.insert(' ' * (target - column) + opener)
    if closer:
        here = b.pt
        b.insert(' ' + closer)
        b.goto_char(here)


@command('*p')
def comment_line(n=1):
    """Comment or uncomment the current line, or the lines of the region, and move down."""
    b = buf()
    if b.region_active():
        first, last = _lines_of(b, *b.region())
    else:
        first = b.line_at()
        last = min(b.text.line_count - 1, first + max(1, n) - 1)
    if _all_commented(b, first, last):
        uncomment_lines(b, first, last)
    else:
        comment_lines(b, first, last)
    b.goto_char(b.text.line_start(last))
    b.forward_line(1)
    b.mark_active = False


# -- filling -----------------------------------------------------------------------------------

@command('*P')
def fill_paragraph(arg=None):
    """Fill the paragraph at point to ``fill_column``; with an argument, justify it too."""
    import textwrap
    b = buf()
    text = b.text
    line = b.line_at()
    if not text.line(line).strip():
        return
    first = last = line
    while first > 0 and text.line(first - 1).strip():
        first -= 1
    while last + 1 < text.line_count and text.line(last + 1).strip():
        last += 1
    lines = [text.line(i) for i in range(first, last + 1)]
    marker = re.match(r'[ \t]*(?:(?:#+|;+|//+|--|%+|>+|\*)[ \t]*)?', lines[0]).group()
    prefix = marker if all(l.startswith(marker) for l in lines) else re.match(
        r'[ \t]*', lines[0]).group()
    words = ' '.join(l[len(prefix):].strip() for l in lines)
    width = max(len(prefix) + 8, int(b.get('fill_column')))
    double = bool(ed.var('sentence_end_double_space'))
    if double:
        words = re.sub(r'([.?!][\]"\')]*) (?=[A-Z("\'])', r'\1  ', re.sub(r' +', ' ', words))
    filled = textwrap.wrap(words, width=width, initial_indent=prefix, subsequent_indent=prefix,
                           break_long_words=False, break_on_hyphens=False,
                           fix_sentence_endings=False) or [prefix.rstrip()]
    column = b.pt - b.bol()
    offset = sum(len(l) + 1 for l in lines[:line - first]) + column
    new = '\n'.join(filled)
    old = '\n'.join(lines)
    if new != old:
        b.replace_region(text.line_start(first), text.line_end(last), new)
        # Keep point by the same word: count the non-space characters before it.
        seen = len(re.sub(r'\s', '', old[:offset]))
        at = 0
        for at, char in enumerate(new):
            if seen <= 0:
                break
            if not char.isspace():
                seen -= 1
        else:
            at = len(new)
        b.goto_char(b.text.line_start(first) + at)


@command(interactive=lambda: [ed.current_prefix_arg])
def set_fill_column(arg=None):
    """Set ``fill_column`` to the argument, or to the column point is in."""
    b = buf()
    column = arg if isinstance(arg, int) else b.current_column()
    b.set('fill_column', column)
    ed.message(f'Fill column set to {column}', log=False)


# -- completing from the buffer ------------------------------------------------------------------

_dabbrev = {}


@command('*')
def dabbrev_expand():
    """Complete the word before point from words elsewhere in this and other buffers.

    Repeat for the next possibility."""
    b = buf()
    state = _dabbrev
    if ed.last_command == 'dabbrev-expand' and state.get('buffer') is b \
            and state.get('end') == b.pt:
        b.delete_region(state['start'] + len(state['stem']), b.pt)
    else:
        found = re.compile(r'[\w]+$').search(b.substring(b.bol(), b.pt))
        if not found:
            raise EditorError('No dynamic abbreviation to expand')
        stem = found.group()
        state.clear()
        state.update(buffer=b, stem=stem, start=b.pt - len(stem), tried={stem}, queue=None)
    stem = state['stem']
    if state['queue'] is None:
        flags = re.IGNORECASE if ed.var('dabbrev_case_fold_search') and stem == stem.lower() else 0
        pattern = re.compile(r'(?<![\w])' + re.escape(stem) + r'[\w]+', flags)
        text = b.text.snapshot()
        here = state['start']
        before = [m.group() for m in pattern.finditer(text, 0, here)][::-1]
        after = [m.group() for m in pattern.finditer(text, here + len(stem))]
        others = []
        for other in ed.buffers:
            if other is not b and not other.temporary:
                others.extend(m.group() for m in pattern.finditer(other.text.snapshot()))
        state['queue'] = iter(before + after + others)
    for candidate in state['queue']:
        if candidate in state['tried']:
            continue
        state['tried'].add(candidate)
        b.insert(candidate[len(stem):])
        state['end'] = b.pt
        return
    state['end'] = b.pt
    state['queue'] = None
    state['tried'] = {stem}
    raise EditorError(f'No further dynamic expansion for "{stem}" found')


# -- the shell -----------------------------------------------------------------------------------

def _run_shell(text, stdin=None):
    import os
    import subprocess
    shell = ed.var('shell_file_name') or os.environ.get('SHELL') or '/bin/sh'
    try:
        done = subprocess.run([shell, '-c', text], input=stdin, capture_output=True,
                              cwd=buf().directory or None, text=True, errors='replace')
    except OSError as error:
        raise EditorError(f'Cannot run {shell}: {error.strerror}') from None
    return done.stdout + done.stderr, done.returncode


def _show_output(output, status, command_text):
    stripped = output.rstrip('\n')
    outcome = 'succeeded' if status == 0 else f'failed with code {status}'
    if not stripped:
        ed.message(f'(Shell command {outcome} with no output)', log=False)
    elif stripped.count('\n') < 6 and len(stripped) < 600:
        ed.message(stripped)
    else:
        buffer = ed.get_buffer_create('*Shell Command Output*')
        with inhibit_read_only():
            buffer.erase()
            buffer.insert(output)
        buffer.goto_char(0)
        buffer.set_modified(False)
        ed.display_buffer(buffer)
        ed.message(f'Shell command {outcome}', log=False)


@command(interactive=lambda: [read_from_minibuffer('Shell command: ', history='shell'),
                              ed.current_prefix_arg])
def shell_command(command_text, insert=None):
    """Run a shell command and show its output.  With a prefix argument, insert the output."""
    output, status = _run_shell(command_text)
    if insert is not None:
        b = buf()
        start = b.pt
        b.insert(output)
        b.push_mark(b.pt)
        b.goto_char(start)
    else:
        _show_output(output, status, command_text)


def _read_region_command():
    b = buf()
    start, end = b.region()
    return [start, end, read_from_minibuffer('Shell command on region: ', history='shell'),
            ed.current_prefix_arg]


@command(interactive=_read_region_command)
def shell_command_on_region(start, end, command_text, replace=None):
    """Run a shell command with the region as its input.

    With a prefix argument, replace the region with the command's output."""
    b = buf()
    output, status = _run_shell(command_text, b.substring(start, end))
    if replace is not None:
        b.replace_region(start, end, output)
        b.goto_char(start + len(output))
    else:
        _show_output(output, status, command_text)


# -- brackets that close themselves ----------------------------------------------------------------

@minor_mode('electric-pair-mode', '', global_=True)
def electric_pair_mode(buffer, on):
    """Typing an opening bracket or quote also inserts the one that closes it."""


def _electric_pair():
    if not ed.var('electric_pair_mode') or ed.last_event is None:
        return
    b = buf()
    char = ed.last_event.key
    pairs = {'(': ')', '[': ']', '{': '}', '"': '"'}
    if char in pairs and (b.eolp() or b.char_after() in ' \t)]}'):
        if char == '"' and fontlock.in_string_or_comment(b, b.pt - 1):
            return
        here = b.pt
        b.insert(pairs[char])
        b.goto_char(here)
    elif char in ')]}"' and b.char_after() == char and b.char_before(b.pt - 1) != '\\':
        b.delete_region(b.pt, b.pt + 1)


add_hook('post_self_insert_hook', _electric_pair)

for _keys, _name in (
        ('C-M-f', 'forward-sexp'), ('C-M-<right>', 'forward-sexp'),
        ('C-M-b', 'backward-sexp'), ('C-M-<left>', 'backward-sexp'),
        ('C-M-n', 'forward-list'), ('C-M-p', 'backward-list'),
        ('C-M-u', 'backward-up-list'), ('C-M-<up>', 'backward-up-list'),
        ('C-M-d', 'down-list'), ('C-M-<down>', 'down-list'),
        ('C-M-k', 'kill-sexp'), ('C-M-DEL', 'backward-kill-sexp'),
        ('C-M-SPC', 'mark-sexp'), ('C-M-t', 'transpose-sexps'),
        ('C-M-a', 'beginning-of-defun'), ('C-M-<home>', 'beginning-of-defun'),
        ('C-M-e', 'end-of-defun'), ('C-M-<end>', 'end-of-defun'), ('C-M-h', 'mark-defun'),
        ('M-;', 'comment-dwim'), ('C-x C-;', 'comment-line'),
        ('M-q', 'fill-paragraph'), ('C-x f', 'set-fill-column'),
        ('M-/', 'dabbrev-expand'),
        ('M-!', 'shell-command'), ('M-|', 'shell-command-on-region')):
    global_map.bind(_keys, _name)
