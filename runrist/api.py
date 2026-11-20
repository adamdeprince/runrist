# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""What extensions use.

``from runrist.api import *`` brings in the editor object, the decorators that
define commands, modes and variables, and functions that act on the current
buffer under the names Emacs gives them::

    from runrist.api import *

    @command('p')
    def duplicate_line(n=1):
        "Insert N copies of the current line below it."
        line = buffer_substring(line_beginning_position(), line_end_position())
        with save_excursion():
            goto_char(line_end_position())
            insert(('\\n' + line) * n)

    global_set_key('C-c d', duplicate_line)

Positions count from zero and ranges exclude their end, as in Python.
"""

from . import faces, files, fold, keymap, live, minibuffer, variables
from .buffer import Buffer, Marker, Overlay, inhibit_read_only
from .command import command, commands, defalias
from .editor import ed, global_map
from .errors import (BeginningOfBuffer, BufferReadOnly, EditorError, EndOfBuffer, Quit,
                     SearchFailed)
from .faces import defface, deftheme, load_theme, set_face
from .keymap import Keymap, kbd, key_description
from .minibuffer import (completing_read, read_buffer, read_char, read_command,
                         read_file_name, read_from_minibuffer, read_number, read_string,
                         y_or_n_p, yes_or_no_p)
from .mode import (add_auto_mode, major_mode, minor_mode, minor_mode_enabled,
                   set_major_mode, set_minor_mode)
from .variables import (add_hook, defhook, defvar, remove_hook, run_hooks, setq, v)


def select_ai(provider, **settings):
    """Select the AI provider and optional model and effort for this editor run."""
    from .ai import select_ai as select
    return select(provider, **settings)


def current_buffer():
    return ed.current_buffer


def selected_window():
    return ed.selected_window


def message(text=None):
    """Show ``text`` in the echo area and log it in ``*Messages*``."""
    return ed.message(text)


def point():
    return ed.current_buffer.pt


def point_min():
    return ed.current_buffer.point_min


def point_max():
    return ed.current_buffer.point_max


def goto_char(position):
    return ed.current_buffer.goto_char(position)


def insert(*strings):
    ed.current_buffer.insert(*strings)


def delete_region(start, end):
    return ed.current_buffer.delete_region(start, end)


def buffer_string():
    return ed.current_buffer.string()


def buffer_substring(start, end):
    return ed.current_buffer.substring(start, end)


def char_after(position=None):
    return ed.current_buffer.char_after(position)


def char_before(position=None):
    return ed.current_buffer.char_before(position)


def bolp():
    return ed.current_buffer.bolp()


def eolp():
    return ed.current_buffer.eolp()


def bobp():
    return ed.current_buffer.bobp()


def eobp():
    return ed.current_buffer.eobp()


def forward_line(n=1):
    return ed.current_buffer.forward_line(n)


def line_beginning_position(n=1):
    return ed.current_buffer.line_beginning_position(n)


def line_end_position(n=1):
    return ed.current_buffer.line_end_position(n)


def line_number_at_pos(position=None):
    return ed.current_buffer.line_number(position)


def current_column():
    return ed.current_buffer.current_column()


def move_to_column(column, force=False):
    return ed.current_buffer.move_to_column(column, force)


def current_indentation():
    return ed.current_buffer.current_indentation()


def search_forward(string, bound=None, noerror=False, count=1):
    return ed.current_buffer.search_forward(string, bound, noerror=noerror, count=count)


def search_backward(string, bound=None, noerror=False, count=1):
    return ed.current_buffer.search_backward(string, bound, noerror=noerror, count=count)


def re_search_forward(pattern, bound=None, noerror=False, count=1):
    return ed.current_buffer.re_search_forward(pattern, bound, noerror=noerror, count=count)


def re_search_backward(pattern, bound=None, noerror=False, count=1):
    return ed.current_buffer.re_search_backward(pattern, bound, noerror=noerror, count=count)


def looking_at(pattern):
    return ed.current_buffer.looking_at(pattern)


def looking_back(pattern, limit=None):
    return ed.current_buffer.looking_back(pattern, limit)


def mark():
    return ed.current_buffer.mark_pos()


def region_beginning():
    return ed.current_buffer.region()[0]


def region_end():
    return ed.current_buffer.region()[1]


def region_active_p():
    return ed.current_buffer.region_active()


def save_excursion():
    """``with save_excursion():`` restores point afterwards."""
    return ed.current_buffer.save_excursion()


def with_current_buffer(buffer):
    """``with with_current_buffer(b):`` makes ``b`` current for the block."""
    return ed.current(ed.get_buffer(buffer) or buffer)


def get_buffer(name):
    return ed.get_buffer(name)


def get_buffer_create(name):
    return ed.get_buffer_create(name)


def switch_to_buffer(buffer):
    return ed.switch_to_buffer(buffer)


def find_file(filename):
    return ed.switch_to_buffer(files.find_file_noselect(filename))


def global_set_key(keys, binding):
    """Bind a key sequence, in ``kbd`` notation, for every buffer."""
    global_map.bind(keys, binding)


def global_unset_key(keys):
    global_map.unbind(keys)


def local_set_key(keys, binding):
    """Bind a key sequence in the current buffer's major mode."""
    ed.current_buffer.keymap.bind(keys, binding)


def call_interactively(name):
    return ed.call_interactively(name)


def run_at_time(delay, function, repeat=None):
    return ed.run_at_time(delay, function, repeat)


def run_with_idle_timer(seconds, function, repeat=True):
    return ed.run_with_idle_timer(seconds, function, repeat)


__all__ = [name for name in dir() if not name.startswith('_')]
