# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Keymaps.

A keymap binds keys to commands or to further keymaps, which makes the key a
prefix.  Commands are bound by name, so redefining a command changes what
every key bound to it does.  A keymap may have a parent whose bindings it
inherits, and a default binding that catches every key it does not bind.

Several keymaps are active at once, in order of priority.  The first of them
to bind a key decides whether that key is a command or a prefix; when it is a
prefix, the prefix maps of all the active keymaps are searched together for
what follows.
"""

from .keys import kbd, key_description

keymaps = {}


class Keymap:
    def __init__(self, name='', parent=None, doc=''):
        self.name = name
        self.parent = parent
        self.doc = doc
        self.bindings = {}
        self.default = None
        self.remaps = {}
        if name:
            keymaps[name] = self

    def __repr__(self):
        return f'<Keymap {self.name or hex(id(self))}>'

    def chain(self):
        """This keymap, then its ancestors."""
        seen = set()
        keymap = self
        while keymap is not None and id(keymap) not in seen:
            seen.add(id(keymap))
            yield keymap
            keymap = keymap.parent

    def bind(self, keys, command):
        """Bind a key sequence, creating prefix keymaps on the way to it.

        ``keys`` is in ``kbd`` notation or a tuple of keys.  ``command`` is a
        command name, a command function, a callable or a keymap; None removes
        the binding.
        """
        keys = kbd(keys)
        keymap = self
        for key in keys[:-1]:
            sub = keymap.bindings.get(key)
            if not isinstance(sub, Keymap):
                sub = keymap.bindings[key] = Keymap()
            keymap = sub
        if command is None:
            keymap.bindings.pop(keys[-1], None)
        else:
            keymap.bindings[keys[-1]] = binding_of(command)
        return self

    def unbind(self, keys):
        return self.bind(keys, None)

    def remap(self, command, replacement):
        """While this keymap is active, keys bound to ``command`` run ``replacement``."""
        self.remaps[binding_of(command)] = binding_of(replacement)
        return self

    def get(self, key):
        """What ``key`` is bound to here or in an ancestor."""
        for keymap in self.chain():
            binding = keymap.bindings.get(key)
            if binding is not None:
                return binding
        return None

    def items(self, prefix=(), _seen=None):
        """Every ``(key sequence, command)`` bound in this keymap and its prefix maps."""
        seen = set() if _seen is None else _seen
        if id(self) in seen:
            return
        seen.add(id(self))
        for key, binding in self.bindings.items():
            if isinstance(binding, Keymap):
                yield from binding.items(prefix + (key,), seen)
            else:
                yield prefix + (key,), binding


def binding_of(command):
    """What to store for ``command``: its name if it is a registered command."""
    record = getattr(command, 'command', None)
    if record is not None and hasattr(record, 'name'):
        return record.name
    return command


def flatten(maps):
    out = []
    for keymap in maps:
        if keymap is not None:
            out.extend(keymap.chain())
    return out


def lookup(maps, keys):
    """Look ``keys`` up in ``maps``, which are in order of priority.

    Returns the command bound to the sequence, a list of keymaps if it is a
    prefix, or None if it is not bound.
    """
    current = flatten(maps)
    last = len(keys) - 1
    for i, key in enumerate(keys):
        prefixes = []
        decided = False
        for keymap in current:
            binding = keymap.bindings.get(key)
            if binding is None:
                binding = keymap.default
                if binding is None:
                    continue
            if isinstance(binding, Keymap):
                decided = True
                prefixes.extend(binding.chain())
            elif not decided:
                return binding if i == last else None
        if not decided:
            return None
        current = prefixes
    return current


def remapped(maps, command):
    """``command`` after any remapping the active keymaps ask for."""
    if isinstance(command, str):
        for keymap in flatten(maps):
            replacement = keymap.remaps.get(command)
            if replacement is not None:
                return replacement
    return command


def where_is(maps, command):
    """Key sequences that run ``command`` in ``maps``, shortest first."""
    command = binding_of(command)
    found = []
    for keymap in flatten(maps):
        for keys, binding in keymap.items():
            if binding == command and keys not in found and lookup(maps, keys) == command:
                found.append(keys)
    found.sort(key=lambda keys: (len(keys), sum(map(len, keys)), keys))
    return found


def describe(maps, prefix=()):
    """``{key sequence: command}`` for everything reachable in ``maps`` under
    ``prefix``, with shadowed bindings left out."""
    out = {}
    start = lookup(maps, prefix) if prefix else flatten(maps)
    if not isinstance(start, list):
        return out
    seen = set()
    for keymap in start:
        for keys, binding in keymap.items(_seen=seen):
            full = prefix + keys
            if full not in out and lookup(maps, full) == binding:
                out[full] = binding
    return out


def command_name(binding):
    if isinstance(binding, str):
        return binding
    if isinstance(binding, list) or isinstance(binding, Keymap):
        return 'Prefix Command'
    return getattr(binding, '__name__', repr(binding))


__all__ = ['Keymap', 'keymaps', 'lookup', 'remapped', 'where_is', 'describe', 'flatten',
           'binding_of', 'command_name', 'kbd', 'key_description']
