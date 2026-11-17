# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Loading code into the running editor.

Source is executed one top-level statement at a time in the namespace of the
module it belongs to.  What a statement defines is merged into what is there:

* A function that is defined again keeps its identity and gets the new code,
  so every reference to it -- a key binding, a hook, a name another module
  imported -- runs the new definition from then on.
* A class that is defined again is patched in place, so its existing
  instances get the new methods.
* Anything else is simply rebound.

When a file that is already loaded is loaded again, only the statements that
differ from the last load are executed.  An unchanged ``kill_ring = []`` is
left alone and the kill ring survives; edit that line and it is evaluated
afresh.  A definition that only moved is executed again so that tracebacks
and jump-to-definition have its new line.  This module depends on nothing
else in the editor, so it can reload everything else, itself included.
"""

import ast
import linecache
import os
import sys
import traceback
import types
from collections import defaultdict

_MISSING = object()
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# While a class statement is being executed: {class qualname: names of the
# class attributes whose defining statement has not changed}.  Those keep
# their current values.
_keep = {}


class LoadReport:
    """What a load did."""

    def __init__(self, module, path=None):
        self.module = module
        self.path = path
        self.ran = 0                # statements executed
        self.skipped = 0            # unchanged statements left alone
        self.names = []             # names (re)defined
        self.replaced = []          # classes or functions that could not be patched in place
        self.error = None
        self.traceback = None
        self.value = None

    @property
    def ok(self):
        return self.error is None

    def summary(self):
        name = getattr(self.module, '__name__', '?')
        if self.error is not None:
            line = getattr(self.error, 'lineno', None)
            where = f' line {line}' if line else ''
            return f'{name}:{where} {type(self.error).__name__}: {self.error}'
        if not self.ran:
            return f'{name}: no changes'
        shown = ', '.join(self.names[:6]) + (', ...' if len(self.names) > 6 else '')
        text = f'{name}: {self.ran} statement{"s" if self.ran != 1 else ""} loaded'
        if shown:
            text += f' ({shown})'
        if self.replaced:
            text += '; replaced, not patched: ' + ', '.join(self.replaced)
        return text


# -- merging a new definition into an old one ----------------------------------

def _same_definition(old, new):
    return (getattr(old, '__module__', None) == getattr(new, '__module__', _MISSING)
            and getattr(old, '__qualname__', None) == getattr(new, '__qualname__', _MISSING))


def patch_function(old, new, swap=None):
    """Give ``old`` the behaviour of ``new``.  Returns ``old``, or ``new`` if
    the two cannot share a closure.  ``swap`` is ``(new class, old class)``
    when a method is patched, so ``super()`` keeps referring to the class the
    instances actually have."""
    if old is new:
        return old
    old_cells = old.__closure__ or ()
    new_cells = new.__closure__ or ()
    if len(old_cells) != len(new_cells):
        return new
    old.__code__ = new.__code__
    old.__defaults__ = new.__defaults__
    old.__kwdefaults__ = new.__kwdefaults__
    for attr in ('__doc__', '__name__', '__qualname__', '__type_params__'):
        try:
            setattr(old, attr, getattr(new, attr))
        except (AttributeError, TypeError):
            pass
    try:
        old.__annotate__ = new.__annotate__
    except (AttributeError, TypeError):
        try:
            old.__annotations__ = dict(new.__annotations__)
        except Exception:
            pass
    old.__dict__.clear()
    old.__dict__.update(new.__dict__)
    for old_cell, new_cell in zip(old_cells, new_cells):
        try:
            value = new_cell.cell_contents
        except ValueError:
            continue
        if swap is not None and value is swap[0]:
            value = swap[1]
        old_cell.cell_contents = value
    return old


def _retarget(value, new, old):
    """Point the ``__class__`` cell of a method defined in ``new`` at ``old``."""
    function = value
    if isinstance(value, (staticmethod, classmethod)):
        function = value.__func__
    elif isinstance(value, property):
        for part in (value.fget, value.fset, value.fdel):
            if part is not None:
                _retarget(part, new, old)
        return
    if isinstance(function, types.FunctionType):
        for cell in function.__closure__ or ():
            try:
                if cell.cell_contents is new:
                    cell.cell_contents = old
            except ValueError:
                pass


def _patch_member(current, value, swap):
    if isinstance(value, types.FunctionType) and isinstance(current, types.FunctionType):
        return patch_function(current, value, swap)
    if isinstance(value, (staticmethod, classmethod)) and type(current) is type(value):
        a, b = current.__func__, value.__func__
        if isinstance(a, types.FunctionType) and isinstance(b, types.FunctionType) \
                and patch_function(a, b, swap) is a:
            return current
        return value
    if isinstance(value, property) and isinstance(current, property):
        for a, b in ((current.fget, value.fget), (current.fset, value.fset),
                     (current.fdel, value.fdel)):
            if a is None and b is None:
                continue
            if not (isinstance(a, types.FunctionType) and isinstance(b, types.FunctionType)
                    and patch_function(a, b, swap) is a):
                return value
        return current
    if isinstance(value, type) and isinstance(current, type) and _same_definition(current, value):
        return patch_class(current, value)
    return value


def patch_class(old, new):
    """Make ``old`` behave as ``new``.  Returns ``old``, or ``new`` if the two
    differ in a way existing instances could not survive."""
    if old is new:
        return old
    import abc
    if type(old) is not type(new) or type(old) not in (type, abc.ABCMeta):
        return new
    if old.__dict__.get('__slots__') != new.__dict__.get('__slots__'):
        return new
    if old.__bases__ != new.__bases__:
        try:
            old.__bases__ = new.__bases__
        except TypeError:
            return new
    keep = _keep.get(new.__qualname__, ())
    swap = (new, old)
    current_dict = old.__dict__
    for name, value in list(new.__dict__.items()):
        if name in ('__dict__', '__weakref__') or name.startswith('_abc_'):
            continue
        if isinstance(value, (types.MemberDescriptorType, types.GetSetDescriptorType)):
            continue
        _retarget(value, new, old)
        current = current_dict.get(name, _MISSING)
        if current is _MISSING:
            setattr(old, name, value)
        elif name in keep and not callable(value):
            continue
        else:
            try:
                setattr(old, name, _patch_member(current, value, swap))
            except (AttributeError, TypeError):
                pass
    return old


def redefine(old, new):
    """The object a name should be bound to when it held ``old`` and is being
    given ``new``: ``old`` patched to behave as ``new`` where that is possible."""
    if old is new:
        return old
    if isinstance(old, types.FunctionType) and isinstance(new, types.FunctionType):
        return patch_function(old, new) if _same_definition(old, new) else new
    if isinstance(old, type) and isinstance(new, type):
        return patch_class(old, new) if _same_definition(old, new) else new
    return new


class _Namespace:
    """The locals of a module being loaded: every binding is merged."""

    def __init__(self, namespace, report):
        self.namespace = namespace
        self.report = report

    def __getitem__(self, name):
        return self.namespace[name]

    def __contains__(self, name):
        return name in self.namespace

    def __setitem__(self, name, value):
        namespace = self.namespace
        old = namespace.get(name, _MISSING)
        if old is _MISSING or old is value or name.startswith('__'):
            namespace[name] = value
        else:
            merged = redefine(old, value)
            if merged is value and isinstance(value, (type, types.FunctionType)) \
                    and type(old) is type(value) and _same_definition(old, value):
                self.report.replaced.append(name)
            namespace[name] = merged
        if not name.startswith('__') and name not in self.report.names:
            self.report.names.append(name)

    def __delitem__(self, name):
        del self.namespace[name]

    def keys(self):
        return self.namespace.keys()


# -- executing source -----------------------------------------------------------

def _future_flags(tree):
    import __future__
    flags = 0
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == '__future__':
            for alias in stmt.names:
                feature = getattr(__future__, alias.name, None)
                if feature is not None:
                    flags |= feature.compiler_flag
    return flags


def _class_attrs(tree_body, prefix=''):
    """``{class qualname: {attribute: dump of the statement assigning it}}``."""
    out = {}
    for stmt in tree_body:
        if isinstance(stmt, ast.ClassDef):
            qualname = prefix + stmt.name
            attrs = {}
            for inner in stmt.body:
                targets = ()
                if isinstance(inner, ast.Assign):
                    targets = inner.targets
                elif isinstance(inner, ast.AnnAssign):
                    targets = (inner.target,)
                for target in targets:
                    if isinstance(target, ast.Name):
                        attrs[target.id] = ast.dump(inner)
            out[qualname] = attrs
            out.update(_class_attrs(stmt.body, qualname + '.'))
    return out


def _unchanged_attrs(old, new):
    return {qualname: {name for name, dump in attrs.items()
                       if old.get(qualname, {}).get(name) == dump}
            for qualname, attrs in new.items()}


def _run(module, statements, filename, flags, report, want_value=False):
    namespace = module.__dict__
    local = _Namespace(namespace, report)
    last = len(statements) - 1
    for i, stmt in enumerate(statements):
        try:
            if want_value and i == last and isinstance(stmt, ast.Expr):
                code = compile(ast.Expression(stmt.value), filename, 'eval', flags, True)
                report.value = eval(code, namespace, local)
            else:
                code = compile(ast.Module([stmt], []), filename, 'exec', flags, True)
                exec(code, namespace, local)
            report.ran += 1
        except BaseException as error:     # a broken definition must not take the editor down
            if isinstance(error, (SystemExit, GeneratorExit)):
                raise
            report.error = error
            report.traceback = traceback.format_exc()
            if getattr(error, 'lineno', None) is None:
                try:
                    error.lineno = _line_in(error, filename) or stmt.lineno
                except Exception:
                    pass
            return False
    return True


def _line_in(error, filename):
    line = None
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == filename:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


class _State:
    """A loaded file: where it is and the source it was last loaded from."""

    __slots__ = ('module', 'path', 'stamp', 'source')

    def __init__(self, module, path, stamp, source):
        self.module = module
        self.path = path
        self.stamp = stamp
        self.source = source


_states = {}


def _stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _read(path):
    with open(path, encoding='utf-8') as source:
        return source.read()


def track(module):
    """Remember ``module``'s file as it is now, so later edits can be told apart."""
    path = getattr(module, '__file__', None)
    if not path or not path.endswith('.py'):
        return None
    path = os.path.realpath(path)
    if path in _states:
        return _states[path]
    try:
        source = _read(path)
    except (OSError, UnicodeDecodeError):
        return None
    state = _states[path] = _State(module, path, _stamp(path), source)
    return state


