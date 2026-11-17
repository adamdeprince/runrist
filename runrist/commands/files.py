# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Commands that visit and save files, and that leave the editor."""

import os

from .. import files
from ..command import command
from ..editor import ed, global_map
from ..errors import EditorError
from ..minibuffer import (abbreviate_file_name, read_file_name, y_or_n_p, yes_or_no_p)


def buf():
    return ed.current_buffer


@command('FFind file: ')
def find_file(filename):
    """Edit file FILENAME, in a buffer of its own."""
    ed.switch_to_buffer(files.find_file_noselect(filename))


@command('FFind file in other window: ')
def find_file_other_window(filename):
    """Edit file FILENAME in another window."""
    ed.display_buffer(files.find_file_noselect(filename), select=True)


@command('FFind file read-only: ')
def find_file_read_only(filename):
    """Edit file FILENAME without being able to change it."""
    buffer = files.find_file_noselect(filename)
    buffer.read_only = True
    ed.switch_to_buffer(buffer)


def _read_alternate():
    b = buf()
    return [read_file_name('Find alternate file: ',
                           initial=abbreviate_file_name(b.filename) if b.filename else None)]


@command(interactive=_read_alternate)
def find_alternate_file(filename):
    """Replace this buffer with one visiting FILENAME."""
    old = buf()
    if old.modified and old.filename and not yes_or_no_p(
            f'Buffer {old.name} is modified; kill anyway?'):
        raise EditorError('Aborted')
    new = files.find_file_noselect(filename)
    ed.switch_to_buffer(new)
    if new is not old:
        ed.kill_buffer(old)


@command('P')
def save_buffer(arg=None):
    """Save the current buffer in its file."""
    b = buf()
    if not b.filename:
        name = read_file_name('File to save in: ')
        files.set_visited_file(b, name)
        files.normal_mode(b)
    elif not b.modified and os.path.exists(b.filename):
        ed.message('(No changes need to be saved)', log=False)
        return
    if files.changed_on_disk(b) and os.path.exists(b.filename) and not yes_or_no_p(
            f'{os.path.basename(b.filename)} has changed since visited or saved.  Save anyway?'):
        raise EditorError('Save not confirmed')
    _write(b)


def _write(b):
    """Save ``b`` and say so, keeping anything a save hook had to say."""
    before = ed.echo
    size = files.write_buffer(b)
    lines = b.count_lines(0, b.size)
    text = f'Wrote {abbreviate_file_name(b.filename)}  ({lines} lines, {size} bytes)'
    if ed.echo and ed.echo != before and isinstance(ed.echo, str):
        text += '  —  ' + ed.echo
    ed.message(text)


@command('FWrite file: ')
def write_file(filename):
    """Write the current buffer into file FILENAME and visit that file."""
    b = buf()
    if os.path.isdir(filename):
        filename = os.path.join(filename, os.path.basename(b.filename or b.name))
    if os.path.exists(filename) and os.path.realpath(filename) != os.path.realpath(
            b.filename or '') and not y_or_n_p(
                f'File {abbreviate_file_name(filename)} exists; overwrite?'):
        raise EditorError('Canceled')
    files.set_visited_file(b, filename)
    files.normal_mode(b)
    _write(b)


def _unsaved():
    return [b for b in ed.buffers if b.filename and b.modified]


@command('P')
def save_some_buffers(arg=None):
    """Save modified file-visiting buffers, asking about each.

    With a prefix argument, save them all without asking."""
    saved = 0
    for b in _unsaved():
        if arg is not None or y_or_n_p(f'Save file {abbreviate_file_name(b.filename)}?'):
            files.write_buffer(b)
            saved += 1
    ed.message(f'Saved {saved} file{"s" * (saved != 1)}' if saved
               else '(No files need saving)', log=False)


@command('fInsert file: ')
def insert_file(filename):
    """Insert the contents of file FILENAME after point."""
    b = buf()
    with open(filename, 'rb') as source:
        text, *_ = files.decode(source.read())
    start = b.pt
    b.insert(text)
    b.push_mark()
    b.goto_char(start)


@command
def revert_buffer():
    """Discard changes and read the buffer's file again."""
    b = buf()
    if not b.filename:
        revert = b.locals.get('revert_buffer_function')
        if revert is None:
            raise EditorError('Buffer does not seem to be associated with any file')
        revert()
        return
    if b.modified and not yes_or_no_p(
            f'Revert buffer from file {abbreviate_file_name(b.filename)}?'):
        return
    files.revert_buffer(b)
    ed.message(f'Reverted {b.name}', log=False)


@command
def recover_this_file():
    """Replace the buffer's text with its auto-save file, if there is one."""
    b = buf()
    if not b.filename:
        raise EditorError('This buffer is not visiting a file')
    path = files.auto_save_name(b.filename)
    if not os.path.exists(path):
        raise EditorError('No auto-save file for this buffer')
    if not yes_or_no_p(f'Recover {b.name} from its auto-save file?'):
        return
    with open(path, 'rb') as source:
        text, *_ = files.decode(source.read())
    b.replace_region(0, b.size, text)
    ed.message('Recovered; save the buffer to keep it', log=False)


@command('P')
def save_buffers_kill_terminal(arg=None):
    """Offer to save each modified buffer, then leave the editor."""
    from ..minibuffer import read_char
    save_all = arg is not None
    for b in _unsaved():
        if save_all:
            files.write_buffer(b)
            continue
        while True:
            answer = read_char(f'Save file {abbreviate_file_name(b.filename)}? '
                               '(y, n, ! save all, q quit without saving) ')
            if answer in 'yY ':
                files.write_buffer(b)
                break
            if answer in 'nN\x7f':
                break
            if answer == '!':
                save_all = True
                files.write_buffer(b)
                break
            if answer in 'qQ':
                kill_emacs()
    if _unsaved() and not yes_or_no_p('Modified buffers exist; exit anyway?'):
        return
    kill_emacs()


@command
def kill_emacs():
    """Leave the editor at once, saving nothing."""
    ed.run_hooks('kill_emacs_hook')
    for b in ed.buffers:
        if not b.modified:
            files.delete_auto_save(b)
    ed.exit(0)


@command
def pwd():
    """Show the current buffer's default directory."""
    ed.message('Directory ' + abbreviate_file_name(buf().directory or os.getcwd()), log=False)


@command('DChange default directory: ')
def cd(directory):
    """Make DIRECTORY the current buffer's default directory."""
    if not os.path.isdir(directory):
        raise EditorError(f'{directory} is not a directory')
    buf().directory = directory.rstrip('/') or '/'
    pwd()


def start_background_file_work():
    """Begin auto-saving and following files that change on disk."""
    interval = ed.var('auto_save_interval')
    if interval:
        ed.run_with_idle_timer(float(interval), files.do_auto_save)
    ed.run_at_time(2.0, files.auto_revert, repeat=2.0)


from ..variables import add_hook  # noqa: E402

add_hook('focus_in_hook', files.auto_revert)

for _keys, _name in (
        ('C-x C-f', 'find-file'), ('C-x 4 f', 'find-file-other-window'),
        ('C-x 4 C-f', 'find-file-other-window'), ('C-x C-r', 'find-file-read-only'),
        ('C-x C-v', 'find-alternate-file'), ('C-x C-s', 'save-buffer'),
        ('C-x C-w', 'write-file'), ('C-x s', 'save-some-buffers'), ('C-x i', 'insert-file'),
        ('C-x C-c', 'save-buffers-kill-terminal')):
    global_map.bind(_keys, _name)
