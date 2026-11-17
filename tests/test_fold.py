# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
import textwrap

from runrist import fold
from runrist.testing import run, screen, start

SOURCE = textwrap.dedent('''\
    import os

    def first(a):
        x = a + 1
        if x:
            return x
        return 0

    class Thing:
        def method(self):
            return 1

        def other(self):
            return 2

    tail = 1
    ''')


def test_fold_one_block_and_show_the_badge():
    ed = start(SOURCE, mode='python-mode', height=20)
    run(ed, 'C-n C-n C-n C-c @ C-c')            # inside first(): fold it
    rows = screen(ed)
    assert rows[2].startswith('▸  3 def first(a): ⋯ 4 lines')
    assert rows[3].lstrip().startswith('8')      # the next line shown is line 8
    b = ed.current_buffer
    assert b.line_at() == 2                      # point was moved out of the hidden text
    run(ed, 'C-c @ C-c')
    assert not fold.folds(b) and screen(ed)[3].lstrip().startswith('4')


def test_fold_all_definitions_and_cycle():
    ed = start(SOURCE, mode='python-mode', height=20)
    run(ed, '<backtab>')
    rows = [r.rstrip() for r in screen(ed)[:8]]
    assert rows[2].startswith('▸  3 def first(a): ⋯ 4 lines')
    assert rows[4].startswith('▸  9 class Thing: ⋯ 5 lines')
    assert rows[6].lstrip().startswith('16 tail = 1')
    run(ed, '<backtab>')
    assert not fold.folds(ed.current_buffer)


def test_line_motion_steps_over_folds_and_search_opens_them():
    ed = start(SOURCE, mode='python-mode', height=20)
    run(ed, '<backtab> M-< C-n C-n C-n C-n')
    b = ed.current_buffer
    assert b.line_at() == 8                      # down from "def first" lands on "class Thing"
    run(ed, 'C-p C-p')
    assert b.line_at() == 2
    run(ed, 'M-< C-s return SPC 2 RET')
    assert b.line_at() == 13 and not fold.hidden(b).is_hidden(13)


def test_folds_follow_edits():
    ed = start(SOURCE, mode='python-mode', height=20)
    run(ed, 'M-< C-n C-n C-c @ C-h M-< RET RET')    # fold first(), then push it down two lines
    b = ed.current_buffer
    hidden = fold.hidden(b)
    assert hidden.fold_headed_by(4) is not None and hidden.is_hidden(5) and not hidden.is_hidden(9)
