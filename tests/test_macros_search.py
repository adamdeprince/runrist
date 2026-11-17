# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
import pytest

from runrist import kmacro
from runrist.testing import run, screen, start


def text(ed):
    return ed.current_buffer.string()


def test_record_and_replay():
    ed = start('a\nb\nc\nd\n')
    run(ed, '<f3> C-e ! C-n C-a <f4>')
    assert text(ed) == 'a!\nb\nc\nd\n' and ed.defining_macro is None
    assert kmacro.ring[0].describe() == 'C-e ! C-n C-a'
    run(ed, '<f4> <f4>')
    assert text(ed) == 'a!\nb!\nc!\nd\n'
    run(ed, 'C-x e e')                   # e repeats
    assert text(ed) == 'a!\nb!\nc!\nd!\n!'


def test_repeat_count_and_until_error():
    ed = start('1\n2\n3\n4\n5\n6\n')
    run(ed, 'C-x ( C-e x C-n C-a C-x )')
    run(ed, 'C-u 2 C-x e')
    assert text(ed) == '1x\n2x\n3x\n4\n5\n6\n'
    run(ed, 'C-u 0 C-x e')              # until the macro fails at the end of the buffer
    assert text(ed).startswith('1x\n2x\n3x\n4x\n5x\n6x\n')
    assert ed.macro_stack == [] and not ed.macro_abort


def test_counter_and_search_inside_a_macro():
    ed = start('item\nitem\nitem\n')
    run(ed, '<f3> C-s item RET <f3> <f4> <f4> <f4>')
    assert text(ed) == 'item0\nitem1\nitem2\n'
    # A fourth run finds nothing: the search fails and the macro stops there.
    run(ed, '<f4>')
    assert text(ed) == 'item0\nitem1\nitem2\n'


def test_macro_drives_the_minibuffer():
    ed = start('foo bar foo\n')
    run(ed, '<f3> M-x replace-string RET foo RET baz RET <f4>')
    assert text(ed) == 'baz bar baz\n'
    ed.current_buffer.insert_at(ed.current_buffer.size, 'foo again')
    run(ed, '<f4>')
    assert text(ed).endswith('baz again')


def test_name_bind_and_save_as_python():
    ed = start('x\n')
    run(ed, '<f3> C-e y <f4> C-x C-k n my-mac RET')
    run(ed, 'M-x my-mac RET')
    assert text(ed) == 'xyy\n'
    run(ed, 'M-> M-x insert-kbd-macro RET my-mac RET')
    assert "defmacro('my-mac', 'C-e y')" in text(ed)
    kmacro.defmacro('other-mac', 'C-a Z')
    run(ed, 'M-< M-x other-mac RET')
    assert text(ed).startswith('Zxyy')


def test_apply_to_region_lines():
    ed = start('a\nb\nc\nd\n')
    run(ed, '<f3> - SPC <f4> C-/')      # define "- " at point, then undo its first effect
    assert text(ed) == 'a\nb\nc\nd\n'
    run(ed, 'M-< C-SPC C-n C-n C-n C-x C-k r')
    assert text(ed) == '- a\n- b\n- c\nd\n'


def test_quit_abandons_a_definition():
    ed = start('')
    run(ed, '<f3> a b C-g c')
    assert ed.defining_macro is None and text(ed) == 'abc'


def test_isearch():
    ed = start('alpha beta gamma beta delta\n')
    b = ed.current_buffer
    run(ed, 'C-s beta RET')
    assert b.pt == 10
    run(ed, 'M-< C-s beta C-s RET')
    assert b.pt == 21
    run(ed, 'M-< C-s beta C-s C-s C-s RET')      # fails after the second match, then wraps
    assert b.pt == 10
    run(ed, 'M-< C-s beta C-s C-s C-s DEL DEL RET')  # back through the steps
    assert b.pt == 21
    run(ed, 'M-< C-f C-s beta C-s C-g')          # quit returns to where the search began
    assert b.pt == 1
    run(ed, 'M-< C-s C-s RET')                   # an empty search reuses the last string
    assert b.pt == 10


def test_isearch_exits_on_other_keys_and_runs_them():
    ed = start('one two three\n')
    run(ed, 'C-s two C-a x')
    assert text(ed) == 'xone two three\n'
    assert ed.current_buffer.mark_pos() == 0      # the mark is where the search started
    run(ed, 'C-r o n C-r')               # backward, and case folding
    assert ed.current_buffer.pt == 1


def test_query_replace():
    ed = start('cat Cat cat cat\n')
    run(ed, 'M-% cat RET dog RET y n y q')
    assert text(ed) == 'dog Cat dog cat\n'
    run(ed, 'M-< M-% cat RET bird RET !')
    assert text(ed) == 'dog Bird dog bird\n'     # the replacement follows the capitalization


def test_replace_regexp_with_groups():
    ed = start('x=1, y=22\n')
    run(ed, r'M-x replace-regexp RET (\w)=(\d+) RET \2:\1 RET')
    assert text(ed) == '1:x, 22:y\n'
