# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Bringing the editor up: defining everything, and loading the user's own code."""

import os

from . import files, live

SCRATCH = """\
# This buffer is for Python you do not mean to keep.  It runs in the editor itself:
# C-j evaluates the expression before point and inserts its value,
# C-M-x evaluates the definition around point, and M-: asks for an expression.

"""

_HINTS = (
    ('C-x C-f', 'visit a file', 'C-h k', 'what a key does'),
    ('C-x C-s', 'save', 'C-h b', 'every key that does something'),
    ('C-x b', 'switch buffer', 'C-h f', 'describe a command'),
    ('M-x', 'run a command by name', 'C-h m', 'describe this mode'),
    ('C-s', 'search as you type', 'M-%', 'replace, asking'),
    ('<f3> <f4>', 'record a macro, play it', 'C-u 0 <f4>', 'play it until it fails'),
    ('<backtab>', 'fold the buffer to an outline', 'C-c @ C-c', 'fold one block'),
    ('C-M-x', 'evaluate a definition, live', 'M-:', 'evaluate Python'),
    ('C-x 2  C-x 3', 'split the window', 'C-x C-c', 'leave'),
)


def define_everything():
    """Import every module that defines faces, modes, commands or keys."""
    from . import themes, modes, commands    # noqa: F401
    live.track_package('runrist')


def init_file():
    return os.path.join(files.config_directory(), 'init.py')


def load_init_file(path=None):
    """Load the user's ``init.py``, if there is one.  Returns the report or None."""
    from .editor import ed
    path = path or init_file()
    if not os.path.isfile(path):
        return None
    _, report = live.load_file(path, 'runrist_init')
    if not report.ok:
        ed.message(f'Error in init file: {report.summary()}')
        if report.traceback:
            ed._log(report.traceback.rstrip('\n'))
    return report


def prepare_scratch():
    """Give ``*scratch*`` its mode and its opening words."""
    from .editor import ed
    from .mode import set_major_mode
    scratch = ed.get_buffer('*scratch*')
    if scratch is None or scratch.size:
        return
    set_major_mode(scratch, 'python-interaction-mode')
    scratch.insert(SCRATCH)
    scratch.undo_boundary()
    scratch.clear_undo()
    scratch.set_modified(False)


def after_init(args=None):
    """Start the background work and run ``--eval`` code."""
    from . import pyeval, user
    from .commands.files import start_background_file_work
    from .editor import ed
    prepare_scratch()
    start_background_file_work()
    pyeval.start_watching()
    for code in (getattr(args, 'eval', None) or ()):
        report = live.eval_source(user, code, '<command line>')
        if report.error is not None:
            ed.message(f'--eval: {type(report.error).__name__}: {report.error}')


def splash():
    """What a session with no file to visit starts with."""
    from . import __version__
    from .buffer import inhibit_read_only
    from .editor import ed
    buffer = ed.get_buffer('*runrist*') or ed.new_buffer('*runrist*', 'special-mode')
    marks = []
    parts = []
    at = 0

    def add(text, face=None):
        nonlocal at
        if face:
            marks.append((at, at + len(text), face))
        parts.append(text)
        at += len(text)

    indent = ' ' * 6
    add('\n\n')
    add(indent + 'ᚱ ᚢ ᚾ ᚱ ᛁ ᛋ ᛏ', 'splash-rune')
    add('\n\n')
    add(indent + 'runrist', 'splash-title')
    add(f'  {__version__}\n', 'splash-text')
    add(indent + 'A live, Python-extensible editor in the Emacs tradition.\n\n\n', 'splash-text')
    for key, what, other, meaning in _HINTS:
        add(indent)
        add(f'{key:<14}', 'splash-key')
        add(f'{what:<32}', 'splash-text')
        add(f'{other:<12}', 'splash-key')
        add(meaning + '\n', 'splash-text')
    add('\n\n' + indent + 'Terminal: ' + ed.tty.caps.describe() + '\n', 'splash-text')
    add(indent + 'Its own source is live: edit a command, save, and it is changed.\n',
        'splash-text')
    with inhibit_read_only():
        buffer.remove_overlays()
        buffer.erase()
        buffer.insert(''.join(parts))
    for start, end, face in marks:
        buffer.make_overlay(start, end, face=face)
    buffer.undo_enabled = False
    buffer.read_only = True
    buffer.set_modified(False)
    buffer.goto_char(0)
    ed.switch_to_buffer(buffer)
