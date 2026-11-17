# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
import random

import pytest

from runrist.buffer import Buffer, Marker
from runrist.errors import EditorError


def test_markers_follow_text():
    b = Buffer('t', 'hello\nworld\n')
    stays, advances = Marker(b, 5), Marker(b, 5, True)
    b.insert_at(5, '!!')
    assert (stays.pos, advances.pos) == (5, 7)
    b.delete_region(3, 9)
    assert (stays.pos, advances.pos) == (3, 3)
    assert b.string() == 'helorld\n'


def command(b, f):
    f()
    b.undo_boundary()


def test_undo_redo_chain():
    b = Buffer('u')
    b.set_modified(False)
    for word in ('one ', 'two ', 'three'):
        command(b, lambda: b.insert(word))
    assert b.string() == 'one two three' and b.modified
    b.undo()
    assert b.string() == 'one two '
    b.undo(continuing=True)
    assert b.string() == 'one '
    b.redo()
    assert b.string() == 'one two '
    b.redo(continuing=True)
    assert b.string() == 'one two three'
    with pytest.raises(EditorError):
        b.redo(continuing=True)
    # Undoing everything leaves the buffer unmodified again.
    b.undo(only=True)
    b.undo(True, only=True)
    b.undo(True, only=True)
    assert b.string() == '' and not b.modified
    with pytest.raises(EditorError):
        b.undo(True, only=True)
    # A fresh plain undo undoes the undoing.
    b.undo()
    assert b.string() == 'one '
    command(b, lambda: b.insert('X'))
    b.undo(only=True)
    assert b.string() == 'one '
    b.undo(True, only=True)
    assert b.string() == '' and not b.modified


def test_undo_coalesces_runs():
    b = Buffer('c')
    for ch in 'abc':
        b.insert(ch)
    assert b._groups[-1].entries == [('i', 0, 3)]
    b.undo_boundary()
    b.delete_region(2, 3)
    b.delete_region(1, 2)
    assert b._groups[-1].entries == [('d', 1, 'bc')]
    b.undo()
    assert b.string() == 'abc' and b.pt == 3


def test_undo_restores_random_edits():
    random.seed(3)
    for _ in range(40):
        original = ''.join(random.choice('ab\n') for _ in range(50))
        b = Buffer('r', original)
        for _ in range(30):
            if random.random() < .5 or not b.size:
                b.insert_at(random.randrange(b.size + 1), random.choice(['x', 'yy\n', 'zzz']))
            else:
                a = random.randrange(b.size)
                b.delete_region(a, min(b.size, a + random.randrange(1, 5)))
            if random.random() < .4:
                b.undo_boundary()
        final = b.string()
        b.undo()
        with pytest.raises(EditorError):
            while True:
                b.undo(True)
        assert b.string() == original and not b.modified
        b.redo()
        with pytest.raises(EditorError):
            while True:
                b.redo(True)
        assert b.string() == final


def test_lines_columns_search_narrowing():
    b = Buffer('l', 'ab\n\tx中y\nlast')
    assert b.forward_line(1) == 0 and b.pt == 3
    assert b.forward_line(5) == 3 and b.pt == b.size
    b.goto_char(4)
    assert b.current_column() == 8
    b.goto_char(6)
    assert b.current_column() == 11
    assert b.move_to_column(10) == 9 and b.pt == 5      # cannot land inside a wide character
    b.goto_char(0)
    assert b.re_search_forward(r'x(.)y').group(1) == '中' and b.pt == 7
    assert b.re_search_backward(r'[a-z]+').group() == 'y' and b.pt == 6
    b.goto_char(b.size)
    assert b.search_backward('AB', fold=True) == 0
    b.narrow(3, 7)
    assert b.string() == '\tx中y' and b.point_min == 3
    b.goto_char(0)
    assert b.pt == 3
    b.insert_at(7, '!')
    assert b.point_max == 8
    b.widen()
    assert b.string() == 'ab\n\tx中y!\nlast'
    assert b.count_lines(0, b.size) == 3
