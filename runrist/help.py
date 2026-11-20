# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Help: the editor describing itself.

``C-h k`` says what a key does, ``C-h f`` describes a command, ``C-h v`` a
variable, ``C-h b`` lists every key that does something now, ``C-h m``
describes the modes in effect and ``C-h a`` finds commands by a word in their
name.  Help appears in the ``*Help*`` buffer; text shown as a link there can
be followed with ``RET``, and the link to a command's source opens the file
at its definition -- from where ``C-M-x`` puts a change into effect.
"""

import inspect
import re

from . import faces, live, variables
from .buffer import inhibit_read_only
from .command import command, commands, find_command
from .editor import ed, global_map
from .errors import EditorError
from .keymap import Keymap, describe, where_is
from .keys import key_description
from .minibuffer import (abbreviate_file_name, completing_read, read_command,
                         read_from_minibuffer, read_key_sequence, read_variable)
from .mode import major_mode, major_modes

help_map = Keymap('help-map')


class Page:
    """Text for the help buffer, built up piece by piece."""

    def __init__(self):
        self.parts = []
        self.marks = []             # (start, end, face, action)
        self.length = 0

    def add(self, text, face=None, action=None):
        if text:
            if face or action:
                self.marks.append((self.length, self.length + len(text), face, action))
            self.parts.append(text)
            self.length += len(text)
        return self

    def key(self, keys):
        return self.add(key_description(keys), 'help-key-binding')

    def link(self, text, action):
        return self.add(text, 'link', action)

    def show(self, name='*Help*', *, select=False):
        buffer = ed.get_buffer(name) or ed.new_buffer(name, 'help-mode')
        if buffer.major_mode is None or buffer.major_mode.name != 'help-mode':
            from .mode import set_major_mode
            set_major_mode(buffer, 'help-mode')
        with inhibit_read_only():
            buffer.widen()
            buffer.remove_overlays()
            buffer.erase()
            buffer.insert(''.join(self.parts))
        for start, end, face, action in self.marks:
            props = {'kind': 'button'} if action else {}
            if face:
                props['face'] = face
            if action:
                props['action'] = action
            buffer.make_overlay(start, end, **props)
        buffer.undo_enabled = False
        buffer.read_only = True
        buffer.set_modified(False)
        buffer.goto_char(0)
        window = ed.display_buffer(buffer, select=select)
        window.pointm.set(0)
        window.start.set(0)
        window.start_row = 0
        return buffer


@major_mode('help-mode', 'Help', parent='special-mode')
def help_mode(buffer):
    """For reading help.  RET follows the link at point, TAB goes to the next one, q leaves."""
    buffer.set('word_wrap', True)


def find_definition(target):
    """Visit the source of a function or class at its definition."""
    from . import files
    where = live.source_of(target)
    if where is None:
        raise EditorError('The source of that is not available')
    path, line = where
    buffer = files.find_file_noselect(path)
    here = ed.active_window()
    if here.buffer.major_mode is not None and here.buffer.major_mode.name == 'help-mode':
        ed.select_window(here)
    ed.switch_to_buffer(buffer)
    buffer.push_mark()
    buffer.goto_line(line)
    ed.selected_window.recenter = 2


def _source_link(page, target):
    where = live.source_of(target)
    if where is not None:
        path, line = where
        page.add(' in ').link(f'{abbreviate_file_name(path)}:{line}',
                              lambda: find_definition(target))


def _signature(fn):
    try:
        return str(inspect.signature(fn))
    except (TypeError, ValueError):
        return '(...)'


def _bindings_line(page, name):
    keys = where_is(ed.active_maps(ed.active_window().buffer), name)
    if keys:
        page.add('It is bound to ')
        for i, sequence in enumerate(keys[:8]):
            if i:
                page.add(', ')
            page.key(sequence)
        page.add('.\n\n')


def describe_command_page(record, page=None):
    page = page or Page()
    page.add(record.name, 'help-heading').add(' is an interactive command')
    _source_link(page, record.fn)
    page.add('.\n\n')
    _bindings_line(page, record.name)
    page.add(record.name.replace('-', '_') + _signature(record.fn), 'help-argument-name')
    page.add('\n\n')
    page.add(inspect.cleandoc(record.doc) if record.doc else 'Not documented.')
    page.add('\n')
    return page


@command(interactive=lambda: [read_key_sequence('Describe key: ')])
def describe_key(keys):
    """Show what a key sequence does."""
    binding = ed.key_binding(keys, ed.active_maps(ed.active_window().buffer))
    if binding is None or isinstance(binding, list):
        ed.message(f'{key_description(keys)} is undefined', log=False)
        return
    record = find_command(binding)
    page = Page().key(keys).add(' runs the command ')
    if record is None:
        page.add(repr(binding)).add('.\n')
    else:
        page.add(record.name + '\n\n', 'help-heading')
        describe_command_page(record, page)
    page.show()


@command(interactive=lambda: [read_key_sequence('Describe key briefly: ')])
def describe_key_briefly(keys):
    """Say in the echo area what a key sequence runs."""
    binding = ed.key_binding(keys, ed.active_maps(ed.active_window().buffer))
    if binding is None or isinstance(binding, list):
        ed.message(f'{key_description(keys)} is undefined', log=False)
    else:
        record = find_command(binding)
        ed.message(f'{key_description(keys)} runs the command '
                   f'{record.name if record else binding!r}', log=False)


def _command_at_point():
    b = ed.current_buffer
    found = re.search(r'[\w-]+$', b.substring(b.bol(), b.pt))
    after = re.match(r'[\w-]*', b.substring(b.pt, b.eol()))
    word = (found.group() if found else '') + (after.group() if after else '')
    for candidate in (word, word.replace('_', '-')):
        if candidate in commands:
            return candidate
    return None


@command(interactive=lambda: [read_command('Describe command: ', default=_command_at_point())])
def describe_function(name):
    """Show the documentation of a command."""
    record = find_command(name)
    if record is None:
        raise EditorError(f'{name} is not a command')
    describe_command_page(record).show()


@command(interactive=lambda: [read_variable('Describe variable: ')])
def describe_variable(name):
    """Show the value and documentation of a variable."""
    var = variables.variables.get(variables.norm(name))
    if var is None:
        raise EditorError(f'No such variable: {name}')
    buffer = ed.active_window().buffer
    page = Page().add(var.name.replace('_', '-'), 'help-heading')
    page.add(' is a hook' if var.is_hook else ' is a variable')
    if var.module:
        page.add(f' defined in {var.module}')
    page.add('.\n\n')
    if var.name in buffer.locals:
        page.add(f'Its value in {buffer.name} is ').add(repr(buffer.locals[var.name]),
                                                        'font-lock-constant-face')
        page.add('\nIts default value is ').add(repr(var.default), 'font-lock-constant-face')
    else:
        page.add('Its value is ').add(repr(var.default), 'font-lock-constant-face')
    page.add('\n\n')
    if var.local:
        page.add('Setting it makes it local to the buffer it is set in.\n\n')
    page.add(inspect.cleandoc(var.doc) if var.doc else 'Not documented.').add('\n')
    page.show()


def _binding_table(page, title, bindings):
    if not bindings:
        return
    page.add(title + '\n', 'help-heading')
    width = min(24, max(len(key_description(keys)) for keys in bindings))
    for keys in sorted(bindings, key=lambda k: [(len(part), part) for part in k]):
        target = bindings[keys]
        name = target if isinstance(target, str) else getattr(target, '__name__', repr(target))
        text = key_description(keys)
        page.add(text, 'help-key-binding').add(' ' * max(2, width - len(text) + 2))
        record = find_command(target)
        if record is not None:
            page.link(name, lambda record=record: describe_command_page(record).show())
        else:
            page.add(name)
        page.add('\n')
    page.add('\n')


@command
def describe_bindings():
    """List every key sequence that does something in the current buffer."""
    buffer = ed.active_window().buffer
    page = Page()
    seen = set()
    for mode in ed.active_minor_modes(buffer):
        if mode.keymap is not None:
            found = {k: v for k, v in describe([mode.keymap]).items() if k not in seen}
            seen.update(found)
            _binding_table(page, f'Minor mode {mode.name}', found)
    if buffer.keymap is not None:
        found = {k: v for k, v in describe([buffer.keymap]).items() if k not in seen}
        seen.update(found)
        title = buffer.major_mode.title if buffer.major_mode else 'Local'
        _binding_table(page, f'{title} mode', found)
    found = {k: v for k, v in describe([global_map]).items() if k not in seen}
    _binding_table(page, 'Global', found)
    page.show()


@command
def describe_mode():
    """Describe the major mode of the current buffer and the minor modes switched on in it."""
    buffer = ed.active_window().buffer
    mode = buffer.major_mode
    page = Page()
    if mode is not None:
        page.add(f'{mode.title} mode', 'help-heading')
        chain = [m.name for m in mode.chain()[:-1]]
        if chain:
            page.add('  (derived from ' + ', '.join(reversed(chain)) + ')')
        page.add('\n\n')
        page.add(inspect.cleandoc(mode.doc) if mode.doc else 'Not documented.')
        page.add('\n\n')
        if mode.setup is not None:
            page.add('Defined')
            _source_link(page, mode.setup)
            page.add('.\n\n')
        if buffer.keymap is not None:
            _binding_table(page, 'Keys', describe([buffer.keymap]))
    active = ed.active_minor_modes(buffer)
    if active:
        page.add('Minor modes\n', 'help-heading')
        for minor in active:
            page.add(f'{minor.name}', 'help-key-binding')
            page.add('  ' + (minor.doc.strip().split('\n')[0] if minor.doc else '') + '\n')
    page.show()


@command(interactive=lambda: [read_from_minibuffer('Search for command (words): ',
                                                   history='apropos')])
def apropos_command(pattern):
    """List the commands whose names contain every word given."""
    words = pattern.lower().split()
    found = sorted(name for name in commands if all(word in name for word in words))
    if not found:
        raise EditorError(f'No commands match {pattern!r}')
    maps = ed.active_maps(ed.active_window().buffer)
    page = Page().add(f'Commands matching "{pattern}"\n\n', 'help-heading')
    for name in found:
        record = commands[name]
        page.link(name, lambda record=record: describe_command_page(record).show())
        keys = where_is(maps, name)
        page.add(' ' * max(2, 34 - len(name)))
        if keys:
            page.key(keys[0])
        page.add('\n  ' + (record.doc.strip().split('\n')[0] if record.doc else '') + '\n')
    page.show()


@command(interactive=lambda: [read_command('Where is command: ')])
def where_is_command(name):
    """Say which keys run a command."""
    keys = where_is(ed.active_maps(ed.active_window().buffer), name)
    if keys:
        ed.message(f'{name} is on ' + ', '.join(key_description(k) for k in keys[:6]), log=False)
    else:
        ed.message(f'{name} is not on any key; run it with M-x {name}', log=False)


@command
def view_echo_area_messages():
    """Show the buffer of messages that have appeared in the echo area."""
    buffer = ed.get_buffer_create('*Messages*')
    window = ed.display_buffer(buffer)
    ed.set_window_point(window, buffer.size)


@command
def view_lossage():
    """Show the last keys typed."""
    page = Page().add('The last keys typed\n\n', 'help-heading')
    page.add(' '.join(ed.recent_keys) + '\n')
    page.show()


@command(interactive=lambda: [completing_read('Describe face: ', faces.face_names(),
                                              require_match=True, category='face')])
def describe_face(name):
    """Show what a face looks like and how it is defined."""
    page = Page().add(name, 'help-heading').add(' is a face.\n\n')
    page.add('Sample: ').add('The quick brown fox jumps over the lazy dog', name).add('\n\n')
    for attribute, value in sorted(faces.resolve(name).items()):
        page.add(f'{attribute:<10} {value!r}\n')
    spec = faces._specs.get(name, {})
    if spec.get('inherit'):
        page.add(f'\nInherits from {spec["inherit"]}.\n')
    if faces.face_doc(name):
        page.add('\n' + faces.face_doc(name) + '\n')
    page.show()


@command
def help_for_help():
    """List the help commands."""
    page = Page().add('Help\n\n', 'help-heading')
    for keys, binding in sorted(describe([help_map]).items()):
        record = find_command(binding)
        page.add('C-h ').key(keys).add('   ')
        page.add((record.doc.strip().split('\n')[0] if record and record.doc else str(binding))
                 + '\n')
    page.show()


def _button_at(buffer, pos):
    for overlay in buffer.overlays_at(pos, 'button'):
        return overlay
    return None


@command
def push_button():
    """Follow the link at point."""
    b = ed.current_buffer
    button = _button_at(b, b.pt) or (_button_at(b, b.pt - 1) if b.pt else None)
    if button is None:
        raise EditorError('No link here')
    button.props['action']()


@command('p')
def forward_button(n=1):
    """Move to the next link."""
    b = ed.current_buffer
    buttons = sorted((o for o in b.overlays if o.props.get('kind') == 'button'),
                     key=lambda o: o.start)
    if not buttons:
        raise EditorError('No links in this buffer')
    for _ in range(abs(n)):
        if n > 0:
            target = next((o for o in buttons if o.start > b.pt), buttons[0])
        else:
            target = next((o for o in reversed(buttons) if o.end <= b.pt), buttons[-1])
        b.goto_char(target.start)


@command('p')
def backward_button(n=1):
    """Move to the previous link."""
    forward_button(-n)


def _symbol_at_point(buffer):
    line = buffer.line_at()
    start = buffer.text.line_start(line)
    for found in re.finditer(r'[\w.]*\w', buffer.text.line(line)):
        if start + found.start() <= buffer.pt <= start + found.end():
            return found.group()
    return None


_xref_marks = []


@command
def xref_find_definitions():
    """Go to the definition of the name at point.

    Names are looked up in the running editor: in the module the buffer is the
    source of, in the commands, and in ``runrist.user``.  A name that is not
    known there is searched for in the buffer as a ``def`` or ``class``."""
    from . import pyeval, user
    b = ed.current_buffer
    name = _symbol_at_point(b)
    if not name:
        raise EditorError('No name at point')
    origin = (b, b.pt)
    module, _ = pyeval.namespace_for(b)
    target = None
    for namespace in (module.__dict__, user.__dict__):
        obj = namespace
        try:
            for part in name.split('.'):
                obj = obj[part] if isinstance(obj, dict) else getattr(obj, part)
        except (KeyError, AttributeError):
            continue
        if live.source_of(obj) is not None:
            target = obj
            break
    if target is None:
        record = find_command(name.replace('_', '-'))
        if record is not None:
            target = record.fn
    if target is not None:
        _xref_marks.append(origin)
        find_definition(target)
        return
    last = name.split('.')[-1]
    pattern = re.compile(rf'^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+{re.escape(last)}\b', re.M)
    found = pattern.search(b.text.snapshot())
    if found is None:
        raise EditorError(f'No definition found for {name}')
    _xref_marks.append(origin)
    b.push_mark()
    b.goto_char(found.start())
    b.back_to_indentation()


@command
def xref_go_back():
    """Return to where ``xref-find-definitions`` was last used."""
    while _xref_marks:
        buffer, pos = _xref_marks.pop()
        if buffer.live:
            ed.switch_to_buffer(buffer)
            buffer.goto_char(pos)
            return
    raise EditorError('No earlier place to go back to')


@command(interactive=lambda: [read_command('Find command: ', default=_command_at_point())])
def find_function(name):
    """Visit the source of a command."""
    record = find_command(name)
    if record is None:
        raise EditorError(f'{name} is not a command')
    find_definition(record.fn)


for _keys, _name in (
        ('k', 'describe-key'), ('c', 'describe-key-briefly'), ('f', 'describe-function'),
        ('x', 'describe-function'), ('v', 'describe-variable'), ('b', 'describe-bindings'),
        ('m', 'describe-mode'), ('a', 'apropos-command'), ('A', 'ai-help'), ('w', 'where-is-command'),
        ('e', 'view-echo-area-messages'), ('l', 'view-lossage'), ('?', 'help-for-help'),
        ('C-h', 'help-for-help')):
    help_map.bind(_keys, _name)
global_map.bind('C-h', help_map)
global_map.bind('<f1>', help_map)
global_map.bind('M-.', 'xref-find-definitions')
global_map.bind('M-,', 'xref-go-back')

_help = major_modes['help-mode'].keymap
for _keys, _name in (('RET', 'push-button'), ('TAB', 'forward-button'),
                     ('<backtab>', 'backward-button'), ('<mouse-1>', 'push-button')):
    _help.bind(_keys, _name)
