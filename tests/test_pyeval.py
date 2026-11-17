# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
import os
import textwrap
import time

from runrist import live, pyeval
from runrist.command import commands
from runrist.testing import run, screen, start


def text(ed):
    return ed.current_buffer.string()


def test_eval_expression_and_last_sexp():
    ed = start('total = sum(range(5)) + 1\n', mode='fundamental-mode')
    run(ed, 'M-: 6 SPC * SPC 7 RET')
    assert screen(ed)[-1].strip() == '42'
    run(ed, 'C-e C-x C-e')                 # the longest expression ending at point
    assert screen(ed)[-1].strip() == '11'
    run(ed, 'C-u C-x C-e')
    assert text(ed) == 'total = sum(range(5)) + 111\n'


def test_eval_defun_defines_a_live_command():
    source = textwrap.dedent('''\
        from runrist.api import *

        @command
        def shout():
            "Insert a shout."
            insert('HEY')

        global_set_key('C-c s', shout)
        ''')
    ed = start(source)
    run(ed, 'M-x eval-buffer RET M-> C-c s')
    assert text(ed).endswith('HEY')
    first = commands['shout'].fn
    # Redefine it in place: the key runs the new code without being bound again.
    b = ed.current_buffer
    b.goto_char(0)
    b.search_forward("insert('HEY')")
    b.replace_region(b.pt - 6, b.pt - 1, "'ho!'")
    report = pyeval.evaluate(b.substring(b.text.line_start(2), b.text.line_end(5)), b, first_line=3)
    assert report.ok and commands['shout'].fn is first
    run(ed, 'M-> C-c s')
    assert text(ed).endswith('HEYho!')


def test_errors_are_reported_not_raised():
    ed = start('x = 1 / 0\n')
    run(ed, 'M-x eval-buffer RET')
    assert 'ZeroDivisionError' in screen(ed)[-1]
    run(ed, 'M-: 1 + RET')
    assert 'SyntaxError' in screen(ed)[-1]
    messages = ed.get_buffer('*Messages*').string()
    assert 'ZeroDivisionError: division by zero' in messages


def test_saving_a_loaded_module_patches_it(tmp_path):
    path = tmp_path / 'plug.py'
    path.write_text('from runrist.api import *\n\n@command\ndef plug_hello():\n    insert("v1")\n')
    module, report = live.load_file(str(path), 'plug_test_module')
    assert report.ok
    pyeval.start_watching()
    ed = start('')
    from runrist import files
    buffer = files.find_file_noselect(str(path))
    ed.switch_to_buffer(buffer)
    fn = commands['plug-hello'].fn
    buffer.goto_char(0)
    buffer.search_forward('v1')
    buffer.replace_region(buffer.pt - 2, buffer.pt, 'v2')
    run(ed, 'C-x C-s')
    assert 'Live:' in ed.echo and commands['plug-hello'].fn is fn
    scratch = ed.new_buffer('out')
    ed.switch_to_buffer(scratch)
    run(ed, 'M-x plug-hello RET')
    assert scratch.string() == 'v2'
    # A change made outside the editor is picked up by the watcher.
    path.write_text(path.read_text().replace('v2', 'v3'))
    stamp = time.time_ns() + 5_000_000_000
    os.utime(path, ns=(stamp, stamp))
    pyeval.watch()
    run(ed, 'M-x plug-hello RET')
    assert scratch.string() == 'v2v3'
