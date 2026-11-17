# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Visiting and saving files.

A file is read whole and decoded as UTF-8.  Bytes that are not valid UTF-8
survive the round trip: they are carried as lone surrogates, shown as
``\\xNN``, and written back as the bytes they were.  Line endings are noticed
on reading -- a file whose lines all end in CRLF, or all in CR, is shown with
plain newlines and saved the way it came -- and so is a byte order mark.

Saving writes a new file beside the old one and renames it into place, so a
crash or a full disk never leaves half a file.  Unsaved changes are also
copied now and then to an auto-save file under the state directory, never
next to the file itself, and offered back by ``recover-this-file``.
"""

import codecs
import os
import re
import stat

from . import mode as modes
from .buffer import inhibit_read_only
from .errors import EditorError
from .minibuffer import abbreviate_file_name, expand_file_name
from .variables import defhook, defvar

defvar('make_backup_files', False,
       'Whether the first save of a session keeps the old contents as NAME~.')
defvar('auto_save_interval', 30.0,
       'Seconds of idleness after which modified buffers are auto-saved; None for never.')
defvar('auto_revert_mode', True,
       'Whether an unmodified buffer follows its file when the file changes on disk.')
defhook('find_file_hook', 'Functions run in a buffer after a file is read into it.')
defhook('before_save_hook', 'Functions run in a buffer before it is saved.')
defhook('after_save_hook', 'Functions run in a buffer after it is saved.')

_BOMS = ((codecs.BOM_UTF8, 'utf-8'), (codecs.BOM_UTF32_LE, 'utf-32-le'),
         (codecs.BOM_UTF32_BE, 'utf-32-be'), (codecs.BOM_UTF16_LE, 'utf-16-le'),
         (codecs.BOM_UTF16_BE, 'utf-16-be'))


def _ed():
    from .editor import ed
    return ed


def state_directory(*parts):
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    path = os.path.join(base, 'runrist', *parts)
    os.makedirs(path, exist_ok=True)
    return path


def config_directory():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'runrist')


def decode(data):
    """``(text, encoding, eol, bom)`` for the bytes of a file."""
    encoding, bom = 'utf-8', False
    for mark, name in _BOMS:
        if data.startswith(mark):
            encoding, bom = name, True
            data = data[len(mark):]
            break
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        text = data.decode('utf-8', 'surrogateescape')
        encoding = 'utf-8'
    crlf = text.count('\r\n')
    lf = text.count('\n') - crlf
    cr = text.count('\r') - crlf
    eol = '\n'
    if crlf and not lf and not cr:
        eol = '\r\n'
        text = text.replace('\r\n', '\n')
    elif cr and not lf and not crlf:
        eol = '\r'
        text = text.replace('\r', '\n')
    return text, encoding, eol, bom


def encode(text, encoding='utf-8', eol='\n', bom=False):
    if eol != '\n':
        text = text.replace('\n', eol)
    data = text.encode(encoding, 'surrogateescape')
    if bom:
        data = dict((name, mark) for mark, name in _BOMS).get(encoding, b'') + data
    return data


def write_bytes(path, data):
    """Replace the file at ``path`` with ``data`` in one step."""
    import tempfile
    target = os.path.realpath(path)
    directory = os.path.dirname(target) or '.'
    try:
        existing = os.stat(target)
    except OSError:
        existing = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.' + os.path.basename(target) + '.',
                                         suffix='.tmp', dir=directory)
    except OSError:
        # The directory cannot take a new file; all that is left is to write in place.
        with open(target, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        return
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if existing is not None:
            os.chmod(temporary, stat.S_IMODE(existing.st_mode))
            try:
                os.chown(temporary, existing.st_uid, existing.st_gid)
            except OSError:
                pass
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def modification_time(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


# -- choosing a mode -----------------------------------------------------------------

_MODE_LINE = re.compile(r'-\*-.*?\bmode:\s*([\w+-]+).*?-\*-|-\*-\s*([\w+-]+)\s*-\*-')


def mode_for(filename, first_line=''):
    """Name of the major mode for a file, from its first line or its name."""
    match = _MODE_LINE.search(first_line)
    if match:
        name = (match.group(1) or match.group(2)).lower()
        for candidate in (name, name + '-mode'):
            if candidate in modes.major_modes:
                return candidate
    if filename:
        name = filename
        while True:
            for pattern, mode_name in modes.auto_mode_alist:
                if pattern.search(name) and mode_name in modes.major_modes:
                    return mode_name
            stripped = re.sub(r'(\.(in|orig|bak|dist|new|old|save|tmp)|~)$', '', name)
            if stripped == name:
                break
            name = stripped
    if first_line.startswith('#!'):
        interpreter = first_line[2:].strip().split()
        if interpreter:
            program = os.path.basename(interpreter[0])
            if program == 'env' and len(interpreter) > 1:
                program = next((p for p in interpreter[1:] if not p.startswith('-')), program)
            for pattern, mode_name in modes.interpreter_mode_alist:
                if pattern.fullmatch(program) and mode_name in modes.major_modes:
                    return mode_name
    return None


def normal_mode(buffer):
    """Put ``buffer`` in the major mode its file calls for."""
    name = mode_for(buffer.filename, buffer.text.line(0))
    if name is None:
        name = 'fundamental-mode'
    modes.set_major_mode(buffer, name)


# -- visiting ------------------------------------------------------------------------

def buffer_visiting(path):
    ed = _ed()
    real = os.path.realpath(path)
    for buffer in ed.buffers:
        if buffer.filename and os.path.realpath(buffer.filename) == real:
            return buffer
    return None


def _buffer_name(path):
    ed = _ed()
    base = os.path.basename(path.rstrip('/')) or path
    if ed.get_buffer(base) is None:
        return base
    parent = os.path.basename(os.path.dirname(path.rstrip('/')))
    return ed.generate_new_buffer_name(f'{base}<{parent}>' if parent else base)


def find_file_noselect(path):
    """The buffer visiting ``path``, reading the file into a new one if needed."""
    ed = _ed()
    path = expand_file_name(path)
    if os.path.isdir(path):
        from .modes import dired
        return dired.dired_noselect(path)
    buffer = buffer_visiting(path)
    if buffer is not None:
        return buffer
    exists = os.path.exists(path)
    if exists:
        with open(path, 'rb') as source:
            data = source.read()
        text, encoding, eol, bom = decode(data)
    else:
        text, encoding, eol, bom = '', 'utf-8', '\n', False
    buffer = ed.new_buffer(_buffer_name(path))
    buffer.filename = path
    buffer.directory = os.path.dirname(path)
    buffer.encoding, buffer.eol_type, buffer.bom = encoding, eol, bom
    was = buffer.undo_enabled
    buffer.undo_enabled = False
    buffer.insert_at(0, text)
    buffer.undo_enabled = was
    buffer.clear_undo()
    buffer.goto_char(0)
    buffer.set_modified(False)
    buffer.file_mtime = modification_time(path)
    if exists and not os.access(path, os.W_OK):
        buffer.read_only = True
    with ed.current(buffer):
        normal_mode(buffer)
        ed.run_hooks('find_file_hook')
    if not exists:
        ed.message('(New file)', log=False)
    elif os.path.exists(auto_save_name(path)) and \
            (modification_time(auto_save_name(path)) or 0) > (buffer.file_mtime or 0):
        ed.message(f'{buffer.name} has auto-save data; consider M-x recover-this-file')
    return buffer


def changed_on_disk(buffer):
    """Whether the buffer's file was changed by someone else since it was read or saved."""
    if not buffer.filename:
        return False
    now = modification_time(buffer.filename)
    return now != buffer.file_mtime and not (now is None and buffer.file_mtime is None)