def track_package(prefix):
    """Track every loaded module of package ``prefix``."""
    for name, module in list(sys.modules.items()):
        if module is not None and (name == prefix or name.startswith(prefix + '.')):
            track(module)


def tracked(path):
    return _states.get(os.path.realpath(path))


def tracked_paths():
    return list(_states)


def load_source(module, source, filename, *, fresh=False, baseline=None):
    """Load ``source`` into ``module``.

    ``baseline`` is the source the module was last loaded from; statements it
    shares with ``source`` are not executed again.  With ``fresh``, or with no
    baseline, every statement is executed.
    """
    report = LoadReport(module, filename)
    try:
        tree = ast.parse(source, filename)
    except SyntaxError as error:
        report.error = error
        report.traceback = ''.join(traceback.format_exception_only(error))
        return report
    seen = defaultdict(list)
    old_attrs = {}
    if baseline is not None and not fresh:
        try:
            old_tree = ast.parse(baseline, filename)
        except SyntaxError:
            old_tree = None
        if old_tree is not None:
            for stmt in old_tree.body:
                seen[ast.dump(stmt)].append(stmt.lineno)
            old_attrs = _class_attrs(old_tree.body)
    todo = []
    for stmt in tree.body:
        lines = seen.get(ast.dump(stmt)) if seen else None
        if lines:
            was = lines.pop(0)
            if isinstance(stmt, _DEFS) and was != stmt.lineno:
                todo.append(stmt)
            else:
                report.skipped += 1
        else:
            todo.append(stmt)
    linecache.checkcache(filename)
    _keep.clear()
    _keep.update(_unchanged_attrs(old_attrs, _class_attrs(tree.body)))
    try:
        _run(module, todo, filename, _future_flags(tree), report)
    finally:
        _keep.clear()
    return report


