# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Moving, inserting, deleting, killing and yanking, the mark, undo, and
prefix arguments: the commands every buffer has, on the keys Emacs gives them."""

import re

from ..buffer import Marker
from ..command import command
from ..editor import ed, global_map
from ..errors import (BeginningOfBuffer, BufferReadOnly, EditorError, EndOfBuffer, Quit)
from ..keymap import Keymap
from ..keys import self_insert_text, split_key
from ..variables import defhook, defvar

defvar('fill_column', 70, 'Column beyond which filling breaks lines.', local=True)
defvar('sentence_end_double_space', True,
       'Whether a sentence must end with two spaces, or a line end, to count.')
defvar('next_screen_context_lines', 2,
       'How many lines stay on screen when scrolling by a screenful.')
defvar('delete_selection_mode', False,
       'Whether typed text replaces the active region.')
defvar('kill_whole_line', False,
       'Whether killing a line at its beginning takes the newline too.')
defvar('electric_indent_mode', True,
       'Whether a new line is indented as the major mode sees fit.')
defvar('indent_line_function', None,
       'Function of no arguments that indents the current line, or None.', local=True)
defvar('require_final_newline', True,
       'Whether a file is given a final newline when saved without one.', local=True)
defhook('post_self_insert_hook', 'Functions run after a character is typed into a buffer.')

_WORD = re.compile(r'[^\W_]+')
_LINE_MOTION = ('next-line', 'previous-line')
universal_argument_map = Keymap('universal-argument-map')


def buf():
    return ed.current_buffer


def numeric(raw):
    return ed.prefix_numeric_value(raw)


# -- moving ------------------------------------------------------------------------------

@command('^p')
def forward_char(n=1):
    """Move point N characters forward."""
    b = buf()
    target = b.pt + n
    b.goto_char(target)
    if target > b.point_max:
        raise EndOfBuffer()
    if target < b.point_min:
        raise BeginningOfBuffer()


@command('^p')
def backward_char(n=1):
    """Move point N characters backward."""
    forward_char(-n)


def _line_move(n):
    b = buf()
    w = ed.selected_window
    if w.buffer is not b:
        column = b.current_column()
        b.forward_line(n)
        b.move_to_column(column)
        return
    if ed.last_command not in _LINE_MOTION:
        w.goal_column = None
    pos, goal, short = ed.display.vertical_motion(w, b.pt, n, w.goal_column)
    w.goal_column = goal
    if short:
        # No line to move to: go to the very end or beginning, as Emacs does.
        pos = b.point_max if n > 0 else b.point_min
    b.goto_char(pos)


@command('^p')
def next_line(n=1):
    """Move point N lines down the screen, keeping to the same column."""
    b = buf()
    before = b.pt
    _line_move(n)
    if b.pt == before and n:
        raise EndOfBuffer() if n > 0 else BeginningOfBuffer()


@command('^p')
def previous_line(n=1):
    """Move point N lines up the screen, keeping to the same column."""
    b = buf()
    before = b.pt
    _line_move(-n)
    if b.pt == before and n:
        raise BeginningOfBuffer() if n > 0 else EndOfBuffer()


@command('^p')
def move_beginning_of_line(n=1):
    """Move point to the beginning of the line."""
    b = buf()
    if n != 1:
        b.forward_line(n - 1)
    b.goto_char(b.bol())


@command('^p')
def move_end_of_line(n=1):
    """Move point to the end of the line."""
    b = buf()
    if n != 1:
        b.forward_line(n - 1)
    b.goto_char(b.eol())


@command('^')
def back_to_indentation():
    """Move point to the first non-blank character of the line."""
    buf().back_to_indentation()


def _forward_word(b, n):
    for _ in range(abs(n)):
        if n > 0:
            if b.re_search_forward(_WORD, noerror=True) is None:
                b.goto_char(b.point_max)
                return False
        else:
            if b.re_search_backward(_WORD, noerror=True) is None:
                b.goto_char(b.point_min)
                return False
    return True


@command('^p')
def forward_word(n=1):
    """Move point forward over N words."""
    _forward_word(buf(), n)


@command('^p')
def backward_word(n=1):
    """Move point backward over N words."""
    _forward_word(buf(), -n)


@command('^P')
def beginning_of_buffer(arg=None):
    """Move point to the beginning of the buffer, leaving the mark where it was.

    With a numeric argument N, move N tenths of the way in."""
    b = buf()
    if not b.region_active():
        b.push_mark()
    if isinstance(arg, int):
        b.goto_char(b.point_min + (b.point_max - b.point_min) * arg // 10)
        b.goto_char(b.bol())
    else:
        b.goto_char(b.point_min)


@command('^P')
def end_of_buffer(arg=None):
    """Move point to the end of the buffer, leaving the mark where it was."""
    b = buf()
    if not b.region_active():
        b.push_mark()
    if isinstance(arg, int):
        b.goto_char(b.point_max - (b.point_max - b.point_min) * arg // 10)
        b.goto_char(b.bol())
    else:
        b.goto_char(b.point_max)


def _blank(b, line):
    return not b.text.line(line).strip()


def _paragraph_end(b, line):
    last = b.line_at(b.point_max)
    while line <= last and _blank(b, line):
        line += 1
    while line <= last and not _blank(b, line):
        line += 1
    return line, last


def _paragraph_start(b, line):
    first = b.line_at(b.point_min)
    while line > first and _blank(b, line):
        line -= 1
    while line > first and not _blank(b, line - 1):
        line -= 1
    return line, first


@command('^p')
def forward_paragraph(n=1):
    """Move point to the end of the paragraph."""
    if n < 0:
        return backward_paragraph(-n)
    b = buf()
    for _ in range(n):
        line, last = _paragraph_end(b, b.line_at())
        b.goto_char(b.text.line_start(line) if line <= last else b.point_max)


@command('^p')
def backward_paragraph(n=1):
    """Move point to the beginning of the paragraph."""
    if n < 0:
        return forward_paragraph(-n)
    b = buf()
    for _ in range(n):
        line = b.line_at()
        if b.bolp() and line > b.line_at(b.point_min):
            line -= 1
        line, first = _paragraph_start(b, line)
        if line > first and _blank(b, line - 1):
            line -= 1
        b.goto_char(max(b.point_min, b.text.line_start(line)))


def _sentence_end():
    if ed.var('sentence_end_double_space'):
        return re.compile(r'[.?!…][\]"\')}»”’]*(?=$|\t|  )', re.MULTILINE)
    return re.compile(r'[.?!…][\]"\')}»”’]*(?=$|[ \t])', re.MULTILINE)


@command('^p')
def forward_sentence(n=1):
    """Move point to the end of the sentence."""
    if n < 0:
        return backward_sentence(-n)
    b = buf()
    pattern = _sentence_end()
    for _ in range(n):
        line, last = _paragraph_end(b, b.line_at())
        bound = b.text.line_end(line - 1) if line > 0 else b.point_max
        bound = max(bound, b.pt)
        if b.re_search_forward(pattern, bound, noerror=True) is None:
            b.goto_char(bound)


@command('^p')
def backward_sentence(n=1):
    """Move point to the beginning of the sentence."""
    if n < 0:
        return forward_sentence(-n)
    b = buf()
    pattern = _sentence_end()
    for _ in range(n):
        origin = b.pt
        b.skip_chars_backward(' \t\n')
        line, _ = _paragraph_start(b, b.line_at())
        bound = b.text.line_start(line)
        here = b.pt
        while True:
            match = b.re_search_backward(pattern, bound, noerror=True)
            if match is None:
                b.goto_char(bound)
                b.skip_chars_forward(' \t\n', origin)
                break
            b.goto_char(match.end())
            b.skip_chars_forward(' \t\n', here)
            if b.pt < origin and b.pt < here:
                break
            b.goto_char(match.start())
            if match.start() <= bound:
                b.goto_char(bound)
                break


@command('^P')
def scroll_up_command(arg=None):
    """Scroll the text up a screenful, or ARG lines."""
    b = buf()
    w = ed.selected_window
    display = ed.display
    display.geometry(w)
    context = int(ed.var('next_screen_context_lines'))
    if arg == '-':
        rows = -max(1, w.body_h - context)
    elif arg is None:
        rows = max(1, w.body_h - context)
    else:
        rows = numeric(arg)
    moved = display.scroll(w, rows)
    if not moved:
        edge = b.point_max if rows > 0 else b.point_min
        if b.pt == edge:
            raise EndOfBuffer() if rows > 0 else BeginningOfBuffer()
        b.goto_char(edge)
        return
    b.goto_char(display.point_into_view(w, b.pt))


@command('^P')
def scroll_down_command(arg=None):
    """Scroll the text down a screenful, or ARG lines."""
    if arg == '-':
        scroll_up_command(None)
    elif arg is None:
        scroll_up_command('-')
    else:
        scroll_up_command(-numeric(arg))


@command('P')
def scroll_other_window(arg=None):
    """Scroll the next window up a screenful, or ARG lines."""
    windows = ed.frame.windows()
    here = ed.active_window()
    if len(windows) < 2:
        raise EditorError('There is no other window')
    other = windows[(windows.index(here) + 1) % len(windows)]
    display = ed.display
    display.geometry(other)
    context = int(ed.var('next_screen_context_lines'))
    if arg == '-':
        rows = -max(1, other.body_h - context)
    elif arg is None:
        rows = max(1, other.body_h - context)
    else:
        rows = numeric(arg)
    display.scroll(other, rows)
    other.pointm.set(display.point_into_view(other, other.pointm.pos))


@command('P')
def scroll_other_window_down(arg=None):
    """Scroll the next window down a screenful, or ARG lines."""
    scroll_other_window('-' if arg is None else -numeric(arg))


@command('P')
def recenter_top_bottom(arg=None):
    """Scroll so point is in the middle of the window; again for the top, then the bottom.

    With a numeric argument, put point on that line of the window.  Also
    redraws the screen."""
    w = ed.selected_window
    if isinstance(arg, list):
        w.recenter = max(0, w.body_h // 2)
    elif arg is not None:
        w.recenter = numeric(arg)
    else:
        cycle = getattr(w, '_recenter_cycle', 0) if ed.last_command == 'recenter-top-bottom' else 0
        margin = min(int(ed.var('scroll_margin')), max(0, (w.body_h - 1) // 2))
        w.recenter = (max(0, w.body_h // 2), margin, -1 - margin)[cycle % 3]
        w._recenter_cycle = cycle + 1
        ed.display.invalidate()


@command('P')
def move_to_window_line_top_bottom(arg=None):
    """Move point to the middle line of the window; again for the top, then the bottom."""
    b = buf()
    w = ed.selected_window
    shown = [row for row in w.rows if row[0] >= 0]
    if not shown:
        return
    if arg is not None:
        n = numeric(arg)
        index = n if n >= 0 else len(shown) + n
    else:
        cycle = getattr(w, '_line_cycle', 0) if ed.last_command == \
            'move-to-window-line-top-bottom' else 0
        index = (len(shown) // 2, 0, len(shown) - 1)[cycle % 3]
        w._line_cycle = cycle + 1
    line, a, _ = shown[max(0, min(index, len(shown) - 1))]
    b.goto_char(b.text.line_start(line) + a)


@command(interactive=lambda: [_read_line_number()])
def goto_line(line):
    """Move point to the beginning of line LINE, counting from one."""
    b = buf()
    b.push_mark()
    b.goto_line(int(line))


def _read_line_number():
    raw = ed.current_prefix_arg
    if isinstance(raw, int):
        return raw
    from ..minibuffer import read_number
    return read_number('Goto line: ')


@command('nGoto char: ')
def goto_char(position):
    """Move point to character position POSITION, counting from one."""
    b = buf()
    b.push_mark()
    b.goto_char(b.point_min + int(position) - 1)


# -- inserting and deleting ------------------------------------------------------------------

def _delete_active_region(b):
    if b.region_active() and ed.var('delete_selection_mode'):
        b.delete_region(*b.region())
        return True
    return False


@command('*p')
def self_insert_command(n=1):
    """Insert the character typed."""
    key = ed.last_event.key if ed.last_event is not None else ''
    text = self_insert_text(key)
    if text is None:
        raise EditorError(f'{key} is undefined')
    if n < 0:
        raise EditorError('Negative repetition argument')
    b = buf()
    _delete_active_region(b)
    b.insert(text * n)
    ed.run_hooks('post_self_insert_hook')


@command('*P')
def newline(arg=None):
    """Insert a newline, and indent the new line if the mode knows how."""
    b = buf()
    _delete_active_region(b)
    b.insert('\n' * numeric(arg))
    if arg is None and ed.var('electric_indent_mode') and b.get('indent_line_function'):
        try:
            # Blank lines keep no indentation of their own.
            previous = b.line_at() - 1
            if previous >= 0 and not b.text.line(previous).strip():
                b.indent_line_to(0, previous)
            indent_according_to_mode()
        except EditorError:
            pass
    ed.run_hooks('post_self_insert_hook')


@command('*p')
def open_line(n=1):
    """Insert a newline after point, leaving point where it is."""
    b = buf()
    here = b.pt
    b.insert('\n' * n)
    b.goto_char(here)


@command('*')
def newline_and_indent():
    """Insert a newline, then indent."""
    b = buf()
    start = b.pt
    b.skip_chars_backward(' \t', b.bol())
    b.delete_region(b.pt, start)
    b.insert('\n')
    indent_according_to_mode()


@command('*')
def electric_newline_and_maybe_indent():
    """Insert a newline, indenting unless electric indentation already does."""
    if ed.var('electric_indent_mode'):
        buf().insert('\n')
    else:
        newline_and_indent()


def indent_according_to_mode():
    """Indent the current line the way the major mode wants."""
    b = buf()
    function = b.get('indent_line_function')
    if function is None:
        indent_relative_line()
    else:
        function()


def indent_relative_line():
    """Give the current line the indentation of the last non-blank line above it."""
    b = buf()
    line = b.line_at()
    column = 0
    for above in range(line - 1, -1, -1):
        if b.text.line(above).strip():
            column = b.current_indentation(b.text.line_start(above))
            break
    b.indent_line_to(column)


@command('*P')
def indent_for_tab_command(arg=None):
    """Indent the current line, or the region if it is active."""
    b = buf()
    if b.region_active():
        indent_region(*b.region())
        return
    function = b.get('indent_line_function')
    if function is None:
        tab_to_tab_stop()
    else:
        function()


@command('*')
def tab_to_tab_stop():
    """Insert space up to the next tab stop."""
    b = buf()
    if b.get('indent_tabs_mode'):
        b.insert('\t')
    else:
        width = int(b.get('tab_width'))
        b.insert(' ' * (width - b.current_column() % width))


@command('*r')
def indent_region(start, end):
    """Indent each line of the region."""
    b = buf()
    first, last = b.line_at(start), b.line_at(max(start, end - 1))
    function = b.get('indent_line_function')
    with b.save_excursion():
        for line in range(first, last + 1):
            if not b.text.line(line).strip():
                b.indent_line_to(0, line)
                continue
            b.goto_char(b.text.line_start(line))
            if function is None:
                indent_relative_line()
            else:
                function()
    ed.keep_mark_active = False


@command(interactive=lambda: [*buf().region(), ed.current_prefix_arg])
def indent_rigidly(start, end, arg=None):
    """Shift the lines of the region sideways by ARG columns.

    With no argument, the arrow keys shift them until another key is typed."""
    b = buf()

    def shift(columns):
        first, last = b.line_at(b.region()[0]), b.line_at(max(b.region()[0], b.region()[1] - 1))
        for line in range(first, last + 1):
            if b.text.line(line).strip():
                at = b.text.line_start(line)
                b.indent_line_to(max(0, b.current_indentation(at) + columns), line)

    if arg is not None:
        shift(numeric(arg))
        return
    keymap = Keymap()
    step = int(b.get('tab_width'))
    for keys, columns in (('<left>', -1), ('<right>', 1), ('S-<left>', -step),
                          ('S-<right>', step), ('<backtab>', -step), ('TAB', step)):
        def run(columns=columns):
            shift(columns)
            ed.keep_mark_active = True
        keymap.bind(keys, run)
    ed.keep_mark_active = True
    ed.message('Indent region with <left>, <right>, S-<left>, or S-<right>.', log=False)
    ed.set_transient_map(keymap, keep=True)


@command('*p')
def delete_forward_char(n=1):
    """Delete the N characters after point, or the region if it is active."""
    b = buf()
    if b.region_active() and abs(n) == 1 and ed.current_prefix_arg is None:
        b.delete_region(*b.region())
        return
    delete_char(n)


@command('*p')
def delete_char(n=1):
    """Delete the N characters after point."""
    b = buf()
    if ed.current_prefix_arg is not None:
        _kill(b.pt, _bounded(b, b.pt + n))
    else:
        b.delete_char(n)


@command('*p')
def delete_backward_char(n=1):
    """Delete the N characters before point."""
    b = buf()
    if b.region_active() and abs(n) == 1 and ed.current_prefix_arg is None:
        b.delete_region(*b.region())
        return
    if ed.current_prefix_arg is not None:
        _kill(_bounded(b, b.pt - n), b.pt)
    else:
        b.delete_char(-n)


def _bounded(b, pos):
    if pos < b.point_min:
        raise BeginningOfBuffer()
    if pos > b.point_max:
        raise EndOfBuffer()
    return pos


@command('*')
def delete_horizontal_space():
    """Delete all spaces and tabs around point."""
    b = buf()
    start = b.pt
    b.skip_chars_backward(' \t', b.bol())
    left = b.pt
    b.goto_char(start)
    b.skip_chars_forward(' \t', b.eol())
    b.delete_region(left, b.pt)


@command('*p')
def just_one_space(n=1):
    """Leave N spaces around point, deleting the rest."""
    b = buf()
    start = b.pt
    b.skip_chars_backward(' \t', b.bol())
    left = b.pt
    b.goto_char(start)
    b.skip_chars_forward(' \t', b.eol())
    b.replace_region(left, b.pt, ' ' * max(0, n))
    b.goto_char(left + max(0, n))


@command('*P')
def delete_indentation(arg=None):
    """Join this line to the one before, with one space between.

    With an argument, join the next line to this one."""
    b = buf()
    if arg is not None:
        b.forward_line(1)
    start = b.bol()
    if start <= b.point_min:
        return
    b.goto_char(start - 1)
    b.delete_region(start - 1, start)
    delete_horizontal_space()
    if not b.bolp() and not b.eolp() and b.char_after() not in ')]}' \
            and b.char_before() not in '([{':
        b.insert(' ')
        b.goto_char(b.pt - 1)


@command('*')
def delete_blank_lines():
    """On a blank line, delete the blank lines around it, leaving one.

    On an isolated blank line, delete it.  On a non-blank line, delete the
    blank lines that follow."""
    b = buf()
    text = b.text
    line = b.line_at()
    count = text.line_count
    if _blank(b, line):
        first = line
        while first > 0 and _blank(b, first - 1):
            first -= 1
        last = line
        while last + 1 < count and _blank(b, last + 1):
            last += 1
        if first == last:
            start = text.line_start(first)
            end = text.line_start(first + 1) if first + 1 < count else text.line_end(first)
            b.delete_region(start, end)
        else:
            b.delete_region(text.line_start(first), text.line_start(last))
            b.goto_char(text.line_start(first))
    else:
        last = line
        while last + 1 < count and _blank(b, last + 1):
            last += 1
        if last > line:
            end = text.line_start(last + 1) if last + 1 < count else text.line_end(last)
            b.delete_region(text.line_start(line + 1), end)


@command('*')
def delete_trailing_whitespace():
    """Delete spaces and tabs at the ends of lines, and blank lines at the end of the buffer."""
    b = buf()
    start, end = b.region() if b.region_active() else (b.point_min, b.point_max)
    with b.save_excursion():
        limit = Marker(b, end, True)
        b.goto_char(start)
        pattern = re.compile(r'[ \t]+$', re.MULTILINE)
        while True:
            match = b.re_search_forward(pattern, limit.pos, noerror=True)
            if match is None:
                break
            b.delete_region(match.start(), match.end())
        limit.delete()
        if not b.region_active():
            b.goto_char(b.point_max)
            b.skip_chars_backward('\n')
            if b.point_max - b.pt > 1:
                b.delete_region(b.pt + 1, b.point_max)


@command('*')
def quoted_insert():
    """Read the next key and insert the character it stands for, whatever it is bound to."""
    ed.echo = 'C-q-'
    try:
        event = ed.read_event()
    finally:
        ed.echo = None
    key = event.key
    mods, base = split_key(key)
    if base == 'RET':
        text = '\r'
    elif base == 'TAB':
        text = '\t'
    elif base == 'SPC':
        text = ' '
    elif base == 'ESC':
        text = '\x1b'
    elif base == 'DEL':
        text = '\x7f'
    elif len(base) == 1:
        text = base
    else:
        raise EditorError(f'{key} does not stand for a character')
    if 'C' in mods and len(text) == 1:
        if text == 'j':
            text = '\n'
        elif '@' <= text.upper() <= '_':
            text = chr(ord(text.upper()) - 64)
        elif text == '?':
            text = '\x7f'
    buf().insert(text * numeric(ed.current_prefix_arg))


# -- killing and yanking -----------------------------------------------------------------------

def _kill(start, end, copy=False):
    """Kill the text between two positions, adding to the last kill if the
    command before this one also killed."""
    b = buf()
    start, end = (start, end) if start <= end else (end, start)
    backward = end <= b.pt and start < b.pt and not copy
    text = b.substring(start, end)
    if ed.last_command == 'kill-region' and text:
        ed.kill_append(text, before=backward)
    elif text:
        ed.kill_new(text)
    ed.this_command = 'kill-region'
    if copy:
        return text
    if b.read_only:
        raise BufferReadOnly(b.name)
    b.delete_region(start, end)
    return text


@command('r')
def kill_region(start, end):
    """Kill the region: delete it and save it in the kill ring."""
    _kill(start, end)


@command('r')
def kill_ring_save(start, end):
    """Save the region in the kill ring without deleting it."""
    b = buf()
    keep = ed.last_command
    _kill(start, end, copy=True)
    ed.this_command = 'kill-ring-save' if keep != 'kill-region' else 'kill-region'
    b.mark_active = False
    ed.message('Saved text to the kill ring', log=False)


@command('P')
def kill_line(arg=None):
    """Kill the rest of the line; at the end of a line, kill the newline.

    With a numeric argument, kill that many lines from point."""
    b = buf()
    start = b.pt
    if arg is None:
        if b.eobp():
            raise EndOfBuffer()
        end = b.eol()
        rest = b.substring(start, end)
        if not rest.strip() or (ed.var('kill_whole_line') and b.bolp()):
            end = min(b.point_max, end + 1)
    else:
        b.forward_line(numeric(arg))
        end = b.pt
        b.goto_char(start)
    _kill(start, end)


@command('p')
def kill_whole_line(n=1):
    """Kill the current line, including its newline."""
    b = buf()
    start = b.bol()
    b.goto_char(start)
    b.forward_line(n)
    end = b.pt
    if end == b.point_max and start > b.point_min and b.char_before(end) != '\n':
        start -= 1                      # the last line: take the newline before it
    b.goto_char(start)
    _kill(start, end)


@command('p')
def kill_word(n=1):
    """Kill characters forward to the end of a word."""
    b = buf()
    start = b.pt
    _forward_word(b, n)
    end = b.pt
    b.goto_char(start)
    _kill(start, end)


@command('p')
def backward_kill_word(n=1):
    """Kill characters backward to the beginning of a word."""
    kill_word(-n)


@command('p')
def kill_sentence(n=1):
    """Kill from point to the end of the sentence."""
    b = buf()
    start = b.pt
    forward_sentence(n)
    end = b.pt
    b.goto_char(start)
    _kill(start, end)


def _read_zap():
    from ..minibuffer import read_char
    return [numeric(ed.current_prefix_arg), read_char('Zap to char: ')]


@command(interactive=_read_zap)
def zap_to_char(n, char):
    """Kill up to and including the Nth occurrence of CHAR."""
    b = buf()
    start = b.pt
    if n >= 0:
        b.search_forward(char, count=n or 1)
    else:
        b.search_backward(char, count=-n)
    end = b.pt
    b.goto_char(start)
    _kill(start, end)


@command('*P')
def yank(arg=None):
    """Reinsert the last stretch of killed text.

    With a numeric argument N, reinsert the Nth most recent kill.  With just
    ``C-u``, leave point at the beginning of the text and the mark at its end."""
    b = buf()
    if isinstance(arg, int):
        text = ed.current_kill(arg - 1)
    elif arg == '-':
        text = ed.current_kill(-2)
    else:
        text = ed.current_kill(0)
    _delete_active_region(b)
    start = b.pt
    b.insert(text)
    end = b.pt
    if isinstance(arg, list):
        b.push_mark(end)
        b.goto_char(start)
    else:
        b.push_mark(start)
    ed.yanked = (b, start, end)
    ed.this_command = 'yank'


@command('*p')
def yank_pop(n=1):
    """Replace the text just yanked with an earlier kill.

    When the last command was not a yank, choose a kill from the ring instead."""
    b = buf()
    if ed.last_command != 'yank' or getattr(ed, 'yanked', (None,))[0] is not b:
        if not ed.kill_ring:
            raise EditorError('Kill ring is empty')
        from ..minibuffer import completing_read
        shown = {}
        for entry in ed.kill_ring:
            label = entry.replace('\n', '⏎')
            shown.setdefault(label if len(label) <= 200 else label[:200] + '…', entry)
        choice = completing_read('Yank from kill ring: ', list(shown), require_match=True,
                                 category='kill-ring')
        text = shown[choice]
        start = b.pt
        b.insert(text)
        b.push_mark(start)
        ed.yanked = (b, start, b.pt)
        ed.this_command = 'yank'
        return
    _, start, end = ed.yanked
    start, end = sorted((b.mark.pos if b.mark is not None else start, b.pt))
    text = ed.current_kill(n)
    b.delete_region(start, end)
    b.goto_char(start)
    b.insert(text)
    b.set_mark(start)
    ed.yanked = (b, start, b.pt)
    ed.this_command = 'yank'


@command('*')
def terminal_paste():
    """Insert text pasted into the terminal, and put it on the kill ring."""
    text = ed.last_event.text or ''
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if not text:
        return
    b = buf()
    _delete_active_region(b)
    start = b.pt
    b.insert(text)
    b.push_mark(start)
    if not ed.kill_ring or ed.kill_ring[0] != text:
        ed.kill_ring.insert(0, text)
        ed.kill_index = 0
    ed.yanked = (b, start, b.pt)
    ed.this_command = 'yank'


# -- transposing and case ---------------------------------------------------------------------------

@command('*')
def transpose_chars():
    """Interchange the characters around point, moving forward one character."""
    b = buf()
    if b.eolp() and not b.bolp():
        b.goto_char(b.pt - 1)
    if b.pt <= b.point_min or b.pt >= b.point_max:
        raise EditorError("Don't have two things to transpose")
    here = b.pt
    pair = b.substring(here - 1, here + 1)
    b.replace_region(here - 1, here + 1, pair[::-1])
    b.goto_char(here + 1)


@command('*')
def transpose_words():
    """Interchange the words around point, leaving point after both."""
    b = buf()
    origin = b.pt
    second = b.re_search_forward(_WORD, noerror=True)
    if second is None:
        b.goto_char(origin)
        raise EditorError("Don't have two things to transpose")
    b.goto_char(second.start())
    first = b.re_search_backward(_WORD, noerror=True)
    if first is None:
        b.goto_char(origin)
        raise EditorError("Don't have two things to transpose")
    earlier, later = first.group(), second.group()
    b.replace_region(second.start(), second.end(), earlier)
    b.replace_region(first.start(), first.end(), later)
    b.goto_char(second.start() + len(later))


@command('*')
def transpose_lines():
    """Exchange the current line with the one before it, moving past both."""
    b = buf()
    text = b.text
    line = b.line_at()
    if line == 0:
        line = 1
    if line >= text.line_count:
        raise EditorError("Don't have two things to transpose")
    upper, lower = text.line(line - 1), text.line(line)
    b.replace_region(text.line_start(line - 1), text.line_end(line), lower + '\n' + upper)
    if line + 1 < b.text.line_count:
        b.goto_char(b.text.line_start(line + 1))
    else:
        b.goto_char(b.point_max)
        b.insert('\n')


def _case_word(n, transform):
    b = buf()
    start = b.pt
    _forward_word(b, n)
    end = b.pt
    lo, hi = sorted((start, end))
    b.replace_region(lo, hi, transform(b.substring(lo, hi)))
    b.goto_char(max(start, end) if n > 0 else start)


def _capitalize(text):
    return re.sub(r'[^\W_]+', lambda m: m.group()[0].upper() + m.group()[1:].lower(), text)


@command('*p')
def upcase_word(n=1):
    """Convert the following word to upper case."""
    _case_word(n, str.upper)


@command('*p')
def downcase_word(n=1):
    """Convert the following word to lower case."""
    _case_word(n, str.lower)


@command('*p')
def capitalize_word(n=1):
    """Capitalize the following word."""
    _case_word(n, _capitalize)


@command('*r')
def upcase_region(start, end):
    """Convert the region to upper case."""
    b = buf()
    with b.save_excursion():
        b.replace_region(start, end, b.substring(start, end).upper())


@command('*r')
def downcase_region(start, end):
    """Convert the region to lower case."""
    b = buf()
    with b.save_excursion():
        b.replace_region(start, end, b.substring(start, end).lower())


@command('*r')
def capitalize_region(start, end):
    """Capitalize each word of the region."""
    b = buf()
    with b.save_excursion():
        b.replace_region(start, end, _capitalize(b.substring(start, end)))


# -- the mark ---------------------------------------------------------------------------------------

@command('P')
def set_mark_command(arg=None):
    """Set the mark where point is and activate it.

    Typed twice, set the mark without activating it.  With an argument, jump
    to the mark and bring back the mark before it."""
    b = buf()
    if arg is not None:
        if b.mark is None:
            raise EditorError('No mark set in this buffer')
        b.goto_char(b.mark.pos)
        b.pop_mark()
        ed.this_command = 'pop-to-mark-command'
        return
    if ed.last_command == 'set-mark-command' and b.mark_active and b.mark is not None \
            and b.mark.pos == b.pt:
        b.mark_active = False
        ed.message('Mark deactivated', log=False)
        return
    b.push_mark(activate=True)
    b.locals['_shift_region'] = False
    ed.keep_mark_active = True
    ed.message('Mark set', log=False)


@command('P')
def exchange_point_and_mark(arg=None):
    """Put the mark where point is and point where the mark was."""
    b = buf()
    if b.mark is None:
        raise EditorError('No mark set in this buffer')
    here, there = b.pt, b.mark.pos
    b.mark.set(here)
    b.goto_char(there)
    if arg is None:
        b.mark_active = True
        ed.keep_mark_active = True


@command
def mark_whole_buffer():
    """Put point at the beginning of the buffer and the mark at its end."""
    b = buf()
    b.push_mark(b.pt)
    b.push_mark(b.point_max, activate=True)
    b.goto_char(b.point_min)
    ed.keep_mark_active = True


@command('p')
def mark_word(n=1):
    """Set the mark N words from point."""
    b = buf()
    start = b.mark.pos if b.region_active() and ed.last_command == 'mark-word' else b.pt
    here = b.pt
    b.goto_char(start)
    _forward_word(b, n)
    target = b.pt
    b.goto_char(here)
    if b.region_active() and ed.last_command == 'mark-word':
        b.mark.set(target)
    else:
        b.push_mark(target, activate=True)
    ed.keep_mark_active = True


@command
def mark_paragraph():
    """Put point at the beginning of this paragraph and the mark at its end."""
    b = buf()
    forward_paragraph(1)
    b.push_mark(activate=True)
    backward_paragraph(1)
    ed.keep_mark_active = True


@command
def keyboard_quit():
    """Abandon whatever is going on."""
    raise Quit()


@command
def exit_recursive_edit():
    """Leave the innermost recursive edit, returning to what started it."""
    from ..errors import ExitRecursiveEdit
    if ed.recursion_depth == 0:
        raise EditorError('No recursive edit is in progress')
    raise ExitRecursiveEdit()


@command
def abort_recursive_edit():
    """Leave the innermost recursive edit or minibuffer, cancelling what started it."""
    from ..errors import AbortRecursiveEdit
    if ed.recursion_depth == 0:
        raise EditorError('No recursive edit is in progress')
    raise AbortRecursiveEdit()


@command
def keyboard_escape_quit():
    """Get out of whatever state the editor is in: a region, a prompt, extra windows."""
    b = buf()
    if b.region_active():
        b.mark_active = False
    elif ed.minibuffer_depth > 0:
        from ..errors import AbortRecursiveEdit
        raise AbortRecursiveEdit()
    elif len(ed.frame.windows()) > 1:
        ed.frame.delete_others(ed.selected_window)
    else:
        raise Quit()


# -- undo --------------------------------------------------------------------------------------------

_UNDO_COMMANDS = ('undo', 'undo-only', 'undo-redo')


@command('*p')
def undo(n=1):
    """Take back the last change; repeat to go further back.

    Any other command ends the sequence, after which undoing undoes the undo."""
    b = buf()
    continuing = ed.last_command in _UNDO_COMMANDS
    redoing = False
    for i in range(max(1, n)):
        fresh = not (continuing or i)
        if fresh and b._groups and b._open is None and b._groups[-1].kind == 'undo':
            redoing = True
        b.undo(continuing=not fresh)
    ed.message('Redo' if redoing else 'Undo', log=False)


@command('*p')
def undo_only(n=1):
    """Take back the last change, never redoing what was undone before."""
    b = buf()
    continuing = ed.last_command in _UNDO_COMMANDS
    for i in range(max(1, n)):
        b.undo(continuing=continuing or i > 0, only=True)
    ed.message('Undo', log=False)


@command('*p')
def undo_redo(n=1):
    """Undo the last undo."""
    b = buf()
    continuing = ed.last_command == 'undo-redo'
    for i in range(max(1, n)):
        b.redo(continuing=continuing or i > 0)
    ed.message('Redo', log=False)


# -- prefix arguments ------------------------------------------------------------------------------------

def _more_prefix():
    ed.set_transient_map(universal_argument_map)


@command
def universal_argument():
    """Begin a numeric argument for the next command.

    Digits and a minus sign that follow make up the argument.  ``C-u`` alone
    means four; each further ``C-u`` multiplies by four."""
    ed.prefix_arg = [4]
    _more_prefix()


@command
def universal_argument_more():
    """Multiply the argument by four, or end a sequence of digits."""
    arg = ed.current_prefix_arg
    if isinstance(arg, list):
        ed.prefix_arg = [arg[0] * 4]
        _more_prefix()
    elif arg == '-':
        ed.prefix_arg = [-4]
        _more_prefix()
    else:
        ed.prefix_arg = arg


@command
def digit_argument():
    """Add the digit typed to the numeric argument for the next command."""
    digit = int(split_key(ed.last_event.key)[1])
    arg = ed.current_prefix_arg
    if isinstance(arg, int):
        ed.prefix_arg = arg * 10 + (digit if arg >= 0 else -digit)
    elif arg == '-':
        ed.prefix_arg = -digit if digit else '-'
    else:
        ed.prefix_arg = digit
    _more_prefix()


@command
def negative_argument():
    """Begin a negative numeric argument for the next command."""
    arg = ed.current_prefix_arg
    if isinstance(arg, int):
        ed.prefix_arg = -arg
    elif arg == '-':
        ed.prefix_arg = None
    else:
        ed.prefix_arg = '-'
    _more_prefix()


# -- odds and ends -----------------------------------------------------------------------------------------

@command('P')
def what_cursor_position(arg=None):
    """Describe the character after point and where point is."""
    import unicodedata
    b = buf()
    total = b.point_max - b.point_min
    column = b.current_column()
    where = f'point={b.pt + 1} of {total} ({100 * (b.pt - b.point_min) // max(1, total)}%) ' \
            f'column={column}'
    char = b.char_after()
    if not char:
        ed.message(where + '  (end of buffer)', log=False)
        return
    o = ord(char)
    shown = {'\n': 'C-j', '\t': 'TAB', ' ': 'SPC'}.get(char, char)
    name = unicodedata.name(char, '')
    ed.message(f'Char: {shown} ({o}, #o{o:o}, #x{o:x}{", " + name if name and arg else ""}) '
               + where, log=False)


@command
def count_words_region():
    """Count the lines, words and characters in the region, or in the buffer."""
    b = buf()
    start, end = b.region() if b.region_active() else (b.point_min, b.point_max)
    text = b.substring(start, end)
    what = 'Region' if b.region_active() else 'Buffer'
    lines = b.count_lines(start, end)
    words = len(re.findall(r'\S+', text))
    ed.message(f'{what} has {lines} line{"s" * (lines != 1)}, {words} word{"s" * (words != 1)}, '
               f'and {len(text)} character{"s" * (len(text) != 1)}', log=False)
    ed.keep_mark_active = True


@command
def redraw_display():
    """Draw the whole screen again."""
    ed.display.invalidate()


@command
def suspend_frame():
    """Stop the editor and return to the shell that started it; ``fg`` brings it back."""
    ed.tty.suspend_process()
    ed.display.invalidate()


@command
def ignore():
    """Do nothing."""


@command
def undefined():
    """Beep: this key deliberately does nothing."""
    ed.ding()


@command('r')
def narrow_to_region(start, end):
    """Hide everything outside the region until ``widen``."""
    b = buf()
    b.narrow(start, end)
    b.mark_active = False


@command
def widen():
    """Make the whole buffer visible again after narrowing."""
    buf().widen()


@command('*r')
def sort_lines(start, end):
    """Sort the lines of the region; in reverse with a prefix argument."""
    b = buf()
    first, last = b.line_at(start), b.line_at(max(start, end - 1))
    lines = [b.text.line(i) for i in range(first, last + 1)]
    lines.sort(reverse=ed.current_prefix_arg is not None)
    b.replace_region(b.text.line_start(first), b.text.line_end(last), '\n'.join(lines))


@command('*r')
def reverse_region(start, end):
    """Reverse the order of the lines in the region."""
    b = buf()
    first, last = b.line_at(start), b.line_at(max(start, end - 1))
    lines = [b.text.line(i) for i in range(first, last + 1)]
    b.replace_region(b.text.line_start(first), b.text.line_end(last), '\n'.join(lines[::-1]))


@command('*r')
def delete_duplicate_lines(start, end):
    """Delete all but the first of each set of identical lines in the region."""
    b = buf()
    first, last = b.line_at(start), b.line_at(max(start, end - 1))
    seen = set()
    kept = []
    for i in range(first, last + 1):
        line = b.text.line(i)
        if line not in seen:
            seen.add(line)
            kept.append(line)
    removed = last - first + 1 - len(kept)
    b.replace_region(b.text.line_start(first), b.text.line_end(last), '\n'.join(kept))
    ed.message(f'Deleted {removed} duplicate line{"s" * (removed != 1)}', log=False)


@command
def toggle_truncate_lines():
    """Switch between wrapping long lines and letting them run off the window."""
    b = buf()
    b.set('truncate_lines', not b.get('truncate_lines'))
    ed.selected_window.hscroll = 0
    ed.message('Truncate long lines ' + ('enabled' if b.get('truncate_lines') else 'disabled'),
               log=False)


@command
def toggle_word_wrap():
    """Switch between breaking wrapped lines anywhere and at spaces."""
    b = buf()
    b.set('word_wrap', not b.get('word_wrap'))
    ed.message('Word wrap ' + ('enabled' if b.get('word_wrap') else 'disabled'), log=False)


@command('p')
def repeat(n=1):
    """Run the last command again; then ``z`` alone repeats it once more."""
    name = getattr(ed, 'last_repeatable', None)
    if name is None:
        raise EditorError('There is nothing to repeat')
    arg = getattr(ed, 'last_repeatable_arg', None)
    for _ in range(max(1, n)):
        ed.current_prefix_arg = arg
        ed.call_interactively(name)
    ed.this_command = name
    again = Keymap()
    again.bind('z', 'repeat')
    ed.set_transient_map(again)
    ed.repeating = True


def _remember_repeatable():
    if ed.this_command not in (None, 'repeat', 'universal-argument', 'universal-argument-more',
                               'digit-argument', 'negative-argument'):
        if not getattr(ed, 'repeating', False):
            ed.last_repeatable = ed.this_command
            ed.last_repeatable_arg = ed.current_prefix_arg
    ed.repeating = False


from ..variables import add_hook  # noqa: E402

add_hook('post_command_hook', _remember_repeatable)


# -- keys --------------------------------------------------------------------------------------------------

for _keys, _name in (
        ('C-f', 'forward-char'), ('<right>', 'forward-char'),
        ('C-b', 'backward-char'), ('<left>', 'backward-char'),
        ('C-n', 'next-line'), ('<down>', 'next-line'),
        ('C-p', 'previous-line'), ('<up>', 'previous-line'),
        ('C-a', 'move-beginning-of-line'), ('<home>', 'move-beginning-of-line'),
        ('C-e', 'move-end-of-line'), ('<end>', 'move-end-of-line'),
        ('M-m', 'back-to-indentation'),
        ('M-f', 'forward-word'), ('C-<right>', 'forward-word'), ('M-<right>', 'forward-word'),
        ('M-b', 'backward-word'), ('C-<left>', 'backward-word'), ('M-<left>', 'backward-word'),
        ('M-<', 'beginning-of-buffer'), ('C-<home>', 'beginning-of-buffer'),
        ('M->', 'end-of-buffer'), ('C-<end>', 'end-of-buffer'),
        ('M-}', 'forward-paragraph'), ('C-<down>', 'forward-paragraph'),
        ('M-{', 'backward-paragraph'), ('C-<up>', 'backward-paragraph'),
        ('M-e', 'forward-sentence'), ('M-a', 'backward-sentence'),
        ('C-v', 'scroll-up-command'), ('<next>', 'scroll-up-command'),
        ('M-v', 'scroll-down-command'), ('<prior>', 'scroll-down-command'),
        ('C-M-v', 'scroll-other-window'), ('C-M-S-v', 'scroll-other-window-down'),
        ('M-<next>', 'scroll-other-window'), ('M-<prior>', 'scroll-other-window-down'),
        ('C-l', 'recenter-top-bottom'), ('M-r', 'move-to-window-line-top-bottom'),
        ('M-g g', 'goto-line'), ('M-g M-g', 'goto-line'), ('M-g c', 'goto-char'),
        ('RET', 'newline'), ('C-j', 'electric-newline-and-maybe-indent'),
        ('C-o', 'open-line'), ('TAB', 'indent-for-tab-command'),
        ('C-M-\\', 'indent-region'), ('C-x TAB', 'indent-rigidly'),
        ('C-d', 'delete-char'), ('<delete>', 'delete-forward-char'),
        ('DEL', 'delete-backward-char'),
        ('M-\\', 'delete-horizontal-space'), ('M-SPC', 'just-one-space'),
        ('M-^', 'delete-indentation'), ('C-x C-o', 'delete-blank-lines'),
        ('C-q', 'quoted-insert'),
        ('C-k', 'kill-line'), ('C-S-DEL', 'kill-whole-line'),
        ('M-d', 'kill-word'), ('C-<delete>', 'kill-word'),
        ('M-DEL', 'backward-kill-word'), ('C-DEL', 'backward-kill-word'),
        ('M-k', 'kill-sentence'), ('M-z', 'zap-to-char'),
        ('C-w', 'kill-region'), ('M-w', 'kill-ring-save'),
        ('C-y', 'yank'), ('M-y', 'yank-pop'), ('<paste>', 'terminal-paste'),
        ('C-t', 'transpose-chars'), ('M-t', 'transpose-words'), ('C-x C-t', 'transpose-lines'),
        ('M-u', 'upcase-word'), ('M-l', 'downcase-word'), ('M-c', 'capitalize-word'),
        ('C-x C-u', 'upcase-region'), ('C-x C-l', 'downcase-region'),
        ('C-SPC', 'set-mark-command'), ('C-x C-x', 'exchange-point-and-mark'),
        ('C-x h', 'mark-whole-buffer'), ('M-@', 'mark-word'), ('M-h', 'mark-paragraph'),
        ('C-g', 'keyboard-quit'), ('M-ESC ESC', 'keyboard-escape-quit'),
        ('C-M-c', 'exit-recursive-edit'), ('C-]', 'abort-recursive-edit'),
        ('C-/', 'undo'), ('C-_', 'undo'), ('C-x u', 'undo'),
        ('C-?', 'undo-redo'), ('C-M-_', 'undo-redo'),
        ('C-u', 'universal-argument'), ('M--', 'negative-argument'), ('C--', 'negative-argument'),
        ('C-M--', 'negative-argument'),
        ('C-x =', 'what-cursor-position'), ('M-=', 'count-words-region'),
        ('C-z', 'suspend-frame'), ('C-x C-z', 'suspend-frame'),
        ('C-x n n', 'narrow-to-region'), ('C-x n w', 'widen'),
        ('C-x z', 'repeat')):
    global_map.bind(_keys, _name)

for _digit in '0123456789':
    global_map.bind(f'M-{_digit}', 'digit-argument')
    global_map.bind(f'C-{_digit}', 'digit-argument')
    global_map.bind(f'C-M-{_digit}', 'digit-argument')
    universal_argument_map.bind(_digit, 'digit-argument')
universal_argument_map.bind('C-u', 'universal-argument-more')
universal_argument_map.bind('-', 'negative-argument')
