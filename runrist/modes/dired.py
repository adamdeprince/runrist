# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Dired: a directory as a buffer.

Visiting a directory lists it.  ``RET`` visits the file on the line, ``o`` in
another window, ``^`` goes up to the parent, ``g`` lists again, ``+`` makes a
directory, ``R`` renames, ``D`` deletes after asking.
"""

import os
import stat
import time

from ..buffer import inhibit_read_only
from ..command import command
from ..editor import ed, global_map
from ..errors import EditorError
from ..minibuffer import abbreviate_file_name, read_file_name, read_string, yes_or_no_p
from ..mode import major_mode, major_modes


@major_mode('dired-mode', 'Dired', parent='special-mode')
def dired_mode(buffer):
    """A directory listing.  RET visits, ^ goes up, g refreshes, + makes a directory,
    R renames and D deletes."""
    buffer.set('truncate_lines', True)
    buffer.set('hl_line_mode', True)


def _size(n):
    for unit in ('', 'K', 'M', 'G', 'T'):
        if n < 1024 or unit == 'T':
            return f'{n:.0f}{unit}' if unit == '' or n >= 10 else f'{n:.1f}{unit}'
        n /= 1024
    return str(n)


def _fill(buffer, directory, keep=None):
    entries = []
    try:
        with os.scandir(directory) as found:
            for entry in found:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append((entry.name, info, entry.is_symlink(),
                                entry.is_dir(follow_symlinks=True) if not entry.is_symlink()
                                else os.path.isdir(entry.path)))
    except OSError as error:
        raise EditorError(f'Cannot list {abbreviate_file_name(directory)}: {error.strerror}') \
            from None
    entries.sort(key=lambda item: (not item[3], item[0].lower()))
    header = f' {abbreviate_file_name(directory)}'
    lines = [header, '']
    marks = [(0, len(header), 'dired-header')]
    names = [None, None]
    at = len(header) + 2
    for name, info, is_link, is_dir in entries:
        when = time.strftime('%Y-%m-%d %H:%M', time.localtime(info.st_mtime))
        shown = name + ('/' if is_dir else '')
        if is_link:
            try:
                shown += ' -> ' + os.readlink(os.path.join(directory, name))
            except OSError:
                pass
        prefix = f'  {stat.filemode(info.st_mode)} {_size(info.st_size):>6}  {when}  '
        lines.append(prefix + shown)
        face = 'dired-symlink' if is_link else 'dired-directory' if is_dir else None
        if face:
            marks.append((at + len(prefix), at + len(prefix) + len(shown), face))
        names.append(name)
        at += len(prefix) + len(shown) + 1
    with inhibit_read_only():
        buffer.widen()
        buffer.remove_overlays()
        buffer.erase()
        buffer.insert('\n'.join(lines) + '\n')
    for start, end, face in marks:
        buffer.make_overlay(start, end, face=face)
    buffer.locals['_dired'] = names
    buffer.locals['revert_buffer_function'] = lambda: _fill(buffer, directory,
                                                            _name_at(buffer, quiet=True))
    buffer.set_modified(False)
    line = names.index(keep) if keep in names else min(2, len(names) - 1)
    buffer.goto_char(buffer.text.line_start(max(0, line)))
    _to_name(buffer)


def _to_name(buffer):
    line = buffer.line_at()
    names = buffer.locals.get('_dired') or []
    if line < len(names) and names[line]:
        text = buffer.text.line(line)
        shown = names[line]
        column = text.find(shown, 30) if len(text) > 30 else text.find(shown)
        if column >= 0:
            buffer.goto_char(buffer.text.line_start(line) + column)


def dired_noselect(directory):
    """The buffer listing ``directory``."""
    directory = os.path.abspath(directory).rstrip('/') or '/'
    for buffer in ed.buffers:
        if buffer.locals.get('_dired_directory') == directory:
            _fill(buffer, directory, _name_at(buffer, quiet=True))
            return buffer
    buffer = ed.new_buffer(os.path.basename(directory) or '/', 'dired-mode')
    buffer.undo_enabled = False
    buffer.directory = directory
    buffer.locals['_dired_directory'] = directory
    _fill(buffer, directory)
    return buffer


def _name_at(buffer, quiet=False):
    names = buffer.locals.get('_dired') or []
    line = buffer.line_at()
    if line < len(names) and names[line]:
        return names[line]
    if quiet:
        return None
    raise EditorError('No file on this line')


def _path_at():
    buffer = ed.current_buffer
    return os.path.join(buffer.locals['_dired_directory'], _name_at(buffer))


@command(interactive=lambda: [read_file_name('Dired (directory): ', directories_only=True)])
def dired(directory):
    """List a directory in a buffer."""
    if not os.path.isdir(directory):
        directory = os.path.dirname(directory)
    ed.switch_to_buffer(dired_noselect(directory))


@command
def dired_find_file():
    """Visit the file or directory on this line."""
    from .. import files
    ed.switch_to_buffer(files.find_file_noselect(_path_at()))


@command
def dired_find_file_other_window():
    """Visit the file on this line in another window."""
    from .. import files
    ed.display_buffer(files.find_file_noselect(_path_at()), select=True)


@command
def dired_up_directory():
    """List the parent directory, with point on the directory just left."""
    here = ed.current_buffer.locals['_dired_directory']
    parent = os.path.dirname(here) or '/'
    buffer = dired_noselect(parent)
    names = buffer.locals.get('_dired') or []
    name = os.path.basename(here)
    if name in names:
        buffer.goto_char(buffer.text.line_start(names.index(name)))
        _to_name(buffer)
    ed.switch_to_buffer(buffer)


@command('p')
def dired_next_line(n=1):
    """Move down to the next file."""
    buffer = ed.current_buffer
    buffer.forward_line(n)
    if buffer.eobp():
        buffer.forward_line(-1)
    _to_name(buffer)


@command('p')
def dired_previous_line(n=1):
    """Move up to the previous file."""
    dired_next_line(-n)


@command
def dired_refresh():
    """Read the directory again."""
    buffer = ed.current_buffer
    _fill(buffer, buffer.locals['_dired_directory'], _name_at(buffer, quiet=True))


@command(interactive=lambda: [read_string('Create directory: ')])
def dired_create_directory(name):
    """Make a directory here."""
    buffer = ed.current_buffer
    path = os.path.join(buffer.locals['_dired_directory'], os.path.expanduser(name))
    try:
        os.makedirs(path)
    except OSError as error:
        raise EditorError(f'Cannot create {name}: {error.strerror}') from None
    _fill(buffer, buffer.locals['_dired_directory'], os.path.basename(path.rstrip('/')))


@command
def dired_do_delete():
    """Delete the file on this line, after asking."""
    import shutil
    buffer = ed.current_buffer
    path = _path_at()
    name = os.path.basename(path)
    is_dir = os.path.isdir(path) and not os.path.islink(path)
    if not yes_or_no_p(f'Delete {name}' + (' and everything in it?' if is_dir else '?')):
        return
    try:
        if is_dir:
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError as error:
        raise EditorError(f'Cannot delete {name}: {error.strerror}') from None
    _fill(buffer, buffer.locals['_dired_directory'])
    ed.message(f'Deleted {name}', log=False)


@command
def dired_do_rename():
    """Rename or move the file on this line."""
    buffer = ed.current_buffer
    path = _path_at()
    target = read_file_name(f'Rename {os.path.basename(path)} to: ',
                            directory=buffer.locals['_dired_directory'])
    if os.path.isdir(target):
        target = os.path.join(target, os.path.basename(path))
    if os.path.exists(target) and not yes_or_no_p(
            f'{abbreviate_file_name(target)} exists; overwrite?'):
        return
    try:
        os.replace(path, target)
    except OSError as error:
        raise EditorError(f'Cannot rename: {error.strerror}') from None
    for other in ed.buffers:
        if other.filename and os.path.realpath(other.filename) == os.path.realpath(path):
            from .. import files
            files.set_visited_file(other, target)
    _fill(buffer, buffer.locals['_dired_directory'], os.path.basename(target))


_map = major_modes['dired-mode'].keymap
for _keys, _name in (('RET', 'dired-find-file'), ('f', 'dired-find-file'),
                     ('e', 'dired-find-file'), ('o', 'dired-find-file-other-window'),
                     ('^', 'dired-up-directory'), ('g', 'dired-refresh'),
                     ('n', 'dired-next-line'), ('p', 'dired-previous-line'),
                     ('C-n', 'dired-next-line'), ('C-p', 'dired-previous-line'),
                     ('<down>', 'dired-next-line'), ('<up>', 'dired-previous-line'),
                     ('SPC', 'dired-next-line'), ('+', 'dired-create-directory'),
                     ('D', 'dired-do-delete'), ('R', 'dired-do-rename')):
    _map.bind(_keys, _name)
global_map.bind('C-x d', 'dired')