def reload_path(path, *, fresh=False):
    """Load the current contents of a tracked file into its module."""
    path = os.path.realpath(path)
    state = _states.get(path)
    if state is None:
        raise KeyError(path)
    report = LoadReport(state.module, path)
    stamp = _stamp(path)
    try:
        source = _read(path)
    except (OSError, UnicodeDecodeError) as error:
        report.error = error
        return report
    report = load_source(state.module, source, path, fresh=fresh, baseline=state.source)
    state.stamp = stamp
    if report.ok:
        state.source = source
    return report


def poll():
    """Reload every tracked file that changed on disk; return the reports."""
    reports = []
    for path, state in list(_states.items()):
        stamp = _stamp(path)
        if stamp is not None and stamp != state.stamp:
            reports.append(reload_path(path))
    return reports


def load_file(path, name=None, *, fresh=False):
    """Load a file as a module, creating the module the first time.

    Returns ``(module, report)``.  The file is tracked afterwards, so saving
    it again loads only what changed.
    """
    path = os.path.realpath(path)
    state = _states.get(path)
    if state is not None:
        return state.module, reload_path(path, fresh=fresh)
    if name is None:
        name = 'runrist_user_' + os.path.splitext(os.path.basename(path))[0]
    module = sys.modules.get(name)
    if module is None or os.path.realpath(getattr(module, '__file__', '') or '') != path:
        module = types.ModuleType(name)
        module.__file__ = path
        sys.modules[name] = module
    stamp = _stamp(path)
    source = _read(path)
    report = load_source(module, source, path, fresh=True)
    _states[path] = _State(module, path, stamp, source if report.ok else '')
    return module, report


