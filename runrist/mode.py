# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Major and minor modes.

A major mode says what kind of text a buffer holds.  It is a set-up function
run in a fresh buffer, a keymap and a place in a family tree; a mode derived
from another runs its parent's set-up first and inherits its keys::

    @major_mode('python-mode', 'Python', parent='prog-mode', auto=[r'\\.pyw?$'])
    def python_mode(buffer):
        buffer.set('comment_start', '# ')

A minor mode is a switch, in one buffer or everywhere, with an optional
keymap and a mark for the mode line::

    @minor_mode('auto-fill-mode', ' Fill')
    def auto_fill_mode(buffer, on):
        ...

Both define a command of the same name, and a hook named after the mode
(``python_mode_hook``) that runs when it is turned on.
"""

import re

from . import variables
from .command import command, command_name
from .keymap import Keymap

major_modes = {}
minor_modes = {}
auto_mode_alist = []        # [(pattern, mode name)], searched in order
interpreter_mode_alist = [] # [(pattern, mode name)], matched against a #! line


class _Pattern:
    """A regular expression compiled when it is first used.

    There are some hundred file name patterns and most sessions test a handful.
    """

    __slots__ = ('pattern', '_compiled')

    def __init__(self, pattern):
        self.pattern = pattern
        self._compiled = None

    def _get(self):
        if self._compiled is None:
            self._compiled = re.compile(self.pattern)
        return self._compiled

    def search(self, text):
        return self._get().search(text)

    def fullmatch(self, text):
        return self._get().fullmatch(text)

# Buffer-local values that survive a change of major mode.
permanent_locals = set()


class MajorMode:
    def __init__(self, name):
        self.name = name
        self.title = name
        self.parent = None
        self.setup = None
        self.keymap = None
        self.doc = ''

    def __repr__(self):
        return f'<MajorMode {self.name}>'

    def chain(self):
        """This mode's ancestors, oldest first, ending with the mode itself."""
        out = []
        mode, seen = self, set()
        while mode is not None and mode.name not in seen:
            seen.add(mode.name)
            out.append(mode)
            mode = major_modes.get(mode.parent) if mode.parent else None
        return out[::-1]

    def derived_from(self, *names):
        return any(mode.name in names for mode in self.chain())

    @property
    def hook(self):
        return self.name.replace('-', '_') + '_hook'


def add_auto_mode(pattern, mode):
    """Use ``mode`` for files whose name matches the regular expression ``pattern``."""
    auto_mode_alist[:] = [(p, m) for p, m in auto_mode_alist if p.pattern != pattern]
    auto_mode_alist.insert(0, (_Pattern(pattern), mode))


def add_interpreter_mode(pattern, mode):
    interpreter_mode_alist[:] = [(p, m) for p, m in interpreter_mode_alist
                                 if p.pattern != pattern]
    interpreter_mode_alist.insert(0, (_Pattern(pattern), mode))


def major_mode(name, title=None, *, parent=None, auto=(), interpreters=()):
    """Define a major mode whose set-up is the decorated function of one buffer."""

    def register(fn):
        mode = major_modes.get(name)
        if mode is None:
            mode = major_modes[name] = MajorMode(name)
            mode.keymap = Keymap(name + '-map')
        mode.title = title or name.removesuffix('-mode').replace('-', ' ').title()
        mode.parent = parent
        mode.setup = fn
        mode.doc = fn.__doc__ or ''
        parent_mode = major_modes.get(parent) if parent else None
        mode.keymap.parent = parent_mode.keymap if parent_mode else None
        for other in major_modes.values():      # modes derived from this one, defined earlier
            if other.parent == name:
                other.keymap.parent = mode.keymap
        variables.defhook(mode.hook, f'Functions run when {name} is turned on.')
        for pattern in auto:
            add_auto_mode(pattern, name)
        for pattern in interpreters:
            add_interpreter_mode(pattern, name)

        def switch():
            from .editor import ed
            set_major_mode(ed.current_buffer, name)

        switch.__name__ = name.replace('-', '_')
        switch.__qualname__ = switch.__name__
        switch.__doc__ = mode.doc or f'Switch the current buffer to {mode.title} mode.'
        switch.__module__ = fn.__module__
        command(name=name)(switch)
        fn.mode = mode
        return fn

    return register


