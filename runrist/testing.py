# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Driving the editor without a terminal, for tests and scripts.

    from runrist.testing import start
    ed = start(text='hello world')
    ed.tty.feed('M-f C-SPC M-f M-w')
    run(ed)
    assert ed.kill_ring[0] == ' world'
"""

from . import startup
from .editor import ed
from .term import FakeTty


def start(text=None, width=80, height=24, name='test.txt', mode=None):
    """Reset the editor onto a fake terminal; return it.

    With ``text``, a buffer holding it is selected and point is at its start."""
    startup.define_everything()
    for buffer in list(ed.buffers):
        buffer.live = False
    if ed.tty is not None and ed.tty.is_fake:
        ed.tty.loop.close()             # each fake terminal has an event loop of its own
    ed.__init__()
    ed.setup(FakeTty(width=width, height=height))
    if text is not None:
        buffer = ed.new_buffer(name, mode or 'fundamental-mode')
        buffer.insert(text)
        buffer.goto_char(0)
        buffer.undo_boundary()
        buffer.clear_undo()
        buffer.set_modified(False)
        ed.switch_to_buffer(buffer)
    return ed


def run(editor=None, keys=None):
    """Run the command loop until the queued input is used up."""
    editor = editor or ed
    if keys is not None:
        editor.tty.feed(keys)
    try:
        editor.command_loop()
    except EOFError:
        pass
    editor.redisplay()
    return editor


def screen(editor=None):
    """The text on the fake terminal's screen, a string per row."""
    editor = editor or ed
    editor.redisplay()
    return list(editor.display.plain)
