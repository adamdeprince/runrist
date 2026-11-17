# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Commands.

A command is a function the user can run by name or bind to a key::

    @command('p')
    def forward_char(n=1):
        "Move point N characters forward."

The name is the function's, with dashes: ``forward-char``.  The argument says
how the function gets its arguments when run interactively, in the manner of
Emacs's ``interactive``:

    p   the numeric prefix argument          P   the raw prefix argument
    r   the region, as two positions         d   point           m   mark
    s   a string                             n   a number
    f   an existing file                     F   a file name     D   a directory
    b   an existing buffer                   B   a buffer name
    C   a command name                       v   a variable name
    c   a character                          k   a key sequence
    x   a Python expression, evaluated       i   nothing (None)

Each code is followed by its prompt, and codes are separated by newlines.  A
leading ``*`` refuses to run in a read-only buffer, and ``^`` makes the command
extend the region when its key is shifted.  ``interactive=`` may instead be a
function returning the argument list.

Defining a command again replaces it; keys bound to its name follow.
"""

import types

from . import live


class Command:
    __slots__ = ('name', 'fn', 'spec', 'flags')

    def __init__(self, name, fn, spec, flags):
        self.name = name
        self.fn = fn
        self.spec = spec
        self.flags = flags

    @property
    def doc(self):
        return self.fn.__doc__ or ''

    def __repr__(self):
        return f'<Command {self.name}>'


commands = {}
aliases = {}


def command_name(function_name):
    return function_name.strip('_').replace('_', '-')


def command(spec=None, *, name=None, interactive=None, **flags):
    """Register a function as a command.  See the module documentation."""

    def register(fn):
        cname = name or command_name(fn.__name__)
        how = interactive if interactive is not None else (None if callable(spec) else spec)
        local = dict(flags)
        if isinstance(how, str):
            while how[:1] in ('*', '^', '@'):
                local[{'*': 'writes', '^': 'shift', '@': 'at_click'}[how[0]]] = True
                how = how[1:]
        existing = commands.get(cname)
        if existing is not None and isinstance(existing.fn, types.FunctionType) \
                and isinstance(fn, types.FunctionType):
            fn = live.redefine(existing.fn, fn)
        record = commands[cname] = Command(cname, fn, how, local)
        fn.command = record
        return fn

    if callable(spec):
        return register(spec)
    return register


def defalias(name, target):
    """Make ``name`` another name for command ``target``."""
    aliases[name] = getattr(getattr(target, 'command', None), 'name', target)


def find_command(name):
    """The command record for a name, function or record; None if there is none."""
    if isinstance(name, Command):
        return name
    if isinstance(name, str):
        seen = set()
        while name in aliases and name not in seen:
            seen.add(name)
            name = aliases[name]
        return commands.get(name)
    return getattr(name, 'command', None)