def eval_source(module, source, filename='<eval>', *, first_line=1):
    """Execute ``source`` in ``module`` and return a report whose ``value`` is
    that of a final expression.  ``first_line`` is the line of the file the
    text begins on, so definitions made this way know where they live."""
    report = LoadReport(module, filename)
    try:
        tree = ast.parse(source, filename)
    except SyntaxError as error:
        if error.lineno is not None:
            error.lineno += first_line - 1
        report.error = error
        report.traceback = ''.join(traceback.format_exception_only(error))
        return report
    if first_line != 1:
        ast.increment_lineno(tree, first_line - 1)
    state = _states.get(os.path.realpath(filename)) if os.path.isabs(filename) else None
    old_attrs = {}
    if state is not None and state.source:
        try:
            old_attrs = _class_attrs(ast.parse(state.source).body)
        except SyntaxError:
            pass
    linecache.checkcache(filename)
    _keep.clear()
    _keep.update(_unchanged_attrs(old_attrs, _class_attrs(tree.body)))
    try:
        _run(module, tree.body, filename, _future_flags(tree), report, want_value=True)
    finally:
        _keep.clear()
    return report


def module_for_path(path):
    """The loaded module whose source is the file at ``path``, if any."""
    path = os.path.realpath(path)
    state = _states.get(path)
    if state is not None:
        return state.module
    for module in list(sys.modules.values()):
        file = getattr(module, '__file__', None)
        if file and os.path.realpath(file) == path:
            return module
    return None


def toplevel_block(lines, index):
    """``(first, last)`` indexes into ``lines`` of the top-level statement
    containing line ``index``, found by indentation so that it works while the
    rest of the file does not parse."""
    def opens(i):
        line = lines[i]
        return bool(line) and not line[0].isspace() and not line.startswith(('#', ')', ']', '}'))

    def continues(i):
        return lines[i].startswith(('else', 'elif', 'except', 'finally'))

    n = len(lines)
    if n == 0:
        return 0, 0
    first = max(0, min(index, n - 1))
    while first > 0 and not (opens(first) and not continues(first)):
        first -= 1
    while first > 0 and lines[first - 1].startswith('@'):
        first -= 1
    if lines[first].startswith('@'):
        # Decorators above a definition belong to it.
        body = first
        while body < n - 1 and lines[body].startswith('@'):
            body += 1
    else:
        body = first
    last = body + 1
    while last < n and (not opens(last) or continues(last)):
        last += 1
    last -= 1
    while last > first and not lines[last].strip():
        last -= 1
    return first, last


def source_of(obj):
    """``(path, line)`` where ``obj`` is defined, or None."""
    import inspect
    try:
        obj = inspect.unwrap(obj)
        path = inspect.getsourcefile(obj)
        _, line = inspect.findsource(obj)
    except (TypeError, OSError):
        return None
    return (path, line + 1) if path else None