def write_buffer(buffer, path=None):
    """Write the whole of ``buffer`` to its file, or to ``path``."""
    ed = _ed()
    path = path or buffer.filename
    if not path:
        raise EditorError('Buffer is not visiting a file')
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        raise EditorError(f'Directory {abbreviate_file_name(directory)} does not exist')
    with ed.current(buffer):
        ed.run_hooks('before_save_hook')
        if buffer.get('require_final_newline') and buffer.size and \
                buffer.text.char_at(buffer.size - 1) != '\n':
            with inhibit_read_only():
                buffer.insert_at(buffer.size, '\n')
    if ed.var('make_backup_files') and not buffer.backed_up and os.path.isfile(path):
        try:
            with open(path, 'rb') as old:
                write_bytes(path + '~', old.read())
        except OSError:
            pass
        buffer.backed_up = True
    data = encode(buffer.text.snapshot(), buffer.encoding, buffer.eol_type, buffer.bom)
    try:
        write_bytes(path, data)
    except OSError as error:
        raise EditorError(f'Cannot write {abbreviate_file_name(path)}: {error.strerror}') from None
    buffer.file_mtime = modification_time(path)
    buffer.set_modified(False)
    delete_auto_save(buffer)
    with ed.current(buffer):
        ed.run_hooks('after_save_hook')
    return len(data)


