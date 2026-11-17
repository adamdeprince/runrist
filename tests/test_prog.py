# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Commands for code, on the modes that come from Emacs and the ones written here."""
import textwrap

from runrist import fontlock
from runrist.testing import run, screen, start


def text(ed):
    return ed.current_buffer.string()


def faces_of(ed, line):
    b = ed.current_buffer
    fontlock.fontify(b, 0, b.line_count - 1)
    content = b.text.line(line)
    return {content[s:e]: f for s, e, f in b.text.meta(line).spans or ()}


def test_python_highlighting_follows_emacs():
    ed = start(textwrap.dedent('''\
        """Module doc."""
        import os

        class Thing(Base):
            def method(self, x=None):
                """Do it."""
                y = f"a {x} b\\n"   # why
                return len(y) + 0x1F
        '''), mode='python-mode')
    assert faces_of(ed, 0) == {'"""Module doc."""': 'font-lock-doc-face'}
    assert faces_of(ed, 1)['import'] == 'font-lock-keyword-face'
    line = faces_of(ed, 3)
    assert line['class'] == 'font-lock-keyword-face' and line['Thing'] == 'font-lock-type-face'
    line = faces_of(ed, 4)
    assert line['method'] == 'font-lock-function-name-face' and line['None'] == \
        'font-lock-constant-face'
    assert faces_of(ed, 5) == {'"""Do it."""': 'font-lock-doc-face'}
    line = faces_of(ed, 6)
    assert line['y'] == 'font-lock-variable-name-face' and line['# why'] == \
        'font-lock-comment-face'
    assert line['\\n'] == 'font-lock-escape-face' and line['x'] == 'default'
    line = faces_of(ed, 7)
    assert line['len'] == 'font-lock-builtin-face' and line['0x1F'] == 'font-lock-number-face'


def test_a_string_left_open_recolors_what_follows_and_closing_it_restores():
    ed = start('a = 1\nb = 2\nc = 3\n', mode='python-mode')
    assert 'b' in faces_of(ed, 1)
    run(ed, 'C-e SPC " " "')                    # open a triple-quoted string on line 1
    assert list(faces_of(ed, 1).values()) == ['font-lock-string-face']
    assert list(faces_of(ed, 2).values()) == ['font-lock-string-face']
    run(ed, '" " "')
    assert faces_of(ed, 1)['b'] == 'font-lock-variable-name-face'


def test_python_indentation():
    ed = start('', mode='python-mode')
    ed.tty.type('def f(a,')
    run(ed, 'RET')
    ed.tty.type('b):')
    run(ed, 'RET')
    ed.tty.type('if a:')
    run(ed, 'RET')
    ed.tty.type('return b')
    run(ed, 'RET')
    ed.tty.type('else:')
    run(ed, 'RET')
    ed.tty.type('pass')
    run(ed, 'RET')
    ed.tty.type('x = [')
    run(ed, 'RET')
    ed.tty.type('1,')
    run(ed, 'RET')
    ed.tty.type(']')
    assert text(run(ed)) == textwrap.dedent('''\
        def f(a,
              b):
            if a:
                return b
            else:
                pass
            x = [
                1,
            ]''')


def test_c_highlighting_indentation_and_folding():
    ed = start('', mode='c-mode', height=16)
    ed.tty.type('int main(void) {')
    run(ed, 'RET')
    ed.tty.type('if (x) {')
    run(ed, 'RET')
    ed.tty.type('return 1; /* c */')
    run(ed, 'RET')
    ed.tty.type('}')
    run(ed, 'RET')
    ed.tty.type('}')
    run(ed)
    assert text(ed) == 'int main(void) {\n    if (x) {\n        return 1; /* c */\n    }\n}'
    line = faces_of(ed, 0)
    assert line['int'] == 'font-lock-type-face' and line['main'] == 'font-lock-function-name-face'
    assert faces_of(ed, 2)['/* c */'] == 'font-lock-comment-face'
    run(ed, 'M-< <backtab>')
    rows = [r.rstrip() for r in screen(ed)[:3]]
    assert rows[0].startswith('▸  1 int main(void) { ⋯ 3 lines') and rows[1].endswith('5 }')


def test_sexp_motion_skips_strings_and_comments():
    ed = start('f(a, "x)y", [1, 2]) # )\nnext', mode='python-mode')
    run(ed, 'C-f C-M-f')
    assert ed.current_buffer.pt == 19                   # past the whole argument list
    run(ed, 'C-M-b')
    assert ed.current_buffer.pt == 1
    run(ed, 'C-M-d C-M-f C-M-f')                        # into the list, over a and the string
    assert ed.current_buffer.pt == 10
    run(ed, 'C-M-u')
    assert ed.current_buffer.pt == 1
    run(ed, 'M-< C-M-k')
    assert text(ed).startswith('(a, "x)y"')


def test_defun_motion_and_marking():
    ed = start('import os\n\ndef a():\n    return 1\n\ndef b():\n    return 2\n', mode='python-mode')
    run(ed, 'M-> C-M-a')
    assert ed.current_buffer.line_at() == 5
    run(ed, 'C-M-a')
    assert ed.current_buffer.line_at() == 2
    run(ed, 'C-M-e')
    assert ed.current_buffer.line_at() == 4
    run(ed, 'C-M-h')
    b = ed.current_buffer
    assert b.substring(*b.region()).strip() == 'def b():\n    return 2'


def test_comments_fill_and_dabbrev():
    ed = start('alpha = 1\nbeta = alpha\n', mode='python-mode')
    run(ed, 'C-x h M-;')
    assert text(ed) == '# alpha = 1\n# beta = alpha\n'
    run(ed, 'C-x h M-;')
    assert text(ed) == 'alpha = 1\nbeta = alpha\n'
    run(ed, 'M-> a l M-/')
    assert text(ed).endswith('alpha')
    ed = start('one two three four five six seven eight nine ten eleven twelve\n')
    ed.current_buffer.set('fill_column', 30)
    run(ed, 'M-q')
    assert text(ed) == 'one two three four five six\nseven eight nine ten eleven\ntwelve\n'


def test_shell_command_and_region_filter():
    ed = start('b\na\nc\n')
    run(ed, 'M-! e c h o SPC h i RET')
    assert screen(ed)[-1].strip() == 'hi'
    run(ed, 'C-x h C-u M-| s o r t RET')
    assert text(ed) == 'a\nb\nc\n'


def test_modes_from_emacs_know_their_comments():
    ed = start('x=1 # note\n', name='run.sh', mode='sh-mode')
    assert faces_of(ed, 0)['# note'] == 'font-lock-comment-face'
    run(ed, 'M-> e c h o SPC h i C-a M-;')
    assert text(ed).endswith('# echo hi') or '# ' in text(ed).split('\n')[-1]
