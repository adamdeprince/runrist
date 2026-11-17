# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Registers and rectangles: the commands under ``C-x r``.

A register is a place with a one-character name that holds text or a
position.  A rectangle is the block of characters between the columns of
point and mark, on every line between them.
"""

from ..buffer import Marker
from ..command import command
from ..editor import ed, global_map
from ..errors import EditorError
from ..minibuffer import read_char, read_string

registers = {}
_killed_rectangle = []


def buf():
    return ed.current_buffer


def _register(prompt):
    return read_char(prompt)


# -- registers ---------------------------------------------------------------------------

@command(interactive=lambda: [_register('Copy to register: '), *buf().region(),
                              ed.current_prefix_arg])
def copy_to_register(register, start, end, delete=None):
    """Copy the region into a register.  With a prefix argument, delete it as well."""
    b = buf()
    registers[register] = b.substring(start, end)
    if delete is not None:
        b.delete_region(start, end)
    b.mark_active = False
    ed.message(f'Copied to register {register}', log=False)


@command(interactive=lambda: [_register('Insert register: '), ed.current_prefix_arg])
def insert_register(register, arg=None):
    """Insert the text held in a register."""
    value = registers.get(register)
    if value is None:
        raise EditorError(f'Register {register} is empty')
    if not isinstance(value, str):
        raise EditorError(f'Register {register} does not hold text')
    b = buf()
    start = b.pt
    b.insert(value)
    if arg is None:
        b.push_mark(b.pt)
        b.goto_char(start)
    else:
        b.push_mark(start)


@command(interactive=lambda: [_register('Point to register: ')])
def point_to_register(register):
    """Remember where point is in a register."""
    b = buf()
    registers[register] = Marker(b, b.pt)
    ed.message(f'Position saved in register {register}', log=False)


@command(interactive=lambda: [_register('Jump to register: ')])
def jump_to_register(register):
    """Go to the position remembered in a register."""
    value = registers.get(register)
    if not isinstance(value, Marker):
        raise EditorError(f'Register {register} does not hold a position')
    if not value.buffer.live:
        raise EditorError('That buffer no longer exists')
    ed.switch_to_buffer(value.buffer)
    value.buffer.goto_char(value.pos)


# -- rectangles --------------------------------------------------------------------------

def _corners():
    """``(first line, last line, left column, right column)`` of the rectangle."""
    b = buf()
    start, end = b.region()
    c1, c2 = b.current_column(start), b.current_column(end)
    return b.line_at(start), b.line_at(end), min(c1, c2), max(c1, c2)


def _span(b, line, left, right):
    """Positions in ``line`` of columns ``left`` and ``right``."""
    start = b.text.line_start(line)
    return start + b.column_to_index(line, left), start + b.column_to_index(line, right)


def _extract(delete):
    b = buf()
    first, last, left, right = _corners()
    rows = []
    for line in range(first, last + 1):
        s, e = _span(b, line, left, right)
        rows.append(b.substring(s, e).ljust(right - left))
        if delete:
            b.delete_region(s, e)
    if delete:
        b.goto_char(_span(b, first, left, left)[0])
    b.mark_active = False
    return rows


@command('*')
def kill_rectangle():
    """Delete the rectangle and save it for ``yank-rectangle``."""
    _killed_rectangle[:] = _extract(True)


@command
def copy_rectangle_as_kill():
    """Save the rectangle for ``yank-rectangle`` without deleting it."""
    _killed_rectangle[:] = _extract(False)


@command('*')
def delete_rectangle():
    """Delete the rectangle."""
    _extract(True)


@command('*')
def yank_rectangle():
    """Insert the last killed rectangle with its corner at point."""
    if not _killed_rectangle:
        raise EditorError('No rectangle has been killed')
    b = buf()
    column = b.current_column()
    line = b.line_at()
    for i, row in enumerate(_killed_rectangle):
        if line + i >= b.text.line_count:
            b.insert_at(b.size, '\n')
        b.goto_char(b.text.line_start(line + i))
        b.move_to_column(column, force=True)
        b.insert(row)


@command('*')
def open_rectangle():
    """Fill the rectangle with spaces, pushing its text to the right."""
    b = buf()
    first, last, left, right = _corners()
    for line in range(first, last + 1):
        b.goto_char(b.text.line_start(line))
        if b.move_to_column(left) == left:
            b.insert(' ' * (right - left))
    b.goto_char(_span(b, first, left, left)[0])
    b.mark_active = False


@command('*')
def clear_rectangle():
    """Replace the rectangle's text with spaces."""
    b = buf()
    first, last, left, right = _corners()
    for line in range(first, last + 1):
        s, e = _span(b, line, left, right)
        b.replace_region(s, e, ' ' * (right - left))
    b.mark_active = False


@command(interactive=lambda: [read_string('String rectangle: ')])
def string_rectangle(text):
    """Replace the rectangle's text on each line with a string."""
    b = buf()
    first, last, left, right = _corners()
    for line in range(first, last + 1):
        b.goto_char(b.text.line_start(line))
        b.move_to_column(left, force=True)
        s, e = _span(b, line, left, right)
        b.replace_region(s, e, text)
    b.goto_char(_span(b, last, left, left)[0] + len(text))
    b.mark_active = False


@command('*P')
def rectangle_number_lines(arg=None):
    """Insert line numbers down the left edge of the rectangle."""
    b = buf()
    first, last, left, _ = _corners()
    start = ed.prefix_numeric_value(arg) if arg is not None else 1
    width = len(str(start + last - first))
    for i, line in enumerate(range(first, last + 1)):
        b.goto_char(b.text.line_start(line))
        b.move_to_column(left, force=True)
        b.insert(f'{start + i:>{width}} ')
    b.mark_active = False


for _keys, _name in (
        ('C-x r s', 'copy-to-register'), ('C-x r x', 'copy-to-register'),
        ('C-x r i', 'insert-register'), ('C-x r g', 'insert-register'),
        ('C-x r SPC', 'point-to-register'), ('C-x r j', 'jump-to-register'),
        ('C-x r k', 'kill-rectangle'), ('C-x r M-w', 'copy-rectangle-as-kill'),
        ('C-x r d', 'delete-rectangle'), ('C-x r y', 'yank-rectangle'),
        ('C-x r o', 'open-rectangle'), ('C-x r c', 'clear-rectangle'),
        ('C-x r t', 'string-rectangle'), ('C-x r N', 'rectangle-number-lines')):
    global_map.bind(_keys, _name)
