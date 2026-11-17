# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Commands driven through the command loop on a fake terminal."""
import pytest

from runrist.testing import run, screen, start


def text(ed):
    return ed.current_buffer.string()


def test_motion_and_kill_ring():
    ed = start('hello world\nsecond line\n')
    run(ed, 'M-f C-SPC M-f M-w')
    assert ed.kill_ring[0] == ' world'
    run(ed, 'C-a C-k C-k C-y C-y')
    assert text(ed) == 'hello world\nhello world\nsecond line\n'
    run(ed, 'C-/')
    assert text(ed) == 'hello world\nsecond line\n'


def test_typing_and_undo_amalgamates():
    ed = start('')
    ed.tty.type('abc def')
    run(ed)
    assert text(ed) == 'abc def'
    run(ed, 'C-/')
    assert text(ed) == ''
    run(ed, 'C-?')
    assert text(ed) == 'abc def'


def test_prefix_arguments():
    ed = start('')
    run(ed, 'C-u x')
    assert text(ed) == 'xxxx'
    run(ed, 'C-u C-u y')
    assert text(ed) == 'xxxx' + 'y' * 16
    run(ed, 'C-u 1 2 z M-3 w')
    assert text(ed).endswith('z' * 12 + 'www')
    run(ed, 'C-a M-- M-2 C-d')         # nothing before point to delete backward
    assert text(ed).startswith('xxxx')
    run(ed, 'C-e C-u - 3 C-d')
    assert text(ed).endswith('z' * 12)


def test_esc_is_meta_and_unbound_keys_are_reported():
    ed = start('one two three')
    run(ed, 'ESC f ESC f')
    assert ed.current_buffer.pt == 7
    run(ed, 'C-x C-y')
    assert 'C-x C-y is undefined' in screen(ed)[-1]


def test_words_and_case():
    ed = start('hello big_world foo')
    run(ed, 'M-u M-c M-l')
    assert text(ed) == 'HELLO Big_world foo'
    run(ed, 'M-b M-b M-t')                  # at the start of a word: swap it with the one before
    assert text(ed) == 'Big HELLO_world foo'


def test_lines_join_transpose_and_blank():
    ed = start('a\n  b\nc\n\n\n\nd')
    run(ed, 'C-n M-^')
    assert text(ed) == 'a b\nc\n\n\n\nd'
    run(ed, 'C-n C-x C-t')
    assert text(ed) == 'c\na b\n\n\n\nd'
    run(ed, 'C-n C-x C-o')
    assert text(ed) == 'c\na b\n\nd'


def test_mark_region_and_shift_select():
    ed = start('abcdef')
    run(ed, 'S-<right> S-<right>')
    b = ed.current_buffer
    assert b.region_active() and b.region() == (0, 2)
    run(ed, '<right>')
    assert not b.region_active()
    run(ed, 'C-x h C-w')
    assert text(ed) == '' and ed.kill_ring[0] == 'abcdef'


def test_yank_pop_cycles():
    ed = start('one two three')
    run(ed, 'M-d M-f C-f M-d C-e C-y')       # two separate kills
    assert ed.kill_ring[:2] == ['three', 'one']
    assert text(ed) == ' two three'
    run(ed, 'M-y')
    assert text(ed) == ' two one'


def test_quit_cancels_prefix_and_region():
    ed = start('abc')
    run(ed, 'C-SPC C-f C-g')
    assert not ed.current_buffer.region_active()
    run(ed, 'C-x C-g x')
    assert text(ed).count('x') == 1


def test_read_only_buffer_refuses_edits():
    ed = start('abc')
    ed.current_buffer.read_only = True
    run(ed, 'x C-d')
    assert text(ed) == 'abc'
    assert 'read-only' in screen(ed)[-1]


def test_window_commands():
    ed = start('line\n' * 50, height=24)
    run(ed, 'C-x 2 C-x o M->')
    top, bottom = ed.frame.windows()
    assert ed.selected_window is bottom and ed.window_point(top) == 0
    assert ed.window_point(bottom) == ed.current_buffer.size
    run(ed, 'C-x 3')
    assert len(ed.frame.windows()) == 3
    run(ed, 'C-x 1')
    assert ed.frame.windows() == [bottom]


def test_scrolling_keeps_point_visible():
    ed = start(''.join(f'line {i}\n' for i in range(200)), height=12)
    run(ed, 'C-v C-v')
    w = ed.selected_window
    lines = [row[0] for row in w.rows]
    assert ed.current_buffer.line_at() in lines and lines[0] > 0
    run(ed, 'M-v M-v M-v')
    assert [row[0] for row in w.rows][0] == 0
    run(ed, 'M-> C-l')
    assert ed.current_buffer.line_at() in [row[0] for row in w.rows]


def test_long_lines_wrap_and_line_motion_follows_the_screen():
    ed = start('x' * 150 + '\nshort', width=60, height=10)
    rows = screen(ed)
    assert rows[0].rstrip().endswith('↩') and rows[1].rstrip().endswith('↩')
    run(ed, 'C-n')
    assert ed.current_buffer.pt == ed.selected_window.text_w - 1     # one screen row down
    run(ed, 'C-n C-n')
    assert ed.current_buffer.line_at() == 1
