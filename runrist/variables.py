# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Named editor variables and hooks.

A variable has a default and may have a value of its own in any buffer.
``v`` reads through the current buffer::

    from runrist.variables import v, defvar
    defvar('tab_width', 8, 'Distance between tab stops.', local=True)
    v.tab_width            # the current buffer's value, else the default
    v.tab_width = 4        # sets this buffer's value, because it is declared local

Names are Python identifiers; ``tab-width`` and ``tab_width`` name the same
variable wherever a name is given as a string.

A hook is a variable holding a list of functions.  Under live reload a
function that is redefined replaces its earlier definition in every hook
instead of being added beside it.
"""

_MISSING = object()


class Variable:
    __slots__ = ('name', 'default', 'doc', 'local', 'is_hook', 'original', 'watchers', 'module')

    def __init__(self, name, default, doc, local, is_hook, module):
        self.name = name
        self.default = default
        self.original = default
        self.doc = doc
        self.local = local
        self.is_hook = is_hook
        self.watchers = []
        self.module = module


variables = {}

# Set by the editor: returns the current buffer, or None before there is one.
current_buffer = lambda: None       # noqa: E731


def norm(name):
    return name.replace('-', '_')


def _caller_module(depth=2):
    import sys
    try:
        return sys._getframe(depth).f_globals.get('__name__')
    except ValueError:
        return None


def defvar(name, default=None, doc='', *, local=False, watch=None):
    """Declare a variable.

    Declaring one again, as happens when its module is reloaded with the
    declaration edited, updates the documentation; the value follows the new
    default only if nobody has changed it from the old one.
    """
    name = norm(name)
    var = variables.get(name)
    if var is None:
        var = variables[name] = Variable(name, default, doc, local, False, _caller_module())
    else:
        if var.default is var.original or var.default == var.original:
            var.default = default
        var.original = default
        var.doc = doc
        var.local = local
    if watch is not None and watch not in var.watchers:
        var.watchers.append(watch)
    return var


def defhook(name, doc=''):
    name = norm(name)
    var = variables.get(name)
    if var is None:
        var = variables[name] = Variable(name, [], doc, False, True, _caller_module())
    else:
        var.doc = doc
        var.is_hook = True
    return var


def _var(name):
    try:
        return variables[norm(name)]
    except KeyError:
        raise AttributeError(f'No such variable: {name}') from None


def get(name, buffer=None):
    """Value of ``name`` in ``buffer`` (the current buffer if None)."""
    name = norm(name)
    if buffer is None:
        buffer = current_buffer()
    if buffer is not None:
        value = buffer.locals.get(name, _MISSING)
        if value is not _MISSING:
            return value
    return _var(name).default


def default_value(name):
    return _var(name).default


def set_default(name, value):
    var = _var(name)
    old, var.default = var.default, value
    for watch in var.watchers:
        watch(var.name, old, value, None)


def set_local(name, value, buffer=None):
    var = _var(name)
    if buffer is None:
        buffer = current_buffer()
    if buffer is None:
        set_default(name, value)
        return
    old = buffer.locals.get(var.name, var.default)
    buffer.locals[var.name] = value
    for watch in var.watchers:
        watch(var.name, old, value, buffer)


def kill_local(name, buffer=None):
    if buffer is None:
        buffer = current_buffer()
    if buffer is not None:
        buffer.locals.pop(norm(name), None)


def setq(name=None, value=None, **pairs):
    """Set variables the way assignment through ``v`` does."""
    if name is not None:
        pairs[norm(name)] = value
    for key, val in pairs.items():
        setattr(v, key, val)


class _Values:
    """Attribute access to variables: ``v.fill_column``."""

    __slots__ = ()

    def __getattr__(self, name):
        return get(name)

    def __setattr__(self, name, value):
        var = _var(name)
        if var.local and current_buffer() is not None:
            set_local(name, value)
        else:
            set_default(name, value)

    def __dir__(self):
        return sorted(variables)


v = _Values()


# -- hooks -----------------------------------------------------------------

def _same_function(a, b):
    if a is b:
        return True
    return (getattr(a, '__qualname__', None) is not None
            and getattr(a, '__module__', None) == getattr(b, '__module__', 0)
            and getattr(a, '__qualname__', None) == getattr(b, '__qualname__', 0)
            and '<lambda>' not in a.__qualname__ and '<locals>' not in a.__qualname__)


def _hook_list(name, buffer):
    name = norm(name)
    if name not in variables:
        defhook(name)
    if buffer is None:
        return variables[name].default
    return buffer.locals.setdefault(name, [])


def add_hook(name, function, *, append=False, local=False):
    """Add ``function`` to hook ``name``, globally or in the current buffer."""
    functions = _hook_list(name, current_buffer() if local else None)
    for i, existing in enumerate(functions):
        if _same_function(existing, function):
            functions[i] = function
            return function
    if append:
        functions.append(function)
    else:
        functions.insert(0, function)
    return function


def remove_hook(name, function, *, local=False):
    functions = _hook_list(name, current_buffer() if local else None)
    functions[:] = [f for f in functions if not _same_function(f, function)]


def hook_functions(name, buffer=None):
    """The functions to run for ``name``: the buffer's own, then the global ones."""
    name = norm(name)
    var = variables.get(name)
    if buffer is None:
        buffer = current_buffer()
    local = buffer.locals.get(name, ()) if buffer is not None else ()
    return [*local, *(var.default if var is not None else ())]


def run_hooks(name, *args):
    for function in hook_functions(name):
        function(*args)


def run_hook_until_success(name, *args):
    for function in hook_functions(name):
        result = function(*args)
        if result:
            return result
    return None