def set_major_mode(buffer, mode):
    """Put ``buffer`` in a major mode, discarding what the old mode set up."""
    if isinstance(mode, str):
        mode = major_modes[mode]
    from .editor import ed
    kept = {k: v for k, v in buffer.locals.items() if k in permanent_locals}
    buffer.locals.clear()
    buffer.locals.update(kept)
    buffer.major_mode = mode
    buffer.keymap = mode.keymap
    buffer.syntax = None
    buffer.text.clear_metas()
    chain = mode.chain()
    with ed.current(buffer):
        for ancestor in chain:
            if ancestor.setup is not None:
                ancestor.setup(buffer)
        for ancestor in chain:
            ed.run_hooks(ancestor.hook)
        ed.run_hooks('after_change_major_mode_hook')
    buffer.modiff += 1


class MinorMode:
    def __init__(self, name):
        self.name = name
        self.lighter = ''
        self.keymap = None
        self.is_global = False
        self.fn = None
        self.doc = ''

    @property
    def variable(self):
        return self.name.replace('-', '_')

    @property
    def hook(self):
        return self.variable + '_hook'

    def __repr__(self):
        return f'<MinorMode {self.name}>'


def minor_mode(name, lighter='', *, global_=False, keymap=None, default=False):
    """Define a minor mode.  The decorated function is called as
    ``f(buffer, on)`` whenever the mode is switched; ``buffer`` is None for a
    global mode."""

    def register(fn):
        mode = minor_modes.get(name)
        if mode is None:
            mode = minor_modes[name] = MinorMode(name)
        mode.lighter = lighter.strip()
        mode.is_global = global_
        mode.fn = fn
        mode.doc = fn.__doc__ or ''
        if keymap is not None:
            mode.keymap = keymap
        variables.defvar(mode.variable, default, mode.doc.strip().split('\n')[0],
                         local=not global_)
        variables.defhook(mode.hook, f'Functions run when {name} is switched on or off.')

        def toggle(arg=None):
            from .editor import ed
            buffer = None if global_ else ed.current_buffer
            now = bool(variables.get(mode.variable, buffer))
            if arg is None:
                want = not now
            elif isinstance(arg, bool):
                want = arg
            else:
                want = ed.prefix_numeric_value(arg) > 0
            set_minor_mode(name, want, buffer)
            ed.message(f'{mode.name.replace("-", " ").capitalize()} '
                       f'{"enabled" if want else "disabled"}'
                       + ('' if global_ else ' in current buffer'))

        toggle.__name__ = command_name(name).replace('-', '_')
        toggle.__qualname__ = toggle.__name__
        toggle.__doc__ = mode.doc or f'Toggle {name}.'
        toggle.__module__ = fn.__module__
        command('P', name=name)(toggle)
        fn.mode = mode
        return fn

    return register


def minor_mode_enabled(name, buffer=None):
    mode = minor_modes.get(name)
    return bool(mode) and bool(variables.get(mode.variable, buffer))


def set_minor_mode(name, on, buffer=None):
    """Switch a minor mode on or off, in ``buffer`` or, for a global mode, everywhere."""
    from .editor import ed
    mode = minor_modes[name]
    on = bool(on)
    if mode.is_global:
        buffer = None
        variables.set_default(mode.variable, on)
    else:
        buffer = buffer or ed.current_buffer
        variables.set_local(mode.variable, on, buffer)
        buffer.modiff += 1
    if mode.fn is not None:
        if buffer is None:
            mode.fn(None, on)
        else:
            with ed.current(buffer):
                mode.fn(buffer, on)
    ed.run_hooks(mode.hook)


def active_minor_modes(buffer):
    """The minor modes switched on in ``buffer``, most recently defined first."""
    return [mode for mode in reversed(list(minor_modes.values()))
            if variables.get(mode.variable, buffer)]