def set_visited_file(buffer, path):
    """Make ``buffer`` visit ``path`` from now on."""
    ed = _ed()
    path = expand_file_name(path)
    delete_auto_save(buffer)
    buffer.filename = path
    buffer.directory = os.path.dirname(path)
    buffer.name = ed.generate_new_buffer_name(os.path.basename(path), ignore=buffer)
    buffer.file_mtime = modification_time(path)
    buffer.read_only = False


def revert_buffer(buffer):
    """Replace the buffer's text with what is in its file now."""
    ed = _ed()
    if not buffer.filename:
        raise EditorError('Buffer does not seem to be associated with any file')
    try:
        with open(buffer.filename, 'rb') as source:
            text, encoding, eol, bom = decode(source.read())
    except OSError as error:
        raise EditorError(f'Cannot revert: {error.strerror}') from None
    line, column = buffer.text.pos_to_linecol(min(buffer.pt, buffer.size))
    with inhibit_read_only():
        buffer.widen()
        old = buffer.text.snapshot()
        if old != text:
            # Replace only the part that differs, so markers elsewhere stay put.
            head = _common(old, text, str.startswith, lambda s, n: s[:n])
            tail = _common(old[head:], text[head:], str.endswith,
                           lambda s, n: s[len(s) - n:])
            buffer.replace_region(head, len(old) - tail, text[head:len(text) - tail])
    buffer.encoding, buffer.eol_type, buffer.bom = encoding, eol, bom
    buffer.file_mtime = modification_time(buffer.filename)
    buffer.undo_boundary()
    buffer.set_modified(False)
    line = min(line, buffer.text.line_count - 1)
    buffer.goto_char(buffer.text.linecol_to_pos(line, column))
    for window in ed.frame.windows():
        if window.buffer is buffer and window is not ed.selected_window:
            window.pointm.set(min(window.pointm.pos, buffer.size))
    delete_auto_save(buffer)


def _common(a, b, test, part):
    """Length of the longest common prefix (or suffix) of two strings."""
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if test(a, part(b, mid)):
            lo = mid
        else:
            hi = mid - 1
    return lo


def auto_revert():
    """Bring unmodified buffers up to date with files that changed on disk."""
    ed = _ed()
    if not ed.var('auto_revert_mode'):
        return
    for buffer in list(ed.buffers):
        if buffer.filename and not buffer.modified and changed_on_disk(buffer) \
                and os.path.isfile(buffer.filename):
            try:
                revert_buffer(buffer)
            except EditorError:
                continue
            ed.message(f'Reverted {buffer.name}: its file changed on disk', log=False)


# -- auto-saving -----------------------------------------------------------------------

def auto_save_name(path):
    import hashlib
    digest = hashlib.sha1(os.path.realpath(path).encode('utf-8', 'surrogateescape')).hexdigest()
    return os.path.join(state_directory('auto-save'),
                        f'{digest[:16]}-{os.path.basename(path)}')


def delete_auto_save(buffer):
    if buffer.auto_save_path:
        try:
            os.unlink(buffer.auto_save_path)
        except OSError:
            pass
        buffer.auto_save_path = None
        buffer.auto_saved_state = None


def do_auto_save():
    """Write an auto-save file for each modified buffer that needs one."""
    ed = _ed()
    for buffer in ed.buffers:
        if not buffer.filename or not buffer.modified:
            continue
        if buffer.auto_saved_state == buffer._state:
            continue
        try:
            path = auto_save_name(buffer.filename)
            write_bytes(path, encode(buffer.text.snapshot(), buffer.encoding, buffer.eol_type,
                                     buffer.bom))
        except OSError:
            continue
        buffer.auto_save_path = path
        buffer.auto_saved_state = buffer._state
