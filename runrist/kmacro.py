# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Keyboard macros.

``F3`` (or ``C-x (``) starts recording what you type, ``F4`` (or ``C-x )``)
stops, and ``F4`` again (or ``C-x e``) plays it back.  A numeric argument
repeats the macro that many times, and an argument of zero repeats it until
something fails -- a search that finds nothing, the end of the buffer --
which is the usual way to apply a macro to the rest of a file.

A macro is the events themselves, replayed through the same ``read_event``
they were read from, so everything works inside one: prompts, searches,
queries.  While one is being defined ``F3`` inserts a counter that goes up by
one each time.  ``C-x C-k n`` gives the last macro a name and makes it a
command, ``C-x C-k b`` puts it on a key, and ``insert-kbd-macro`` writes it
out as Python for your ``init.py``.
"""

from .command import command, commands
from .editor import ed, global_map
from .errors import EditorError
from .keymap import Keymap
from .keys import Event, kbd
from .minibuffer import read_from_minibuffer, read_key_sequence, read_number, y_or_n_p
from .variables import defvar

defvar('kmacro_ring_max', None, 'How many earlier macros are remembered; None keeps them all.')

kmacro_keymap = Keymap('kmacro-keymap')


class Macro:
    """A recorded sequence of events, with its counter."""

    def __init__(self, events, counter=0, format='%d', name=None):
        self.events = list(events)
        self.counter = counter
        self.format = format
        self.name = name

    def describe(self):
        return format_events(self.events)


ring = []           # macros, most recent first; ring[0] is "the last macro"
named = {}          # macros that were given names
_current = None     # the macro being defined or executed, for its counter


def last_macro():
    if not ring:
        raise EditorError('No keyboard macro has been defined')
    return ring[0]


def format_events(events):
    """Events as text ``kbd`` can read back."""
    words = []
    run = ''

    def flush():
        nonlocal run
        if run in ('RET', 'SPC', 'TAB', 'ESC', 'DEL', 'LFD', 'NUL'):
            words.extend((run[0], run[1:]))     # those letters typed, not the key of that name
        elif run:
            words.append(run)
        run = ''

    for event in events:
        key = event.key
        # Typed characters are written as a word; '-', '<' and '>' stand alone so that
        # no word can be mistaken for a key with modifiers or a function key.
        if len(key) == 1 and key not in '<>-':
            run += key
            continue
        flush()
        words.append(key)
    flush()
    return ' '.join(words)


def events_from(keys):
    """Events for a macro written as text, or given as a list of keys and events."""
    if isinstance(keys, str):
        return [Event(key) for key in kbd(keys)]
    return [item if isinstance(item, Event) else Event(kbd(item)[0]) for item in keys]


def _push(macro):
    ring.insert(0, macro)
    limit = ed.var('kmacro_ring_max')
    if limit is not None:
        del ring[max(1, limit):]


# -- defining ----------------------------------------------------------------------------

@command('P')
def kmacro_start_macro(arg=None):
    """Begin recording a keyboard macro.

    A numeric argument is where the macro's counter starts.  ``C-u`` runs the
    last macro and then adds what you type to it; ``C-u C-u`` adds to it
    without running it first."""
    global _current
    if ed.defining_macro is not None:
        raise EditorError('Already defining a keyboard macro')
    if ed.executing_macro:
        return
    if isinstance(arg, list):
        macro = last_macro()
        if arg[0] == 4:
            ed.execute_events(macro.events)
        ed.defining_macro = list(macro.events)
        _current = macro
        ed.message('Appending to keyboard macro...', log=False)
        return
    _current = Macro([], counter=ed.prefix_numeric_value(arg) if arg is not None else 0)
    ed.defining_macro = []
    ed.message('Defining keyboard macro...', log=False)


@command('P')
def kmacro_end_macro(arg=None):
    """Stop recording a keyboard macro.

    With a numeric argument, then run it that many times in all, counting the
    time you just typed it; zero runs it until something fails."""
    global _current
    if ed.defining_macro is None:
        if ed.executing_macro:
            return
        raise EditorError('Not defining a keyboard macro')
    events = ed.defining_macro[:ed.macro_mark]
    ed.defining_macro = None
    macro = _current or Macro([])
    macro.events = events
    if not events:
        ed.message('Keyboard macro defined (but it is empty)', log=False)
        return
    if not (ring and ring[0] is macro):         # appending leaves it where it was
        _push(macro)
    ed.message('Keyboard macro defined', log=False)
    if arg is not None:
        n = ed.prefix_numeric_value(arg)
        if n == 0:
            call_macro(macro, 0)
        elif n > 1:
            call_macro(macro, n - 1)


def call_macro(macro, count=1):
    """Run ``macro`` ``count`` times; until it fails if ``count`` is zero.
    Returns how many times it ran to completion."""
    global _current
    if ed.defining_macro is not None and ed.defining_macro is macro.events:
        raise EditorError('Cannot run a macro while defining it')
    if not macro.events:
        return 0
    previous = _current
    _current = macro
    done = 0
    try:
        while count == 0 or done < count:
            if not ed.execute_events(macro.events):
                break
            done += 1
    finally:
        _current = previous
    return done


def _repeat_map(key):
    keymap = Keymap()
    keymap.bind(key, 'kmacro-call-macro')
    return keymap


@command('P')
def kmacro_call_macro(arg=None):
    """Run the last keyboard macro.

    A numeric argument is how many times; zero means until something fails.
    Afterwards the last key of the sequence that ran this repeats it."""
    macro = last_macro()
    count = 1 if arg is None else ed.prefix_numeric_value(arg)
    call_macro(macro, max(0, count))
    ed.this_command = 'kmacro-call-macro'
    key = ed.this_keys[-1] if ed.this_keys else None
    if key and not ed.executing_macro and ed.minibuffer_depth == 0:
        ed.set_transient_map(_repeat_map(key))


@command('P')
def kmacro_end_and_call_macro(arg=None):
    """Finish recording a keyboard macro if one is being recorded, then run it."""
    if ed.defining_macro is not None:
        kmacro_end_macro(None)
    kmacro_call_macro(arg)


@command('P')
def kmacro_start_macro_or_insert_counter(arg=None):
    """Begin recording a keyboard macro; while recording or running one, insert its counter."""
    if ed.defining_macro is not None or ed.executing_macro:
        kmacro_insert_counter(arg)
    else:
        kmacro_start_macro(arg)


@command('P')
def kmacro_end_or_call_macro(arg=None):
    """Stop recording a keyboard macro, or, when none is being recorded, run the last one."""
    if ed.defining_macro is not None:
        kmacro_end_macro(arg)
    else:
        kmacro_call_macro(arg)


# -- the counter -------------------------------------------------------------------------

def _counted():
    macro = _current or (ring[0] if ring else None)
    if macro is None:
        raise EditorError('No keyboard macro has been defined')
    return macro


@command('P')
def kmacro_insert_counter(arg=None):
    """Insert the macro counter, then add the numeric argument (one by default) to it.

    With ``C-u``, insert the previous value again without changing the counter."""
    macro = _counted()
    if isinstance(arg, list):
        ed.current_buffer.insert(macro.format % (macro.counter - 1))
        return
    ed.current_buffer.insert(macro.format % macro.counter)
    macro.counter += 1 if arg is None else ed.prefix_numeric_value(arg)


@command(interactive=lambda: [read_number('Macro counter value: ')])
def kmacro_set_counter(value):
    """Set the keyboard macro counter."""
    _counted().counter = int(value)


@command(interactive=lambda: [read_number('Add to macro counter: ')])
def kmacro_add_counter(value):
    """Add to the keyboard macro counter."""
    _counted().counter += int(value)


@command(interactive=lambda: [read_from_minibuffer('Macro counter format: ', '%d')])
def kmacro_set_format(format):
    """Set how the keyboard macro counter is written, as a ``%`` format such as ``%03d``."""
    try:
        format % 0
    except (TypeError, ValueError) as error:
        raise EditorError(f'Bad format: {error}') from None
    _counted().format = format


# -- the ring, names and keys ----------------------------------------------------------------

@command
def kmacro_cycle_ring_next():
    """Make the macro before the last one the last one."""
    if len(ring) < 2:
        raise EditorError('Only one keyboard macro is defined' if ring
                          else 'No keyboard macro has been defined')
    ring.append(ring.pop(0))
    ed.message('Macro: ' + ring[0].describe(), log=False)


@command
def kmacro_cycle_ring_previous():
    """Undo ``kmacro-cycle-ring-next``."""
    if len(ring) < 2:
        raise EditorError('Only one keyboard macro is defined' if ring
                          else 'No keyboard macro has been defined')
    ring.insert(0, ring.pop())
    ed.message('Macro: ' + ring[0].describe(), log=False)


@command
def kmacro_view_macro():
    """Show the last keyboard macro."""
    ed.message('Macro: ' + last_macro().describe(), log=False)


@command
def kmacro_delete_ring_head():
    """Forget the last keyboard macro."""
    last_macro()
    del ring[0]
    ed.message('Keyboard macro deleted', log=False)


def defmacro(name, keys, counter=0, format='%d'):
    """Define command ``name`` to run a keyboard macro.

    ``keys`` is the macro as ``kbd`` text -- what ``insert-kbd-macro`` writes --
    or a list of keys and events."""
    macro = Macro(events_from(keys), counter, format, name)
    named[name] = macro

    def run(arg=None):
        count = 1 if arg is None else ed.prefix_numeric_value(arg)
        call_macro(named[name], max(0, count))

    run.__name__ = name.replace('-', '_')
    run.__qualname__ = run.__name__
    run.__doc__ = f'Keyboard macro: {macro.describe()}'
    command('P', name=name)(run)
    return macro


@command('SName for last kbd macro: ')
def kmacro_name_last_macro(name):
    """Give the last keyboard macro a name, making it a command."""
    name = name.strip().replace('_', '-')
    if not name:
        raise EditorError('No name given')
    if name in commands and name not in named:
        raise EditorError(f'{name} is already a command')
    macro = last_macro()
    defmacro(name, macro.events, macro.counter, macro.format)
    ed.message(f'Keyboard macro named {name}', log=False)


@command(interactive=lambda: [read_key_sequence('Bind last macro to key: ')])
def kmacro_bind_to_key(keys):
    """Put the last keyboard macro on a key sequence."""
    macro = last_macro()
    existing = ed.key_binding(keys)
    if existing is not None and not isinstance(existing, list) and not y_or_n_p(
            f'{" ".join(keys)} runs {existing}.  Bind it anyway?'):
        return
    name = macro.name or f'kmacro-{len(named) + 1}'
    defmacro(name, macro.events, macro.counter, macro.format)
    global_map.bind(keys, name)
    ed.message(f'Keyboard macro bound to {" ".join(keys)}', log=False)


def _code_for(name, macro):
    if any(event.text is not None for event in macro.events):
        items = ', '.join(f'Event({e.key!r}, {e.text!r})' if e.text is not None else repr(e.key)
                          for e in macro.events)
        keys = f'[{items}]'
    else:
        keys = repr(macro.describe())
    extra = ''
    if macro.counter:
        extra += f', counter={macro.counter}'
    if macro.format != '%d':
        extra += f', format={macro.format!r}'
    return f'defmacro({name!r}, {keys}{extra})\n'


def _read_macro_name():
    from .minibuffer import completing_read
    return [completing_read('Insert kbd macro (name): ', sorted(named),
                            default=ring[0].name if ring and ring[0].name else None)]


@command(interactive=_read_macro_name)
def insert_kbd_macro(name):
    """Insert Python that defines a keyboard macro, for saving in ``init.py``.

    With no name, write the last macro under the name ``last-kbd-macro``."""
    if name:
        if name not in named:
            raise EditorError(f'{name} is not a named keyboard macro')
        macro = named[name]
    else:
        name, macro = 'last-kbd-macro', last_macro()
    ed.current_buffer.insert('from runrist.kmacro import defmacro\n' + _code_for(name, macro))


@command
def kmacro_edit_macro():
    """Edit the last keyboard macro as text."""
    macro = last_macro()
    text = read_from_minibuffer('Edit macro: ', macro.describe(), history='kmacro')
    try:
        macro.events = events_from(text)
    except ValueError as error:
        raise EditorError(str(error)) from None
    ed.message('Keyboard macro changed', log=False)


@command('r')
def apply_macro_to_region_lines(start, end):
    """Run the last keyboard macro at the beginning of each line of the region."""
    from .buffer import Marker
    b = ed.current_buffer
    macro = last_macro()
    limit = Marker(b, end)
    here = Marker(b, b.bol(start))
    lines = 0
    try:
        while here.pos < limit.pos:
            b.goto_char(here.pos)
            b.forward_line(1)
            following = b.pt
            at_end = following == here.pos or following >= limit.pos
            b.goto_char(here.pos)
            here.set(following)
            if not call_macro(macro, 1):
                break
            lines += 1
            if at_end:
                break
    finally:
        limit.delete()
        here.delete()
    b.mark_active = False
    ed.message(f'Applied the macro to {lines} line{"s" * (lines != 1)}', log=False)


@command('P')
def kbd_macro_query(arg=None):
    """While a macro runs, ask whether to go on with this repetition.

    ``y`` goes on, ``n`` skips the rest of this repetition, ``q`` or ``RET``
    stops the macro altogether, and ``C-r`` lets you edit before deciding."""
    if not ed.executing_macro:
        return
    frame = ed.macro_stack[-1]
    resume = frame.index
    frame.index = len(frame.events)             # so the answer is read from the keyboard
    while True:
        event = ed.read_key('Proceed with macro? (y, n, q, C-r) ')
        key = event.key
        if key in ('y', 'Y', 'SPC'):
            frame.index = resume
            return
        if key in ('n', 'N', 'DEL'):
            return
        if key in ('q', 'Q', 'RET', 'C-g'):
            ed.macro_abort = True
            return
        if key == 'C-r':
            ed.message('Edit, then C-M-c to return to the macro', log=False)
            ed.recursive_edit()


for _keys, _name in (
        ('<f3>', 'kmacro-start-macro-or-insert-counter'), ('<f4>', 'kmacro-end-or-call-macro'),
        ('C-x (', 'kmacro-start-macro'), ('C-x )', 'kmacro-end-macro'),
        ('C-x e', 'kmacro-end-and-call-macro'), ('C-x q', 'kbd-macro-query')):
    global_map.bind(_keys, _name)

for _keys, _name in (
        ('s', 'kmacro-start-macro'), ('C-s', 'kmacro-start-macro'),
        ('C-k', 'kmacro-end-or-call-macro'), ('r', 'apply-macro-to-region-lines'),
        ('n', 'kmacro-name-last-macro'), ('b', 'kmacro-bind-to-key'),
        ('C-n', 'kmacro-cycle-ring-next'), ('C-p', 'kmacro-cycle-ring-previous'),
        ('C-v', 'kmacro-view-macro'), ('C-d', 'kmacro-delete-ring-head'),
        ('TAB', 'kmacro-insert-counter'), ('C-c', 'kmacro-set-counter'),
        ('C-a', 'kmacro-add-counter'), ('C-f', 'kmacro-set-format'),
        ('e', 'kmacro-edit-macro'), ('C-e', 'kmacro-edit-macro'), ('RET', 'kmacro-edit-macro'),
        ('q', 'kbd-macro-query')):
    kmacro_keymap.bind(_keys, _name)
global_map.bind('C-x C-k', kmacro_keymap)
