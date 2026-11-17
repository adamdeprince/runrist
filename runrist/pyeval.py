# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Evaluating Python in the running editor.

``M-:`` evaluates an expression you type.  In a buffer of Python, ``C-x C-e``
evaluates the expression before point, ``C-M-x`` the top-level definition
around it, and ``eval-buffer`` everything that changed.  Code from a file
that is a loaded module is evaluated *in that module*, with definitions
patched in place (see ``runrist.live``), so editing a command in the editor's
own source and pressing ``C-M-x`` changes the command you are using.

Saving such a file loads what changed, and so does changing it from outside:
the editor watches the files of every loaded ``runrist`` module and of your
``init.py``.  Code from anywhere else is evaluated in ``runrist.user``, which
is also where ``M-:`` and ``*scratch*`` live.
"""

import ast
import os
import re

from . import live, user
from .command import command
from .editor import ed, global_map
from .errors import EditorError
from .minibuffer import read_file_name, read_from_minibuffer
from .variables import add_hook, defvar

defvar('live_watch', True,
       'Whether changes to the files of loaded modules are loaded as they happen.')
defvar('live_watch_interval', 1.0, 'Seconds between checks for changed source files.')
defvar('eval_expression_print_length', 400,
       'How much of a result is shown in the echo area; all of it goes to *Messages*.')

_TOKEN = re.compile(r'[^\W\d]\w*|\d[\w.]*|\S')


def namespace_for(buffer):
    """``(module, file name)`` that code from ``buffer`` is evaluated in."""
    if buffer.filename:
        module = live.module_for_path(buffer.filename)
        if module is not None:
            live.track(module)
            return module, buffer.filename
        return user, buffer.filename
    return user, f'<{buffer.name}>'


def show(report, insert=False):
    """Tell the user how an evaluation went.  Returns the report."""
    if report.error is not None:
        error = report.error
        if report.traceback:
            ed._log(report.traceback.rstrip('\n'))
        text = error.msg if isinstance(error, SyntaxError) else str(error)
        line = getattr(error, 'lineno', None)
        where = f' (line {line})' if line else ''
        ed.message(f'{type(error).__name__}: {text}{where}', log=False)
        ed.ding()
        return report
    if insert:
        ed.current_buffer.insert(repr(report.value))
        return report
    names = ', '.join(report.names)
    if report.value is not None or not names:
        text = repr(report.value)
        limit = int(ed.var('eval_expression_print_length'))
        if len(text) > limit:
            ed._log(text)
            text = text[:limit] + '…'
        ed.message(text, log=len(text) <= limit)
    else:
        ed.message(f'Defined {names}' + (
            f'  (replaced, not patched: {", ".join(report.replaced)})' if report.replaced else ''))
    return report


def evaluate(text, buffer=None, first_line=1):
    """Evaluate ``text`` as code from ``buffer`` (the current one if None)."""
    buffer = buffer or ed.current_buffer
    module, filename = namespace_for(buffer)
    with ed.current(buffer):
        return live.eval_source(module, text, filename, first_line=first_line)


class _Lines:
    """The lines of a buffer, fetched as they are asked for."""

    def __init__(self, text):
        self.text = text

    def __len__(self):
        return self.text.line_count

    def __getitem__(self, index):
        return self.text.line(index)


def _dedent(text):
    """``text`` with the indentation of its first line removed from every line."""
    lines = text.split('\n')
    first = next((line for line in lines if line.strip()), '')
    indent = len(first) - len(first.lstrip())
    return '\n'.join(line[indent:] if line[:indent].strip() == '' else line for line in lines)


@command(interactive=lambda: [read_from_minibuffer('Eval: ', history='eval'),
                              ed.current_prefix_arg])
def eval_expression(text, insert=None):
    """Evaluate Python and show the result.  With a prefix argument, insert it instead."""
    report = live.eval_source(user, text, '<minibuffer>')
    show(report, insert=insert is not None)


def expression_before_point(buffer):
    """``(start, text)`` of the longest Python expression ending at point."""
    text = buffer.text
    first, _ = live.toplevel_block(_Lines(text), buffer.line_at())
    start = text.line_start(first)
    source = buffer.substring(start, buffer.pt)
    stripped = source.rstrip()
    for token in _TOKEN.finditer(stripped):
        candidate = stripped[token.start():]
        try:
            ast.parse(candidate.strip(), mode='eval')
        except SyntaxError:
            continue
        return start + token.start(), candidate
    raise EditorError('No expression before point')


@command('P')
def eval_last_sexp(arg=None):
    """Evaluate the Python expression before point.  With a prefix argument, insert the result."""
    b = ed.current_buffer
    start, text = expression_before_point(b)
    module, filename = namespace_for(b)
    report = live.eval_source(module, text.strip(), filename,
                              first_line=b.text.pos_to_line(start) + 1)
    show(report, insert=arg is not None)


@command
def eval_print_last_sexp():
    """Evaluate the Python expression before point and insert its value on the next line."""
    b = ed.current_buffer
    start, text = expression_before_point(b)
    module, filename = namespace_for(b)
    report = live.eval_source(module, text.strip(), filename,
                              first_line=b.text.pos_to_line(start) + 1)
    if report.error is not None:
        show(report)
        return
    b.insert('\n' + repr(report.value) + '\n')


@command
def eval_defun():
    """Evaluate the top-level definition or statement around point, in its module."""
    b = ed.current_buffer
    text = b.text
    first, last = live.toplevel_block(_Lines(text), b.line_at())
    source = b.substring(text.line_start(first), text.line_end(last))
    if not source.strip():
        raise EditorError('No statement here')
    report = evaluate(source, b, first_line=first + 1)
    show(report)


@command('r')
def eval_region(start, end):
    """Evaluate the region as Python."""
    b = ed.current_buffer
    line_start = b.bol(start)
    source = _dedent(b.substring(line_start, end)) if b.substring(line_start, start).strip() == '' \
        else b.substring(start, end)
    report = evaluate(source, b, first_line=b.text.pos_to_line(start) + 1)
    show(report)
    b.mark_active = False


@command('P')
def eval_buffer(arg=None):
    """Load the buffer as Python, evaluating what changed since it was last loaded.

    With a prefix argument, evaluate every statement afresh."""
    b = ed.current_buffer
    module, filename = namespace_for(b)
    state = live.tracked(filename) if os.path.isabs(filename) else None
    with ed.current(b):
        report = live.load_source(module, b.text.snapshot(), filename, fresh=arg is not None,
                                  baseline=state.source if state else None)
    _announce(report)


def _announce(report):
    if report.error is not None:
        show(report)
    else:
        ed.message('Live: ' + report.summary())
        ed.display.invalidate()
    return report


@command(interactive=lambda: [read_file_name('Load file: ', must_match=True)])
def load_file(path):
    """Load a file of Python into the editor."""
    _, report = live.load_file(path)
    _announce(report)


@command('P')
def live_reload(arg=None):
    """Load this buffer's file into the running editor, saving it first.

    With a prefix argument, evaluate every statement of the file afresh rather
    than only what changed."""
    b = ed.current_buffer
    if not b.filename:
        raise EditorError('This buffer is not visiting a file')
    if b.modified:
        from . import files
        files.write_buffer(b)
    if live.tracked(b.filename) is None:
        module = live.module_for_path(b.filename)
        if module is not None:
            live.track(module)
    _, report = live.load_file(b.filename, fresh=arg is not None)
    _announce(report)


def _after_save():
    """Load a saved file that is the source of a loaded module."""
    b = ed.current_buffer
    if not b.filename or not b.filename.endswith('.py'):
        return
    if live.tracked(b.filename) is None:
        module = live.module_for_path(b.filename)
        if module is None:
            return
        live.track(module)
    _announce(live.reload_path(b.filename))


def watch():
    """Load every watched source file that has changed on disk."""
    if not ed.var('live_watch'):
        return
    for report in live.poll():
        _announce(report)


def start_watching():
    add_hook('after_save_hook', _after_save)
    interval = float(ed.var('live_watch_interval') or 0)
    if interval > 0:
        ed.run_at_time(interval, watch, repeat=interval)


for _keys, _name in (('M-:', 'eval-expression'), ('C-x C-e', 'eval-last-sexp')):
    global_map.bind(_keys, _name)
