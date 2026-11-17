# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The editor: its buffers and windows, and the command loop.

There is one ``Editor``, ``ed``.  Its command loop is the plain synchronous
one Emacs has: read a key sequence, find what it is bound to, run that, show
the result.  A command that needs to ask something reads from the minibuffer
by running the same loop again, one level deeper, and gets the answer as an
ordinary return value.

All input comes through ``read_event``.  That is the single place where
keyboard macros are recorded and played back, which is why a macro can drive
anything a person can: the minibuffer, an incremental search, a query.  It is
also the only place the editor waits, so it is where redisplay happens and
where timers and posted callbacks get their turn.
"""

import time
import traceback
from collections import deque
from contextlib import contextmanager

from . import variables
from .buffer import Buffer, inhibit_read_only
from .command import Command, find_command
from .display import Display
from .errors import (AbortRecursiveEdit, BufferReadOnly, EditorError, ExitEditor,
                     ExitRecursiveEdit, Quit)
from .keymap import Keymap, command_name, lookup, remapped
from .keys import add_modifier, fallbacks, key_description, self_insert_text, split_key
from .mode import active_minor_modes, major_modes, set_major_mode
from .variables import defhook, defvar
from .window import Frame

global_map = Keymap('global-map')

defvar('echo_keystrokes', 1.0,
       'Seconds of pause after a prefix key before it is shown in the echo area.')
defvar('transient_mark_mode', True,
       'Whether the region is highlighted while active and goes inactive after an edit.')
defvar('shift_select_mode', True,
       'Whether holding shift while moving selects text.')
defvar('select_enable_clipboard', True,
       'Whether killing text also copies it to the system clipboard.')
defvar('kill_ring_max', None, 'How many kills are kept; None keeps them all.')
defvar('enable_recursive_minibuffers', False,
       'Whether a command may use the minibuffer while it is already in use.')
defvar('ring_bell', False, 'Whether errors ring the terminal bell.')
defvar('which_key', True,
       'Whether pausing after a prefix key lists the keys that can follow it.')
defvar('debug_on_error', False,
       'Whether an error in a command pops up its traceback instead of only logging it.')
defhook('pre_command_hook', 'Functions run before each command.')
defhook('post_command_hook', 'Functions run after each command.')
defhook('kill_buffer_hook', 'Functions run, in the buffer, before it is killed.')
defhook('after_change_major_mode_hook', 'Functions run after a major mode is set up.')
defhook('focus_in_hook', 'Functions run when the terminal window gains focus.')
defhook('focus_out_hook', 'Functions run when the terminal window loses focus.')
defhook('kill_emacs_hook', 'Functions run before the editor exits.')
defhook('pre_redisplay_hook', 'Functions run before the screen is brought up to date.')

# Runs of these commands are undone as one, twenty at a time, as in Emacs.
_AMALGAMATING = {'self-insert-command', 'delete-char', 'delete-backward-char'}


class Timer:
    def __init__(self, function, delay, repeat, idle):
        self.function = function
        self.delay = delay
        self.repeat = repeat
        self.idle = idle
        self.handle = None
        self.fired = False
        self.active = True

    def cancel(self):
        self.active = False
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None


class _MacroFrame:
    __slots__ = ('events', 'index')

    def __init__(self, events):
        self.events = events
        self.index = 0


class Editor:
    def __init__(self):
        self.buffers = []
        self.current_buffer = None
        self.frame = None
        self.selected_window = None
        self.tty = None
        self.loop = None
        self.display = Display(self)
        self.started = False

        self.this_command = None
        self.last_command = None
        self.prefix_arg = None
        self.current_prefix_arg = None
        self.last_prefix_arg = None
        self.this_keys = ()
        self.last_event = None
        self.last_input_event = None
        self.shift_translated = False
        self.deactivate_mark = False
        self.keep_mark_active = False
        self.unread = deque()
        self.transient_maps = []
        self.overriding_map = None
        self.recursion_depth = 0
        self._amalgamated = 0

        self.echo = None
        self.cursor_in_echo = False
        self.minibuffer_message = None
        self.minibuffer_depth = 0
        self.minibuffer_selected_window = None
        self.completion_rows = None
        self.inhibit_redisplay = False

        self.defining_macro = None
        self.macro_mark = 0
        self.macro_stack = []
        self.macro_abort = False

        self.kill_ring = []
        self.kill_index = 0
        self.yanked = None
        # What ``view-lossage`` shows.  A ring by nature: it is the *last* keys typed.
        self.recent_keys = deque(maxlen=300)

        self.timers = []
        self._posted = deque()
        self._idle_since = time.monotonic()
        self._redisplay_errors = 0

    @property
    def highlighters(self):
        """The functions that add highlights to lines as they are drawn."""
        from . import display
        return display.highlighters

    # -- start-up ----------------------------------------------------------------

    def setup(self, tty):
        """Attach a terminal and make the first buffers and the frame."""
        self.tty = tty
        self.loop = tty.loop
        if not self.buffers:
            scratch = self.new_buffer('*scratch*')
            messages = self.new_buffer('*Messages*')
            messages.undo_enabled = False
            messages.read_only = True
        else:
            scratch = self.buffers[0]
        minibuffer = Buffer(' *Minibuf-0*')
        minibuffer.minibuffer = True
        minibuffer.locals['display_line_numbers'] = False
        self.frame = Frame(scratch, minibuffer, tty.width, tty.height)
        self.selected_window = self.frame.root
        self.current_buffer = scratch
        self.started = True

    def var(self, name):
        """Value of a variable in the current buffer."""
        return variables.get(name, self.current_buffer)

    # -- buffers -------------------------------------------------------------------

    def get_buffer(self, name):
        if isinstance(name, Buffer):
            return name if name.live else None
        for buffer in self.buffers:
            if buffer.name == name:
                return buffer
        return None

    def generate_new_buffer_name(self, name, ignore=None):
        if self.get_buffer(name) in (None, ignore):
            return name
        n = 2
        while self.get_buffer(f'{name}<{n}>') not in (None, ignore):
            n += 1
        return f'{name}<{n}>'

    def new_buffer(self, name, mode='fundamental-mode'):
        """Make a buffer with a name not yet in use."""
        buffer = Buffer(self.generate_new_buffer_name(name))
        if buffer.temporary:
            buffer.undo_enabled = False
        if self.current_buffer is not None:
            buffer.directory = self.current_buffer.directory
        if buffer.directory is None:
            import os
            buffer.directory = os.getcwd()
        self.buffers.append(buffer)
        if self.current_buffer is None:
            self.current_buffer = buffer
        if mode in major_modes:
            set_major_mode(buffer, mode)
        return buffer

    def get_buffer_create(self, name, mode='fundamental-mode'):
        return self.get_buffer(name) or self.new_buffer(name, mode)

    def record_buffer(self, buffer):
        """Note that ``buffer`` was just selected: it becomes the first in the list."""
        if buffer in self.buffers and self.buffers[0] is not buffer:
            self.buffers.remove(buffer)
            self.buffers.insert(0, buffer)

    def other_buffer(self, buffer=None, visible_ok=False):
        """The most recently selected buffer other than ``buffer`` that the
        user would want to see."""
        shown = {w.buffer for w in self.frame.windows()} if self.frame else set()
        fallback = None
        for candidate in self.buffers:
            if candidate is buffer or candidate.temporary:
                continue
            if candidate in shown and not visible_ok:
                fallback = fallback or candidate
                continue
            return candidate
        return fallback or self.get_buffer_create('*scratch*')

    def kill_buffer(self, buffer):
        """Remove a buffer; windows showing it show another."""
        buffer = self.get_buffer(buffer)
        if buffer is None:
            return False
        with self.current(buffer):
            self.run_hooks('kill_buffer_hook')
        replacement = self.other_buffer(buffer)
        if replacement is buffer:
            replacement = self.new_buffer('*scratch*')
        for window in self.frame.windows():
            if window.buffer is buffer:
                target = next((b for b in reversed(window.previous)
                               if b.live and b is not buffer), replacement)
                if window is self.selected_window:
                    window.set_buffer(target, target.pt)
                    self.current_buffer = target
                else:
                    window.set_buffer(target, target.pt)
            if buffer in window.previous:
                window.previous.remove(buffer)
        self.buffers.remove(buffer)
        buffer.live = False
        if self.current_buffer is buffer:
            self.current_buffer = self.selected_window.buffer
        return True

    def set_buffer(self, buffer):
        """Make ``buffer`` current for editing, without showing it."""
        self.current_buffer = buffer

    @contextmanager
    def current(self, buffer):
        """Make ``buffer`` current for the duration."""
        old = self.current_buffer
        self.current_buffer = buffer
        try:
            yield buffer
        finally:
            self.current_buffer = old if old is not None and old.live else (
                self.selected_window.buffer if self.selected_window else buffer)

    # -- windows -------------------------------------------------------------------

    def window_point(self, window):
        """Point of ``window``: the buffer's own in the selected window."""
        return window.buffer.pt if window is self.selected_window else window.pointm.pos

    def set_window_point(self, window, pos):
        if window is self.selected_window:
            window.buffer.goto_char(pos)
        else:
            window.pointm.set(pos)

    def active_window(self):
        """The window commands act on: the one that was selected before the
        minibuffer, when the minibuffer is."""
        w = self.selected_window
        if w is not None and w.is_minibuffer and self.minibuffer_selected_window is not None:
            return self.minibuffer_selected_window
        return w

    def select_window(self, window, record=True):
        old = self.selected_window
        if old is not window and old is not None:
            old.pointm.set(old.buffer.pt)
            window.buffer.pt = window.buffer.clamp(window.pointm.pos)
        self.selected_window = window
        self.current_buffer = window.buffer
        if record and not window.is_minibuffer:
            self.record_buffer(window.buffer)

    def switch_to_buffer(self, buffer, window=None):
        """Show ``buffer`` in ``window`` (the selected one if None) and, if that
        is the selected window, make it current."""
        buffer = self.get_buffer(buffer) or self.get_buffer_create(buffer)
        window = window or self.selected_window
        if window.is_minibuffer:
            window = self.active_window()
            if window.is_minibuffer:
                window = self.frame.windows()[0]
        if window is self.selected_window:
            window.set_buffer(buffer, buffer.pt)
            self.current_buffer = buffer
        else:
            window.set_buffer(buffer, buffer.pt)
        self.record_buffer(buffer)
        return buffer

    def window_for(self, buffer):
        """A window showing ``buffer``, or None."""
        for window in self.frame.windows():
            if window.buffer is buffer:
                return window
        return None

    def display_buffer(self, buffer, select=False):
        """Show ``buffer`` in some window other than the selected one, splitting
        if there is no other.  Returns the window."""
        buffer = self.get_buffer(buffer) or self.get_buffer_create(buffer)
        here = self.active_window()
        window = self.window_for(buffer)
        if window is None or (window is here and not select):
            others = [w for w in self.frame.windows() if w is not here and not w.dedicated]
            if others:
                window = max(others, key=lambda w: w.w * w.h)
            else:
                try:
                    window = self.frame.split(here, horizontal=here.w >= 160)
                except EditorError:
                    window = here
            if window is self.selected_window:
                self.switch_to_buffer(buffer)
            else:
                window.set_buffer(buffer, buffer.pt)
        if select and window is not self.selected_window and not self.selected_window.is_minibuffer:
            self.select_window(window)
        return window

    # -- telling the user --------------------------------------------------------------

    def message(self, text=None, log=True):
        """Show ``text`` in the echo area; None clears it."""
        if text is None or text == '':
            self.echo = None
            return None
        self.echo = text if isinstance(text, list) else str(text)
        self.cursor_in_echo = False
        if log:
            plain = ''.join(part for part, _ in text) if isinstance(text, list) else str(text)
            self._log(plain)
        return text

    def _log(self, text):
        messages = self.get_buffer('*Messages*')
        if messages is None:
            return
        at_end = [w for w in (self.frame.windows() if self.frame else ())
                  if w.buffer is messages and self.window_point(w) == messages.size]
        point_at_end = messages.pt == messages.size
        with inhibit_read_only():
            messages.insert_at(messages.size, text + '\n')
        messages.set_modified(False)
        if point_at_end:
            messages.pt = messages.size
        for window in at_end:
            self.set_window_point(window, messages.size)

    def ding(self):
        """Signal that something could not be done.  Ends a keyboard macro."""
        if self.macro_stack:
            self.macro_abort = True
        if self.defining_macro is not None:
            self.defining_macro = None
            self._log('Keyboard macro definition ended by an error')
        if self.var('ring_bell') and self.tty is not None:
            self.tty.bell()

    def report_error(self, error, where=''):
        """Record an unexpected error and say so, without stopping the editor."""
        text = ''.join(traceback.format_exception(error))
        summary = f'{type(error).__name__}: {error}'
        self._log(text.rstrip('\n'))
        self.message((f'{where}: ' if where else '') + summary, log=False)
        if self.macro_stack:
            self.macro_abort = True
        return summary

    def run_hooks(self, name, *args):
        """Run a hook; a function that fails is reported and the rest still run."""
        for function in variables.hook_functions(name, self.current_buffer):
            try:
                function(*args)
            except (KeyboardInterrupt, ExitRecursiveEdit, AbortRecursiveEdit, ExitEditor):
                raise
            except Exception as error:
                self.report_error(error, f'{variables.norm(name)}')

    # -- timers and things posted from elsewhere -------------------------------------------

    def post(self, function):
        """Run ``function`` in the command loop as soon as it is idle.

        This is how code running in the asyncio loop -- a subprocess reader, a
        network callback -- hands its result to the editor.
        """
        self._posted.append(function)
        if self.tty is not None:
            self.tty.wake()

    def run_at_time(self, delay, function, repeat=None):
        """Call ``function`` after ``delay`` seconds, then every ``repeat`` seconds."""
        timer = Timer(function, delay, repeat, False)

        def fire():
            if not timer.active:
                return
            self.post(timer.function)
            if timer.repeat:
                timer.handle = self.loop.call_later(timer.repeat, fire)
            else:
                timer.active = False

        timer.handle = self.loop.call_later(max(0.0, delay), fire)
        self.timers.append(timer)
        self.timers = [t for t in self.timers if t.active]
        return timer

    def run_with_idle_timer(self, seconds, function, repeat=True):
        """Call ``function`` each time the editor has been idle for ``seconds``."""
        timer = Timer(function, seconds, repeat, True)
        self.timers.append(timer)
        return timer

    def cancel_timer(self, timer):
        timer.cancel()
        self.timers = [t for t in self.timers if t.active]

    def _run_posted(self):
        ran = False
        while self._posted:
            function = self._posted.popleft()
            ran = True
            try:
                function()
            except (KeyboardInterrupt, ExitRecursiveEdit, AbortRecursiveEdit, ExitEditor):
                raise
            except Exception as error:
                self.report_error(error, 'timer')
        return ran

    def _idle_wait(self):
        """Seconds until the next idle timer is due, or None."""
        idle = time.monotonic() - self._idle_since
        due = [t.delay - idle for t in self.timers if t.active and t.idle and not t.fired]
        return max(0.0, min(due)) if due else None

    def _run_idle(self):
        idle = time.monotonic() - self._idle_since
        ran = False
        for timer in list(self.timers):
            if timer.active and timer.idle and not timer.fired and idle >= timer.delay:
                timer.fired = True
                if not timer.repeat:
                    timer.active = False
                ran = True
                try:
                    timer.function()
                except (KeyboardInterrupt, ExitRecursiveEdit, AbortRecursiveEdit, ExitEditor):
                    raise
                except Exception as error:
                    self.report_error(error, 'idle timer')
        return ran

    # -- input -----------------------------------------------------------------------

    def input_pending(self):
        if self.unread:
            return True
        frame = self.macro_stack[-1] if self.macro_stack else None
        if frame is not None and frame.index < len(frame.events):
            return True
        return self.tty.input_pending()

    def redisplay(self, force=False):
        if self.inhibit_redisplay or self.frame is None:
            return
        if force:
            self.display.invalidate()
        try:
            self.run_hooks('pre_redisplay_hook')
            self.display.redisplay()
            self._redisplay_errors = 0
        except (KeyboardInterrupt, ExitRecursiveEdit, AbortRecursiveEdit, ExitEditor):
            raise
        except Exception as error:
            self._redisplay_errors += 1
            self.report_error(error, 'redisplay')
            if self._redisplay_errors < 3:
                self.display.invalidate()

    def read_event(self, timeout=None, record=True):
        """The next input event, or None if ``timeout`` seconds pass first.

        Events pushed back with ``unread`` come first, then those of a keyboard
        macro being run, then the terminal.  Before waiting on the terminal the
        screen is brought up to date.
        """
        if self.unread:
            event = self.unread.popleft()
            self.last_input_event = event
            return event
        frame = self.macro_stack[-1] if self.macro_stack else None
        if frame is not None and frame.index < len(frame.events):
            event = frame.events[frame.index]
            frame.index += 1
            self.last_input_event = event
            return event
        tty = self.tty
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._run_posted()
            if not tty.input_pending():
                self.redisplay()
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            idle = self._idle_wait()
            if idle is not None and (wait is None or idle < wait):
                wait = idle
            event = tty.read(wait)
            if event is None:
                if self._run_idle() or self._posted:
                    continue
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                continue
            self._idle_since = time.monotonic()
            for timer in self.timers:
                timer.fired = False
            key = event.key
            if key == '<resize>':
                continue
            if key == '<protocol>':
                self._protocol_event(event.data)
                continue
            if key in ('<focus-in>', '<focus-out>'):
                self.run_hooks('focus_in_hook' if key == '<focus-in>' else 'focus_out_hook')
                continue
            if record and self.defining_macro is not None and event.recordable:
                self.defining_macro.append(event)
            self.recent_keys.append(key)
            self.last_input_event = event
            return event

    protocol_handlers = {}

    def _protocol_event(self, report):
        handler = self.protocol_handlers.get(getattr(report, 'protocol', None))
        if handler is not None:
            try:
                handler(report)
            except Exception as error:
                self.report_error(error, 'protocol event')

    def read_key(self, prompt=None):
        """Read one key, showing ``prompt`` in the echo area.  Returns the event."""
        saved = self.echo, self.cursor_in_echo
        if prompt is not None:
            self.echo = prompt
            self.cursor_in_echo = True
        try:
            while True:
                event = self.read_event()
                if event.key == 'ESC':
                    following = self.read_event()
                    following = type(following)(add_modifier(following.key, 'M'),
                                                following.text)
                    return following
                if event.key != '<mouse-movement>':
                    return event
        finally:
            self.cursor_in_echo = False
            if prompt is not None:
                self.echo = None if saved[0] == prompt else saved[0]

    # -- finding what a key does ---------------------------------------------------------

    def active_maps(self, buffer=None):
        """The keymaps that apply now, most specific first."""
        maps = [entry[0] for entry in reversed(self.transient_maps)]
        if self.overriding_map is not None:
            maps.append(self.overriding_map)
            maps.append(global_map)
            return maps
        buffer = buffer or self.current_buffer
        maps.extend(mode.keymap for mode in active_minor_modes(buffer) if mode.keymap)
        if buffer.keymap is not None:
            maps.append(buffer.keymap)
        maps.append(global_map)
        return maps

    def key_binding(self, keys, maps=None):
        """What a key sequence runs now, after remapping; None if nothing."""
        from .keys import kbd
        maps = maps or self.active_maps()
        binding = lookup(maps, kbd(keys))
        if binding is None or isinstance(binding, list):
            return binding
        return remapped(maps, binding)

    def _describe_prefix(self, keys, meta):
        parts = []
        arg = self.prefix_arg
        if arg is not None:
            if isinstance(arg, list):
                n, text = arg[0], 'C-u'
                while n > 4:
                    text += ' C-u'
                    n //= 4
                parts.append(text)
            else:
                parts.append('C-u ' + str(arg))
        parts.extend(keys)
        if meta:
            parts.append('ESC')
        return ' '.join(parts)

    def _prefix_help(self, keys, meta):
        """What to show once the user pauses after a prefix key: the keys typed
        and, if ``which_key`` is set, what may follow them."""
        typed = self._describe_prefix(keys, meta) + '-'
        if not keys or meta or not self.var('which_key') or self.minibuffer_depth:
            return typed
        from .keymap import describe
        choices = {}
        for sequence, binding in describe(self.active_maps(), prefix=tuple(keys)).items():
            following = sequence[len(keys)]
            if len(sequence) > len(keys) + 1:
                choices.setdefault(following, '+prefix')
            else:
                choices[following] = binding if isinstance(binding, str) else getattr(
                    binding, '__name__', '...')
        if not choices:
            return typed
        items = [f'{key} {name}' for key, name in sorted(
            choices.items(), key=lambda item: (len(item[0]), item[0].lower(), item[0]))]
        width = max(map(len, items)) + 3
        columns = max(1, self.frame.width // width)
        rows = [''.join(item.ljust(width) for item in items[i:i + columns]).rstrip()
                for i in range(0, len(items), columns)]
        return '\n'.join([*rows, typed])

    def read_key_sequence(self):
        """Read keys until they name a command or nothing.

        Returns ``(keys, binding)``; ``binding`` is None for an unbound
        sequence.  ``ESC`` followed by a key is that key with Meta, and a key
        with no binding of its own is tried under its other names.
        """
        keys = ()
        meta = False
        echoed = False
        self.shift_translated = False
        while True:
            pending = bool(keys) or meta or self.prefix_arg is not None
            delay = self.var('echo_keystrokes')
            if pending and not echoed and delay and not self.macro_stack:
                event = self.read_event(delay)
                if event is None:
                    echoed = True
                    self.echo = self._prefix_help(keys, meta)
                    self.cursor_in_echo = self.minibuffer_depth == 0
                    continue
            else:
                event = self.read_event()
            if not pending and self.minibuffer_depth == 0:
                self.echo = None
            self.minibuffer_message = None
            key = event.key
            if meta:
                key = add_modifier(key, 'M')
                meta = False
            maps = self.active_maps()
            sequence = keys + (key,)
            binding = lookup(maps, sequence)
            if binding is None:
                for other in fallbacks(key):
                    binding = lookup(maps, keys + (other,))
                    if binding is not None:
                        if 'S' in split_key(key)[0] and 'S' not in split_key(other)[0]:
                            self.shift_translated = True
                        sequence = keys + (other,)
                        break
            if binding is None and key == 'ESC':
                meta = True
                if echoed:
                    self.echo = self._describe_prefix(keys, meta) + '-'
                continue
            if binding is None and not keys:
                if event.is_mouse:
                    continue                    # a mouse event nothing is bound to
                if self_insert_text(key) is not None:
                    binding = 'self-insert-command'     # an ordinary character inserts itself
            keys = sequence
            self.last_event = event
            if isinstance(binding, list):
                if echoed:
                    self.echo = self._prefix_help(keys, False)
                continue
            if echoed:
                self.echo = None
                self.cursor_in_echo = False
            self.this_keys = keys
            return keys, (None if binding is None else remapped(maps, binding))

    def set_transient_map(self, keymap, keep=False, on_exit=None):
        """Give ``keymap`` priority for the next key sequence.

        With ``keep`` true it stays for as long as the keys typed are bound in
        it; ``keep`` may also be a function called before each command that
        says whether to stay.  Returns a function that removes it.
        """
        entry = (keymap, keep, on_exit)
        self.transient_maps.append(entry)

        def remove():
            if entry in self.transient_maps:
                self.transient_maps.remove(entry)
                if on_exit is not None:
                    on_exit()

        return remove

    def _expire_transient_maps(self, keys):
        for entry in list(self.transient_maps):
            keymap, keep, on_exit = entry
            if keep is True:
                stay = lookup([keymap], keys) is not None
            elif callable(keep):
                stay = bool(keep())
            else:
                stay = False
            if not stay and entry in self.transient_maps:
                self.transient_maps.remove(entry)
                if on_exit is not None:
                    on_exit()

    # -- running commands ----------------------------------------------------------------

    def prefix_numeric_value(self, raw):
        if raw is None:
            return 1
        if raw == '-':
            return -1
        if isinstance(raw, list):
            return raw[0]
        return raw

    def interactive_args(self, record):
        spec = record.spec
        if spec is None:
            return []
        if callable(spec):
            return list(spec())
        from . import minibuffer
        return minibuffer.read_interactive_args(self, spec)

    def handle_shift_selection(self):
        buffer = self.current_buffer
        if not self.var('shift_select_mode'):
            return
        if self.shift_translated:
            if not (buffer.mark_active and buffer.locals.get('_shift_region')):
                buffer.push_mark(activate=True)
                buffer.locals['_shift_region'] = True
        elif buffer.locals.get('_shift_region'):
            buffer.mark_active = False
            buffer.locals['_shift_region'] = False

    def call_interactively(self, command):
        """Run a command as if from the keyboard, reading its arguments."""
        record = find_command(command)
        if record is None:
            if callable(command):
                return command()
            raise EditorError(f'{command} is not a command')
        if record.flags.get('writes') and self.current_buffer.read_only:
            raise BufferReadOnly(self.current_buffer.name)
        if record.flags.get('shift'):
            self.handle_shift_selection()
        return record.fn(*self.interactive_args(record))

    def execute(self, binding, keys=()):
        """Run what a key sequence is bound to, as one command."""
        record = find_command(binding)
        if record is None:
            if not callable(binding):
                self.message(f'{binding} is not a command')
                self.ding()
                return
            record = Command(getattr(binding, '__name__', 'anonymous'), binding, None, {})
        self.this_command = record.name
        self.current_prefix_arg = self.prefix_arg
        self.prefix_arg = None
        self.deactivate_mark = False
        self.keep_mark_active = False
        buffer = self.current_buffer
        version = buffer.text.version
        self._expire_transient_maps(keys)
        # Each command's changes are undone together.  A run of typed characters
        # is undone twenty at a time, so it does not get a boundary of its own.
        amalgamate = (record.name in _AMALGAMATING and record.name == self.last_command
                      and self._amalgamated < 19)
        self._amalgamated = self._amalgamated + 1 if amalgamate else 0
        for other in (*self.buffers, self.frame.minibuffer_window.buffer):
            if other._open is not None and not (amalgamate and other is buffer):
                other.undo_boundary()
        try:
            self.run_hooks('pre_command_hook')
            self.call_interactively(record)
        except (ExitRecursiveEdit, AbortRecursiveEdit, ExitEditor):
            self._after_command(buffer, version)
            raise
        except KeyboardInterrupt:
            self.quit()
        except EditorError as error:
            self.message(str(error), log=False)
            self.ding()
        except Exception as error:
            self.report_error(error, record.name)
            self.ding()
            if self.var('debug_on_error'):
                self._show_backtrace(error)
        self._after_command(buffer, version)

    def _show_backtrace(self, error):
        buffer = self.get_buffer_create('*Backtrace*')
        with inhibit_read_only():
            buffer.erase()
            buffer.insert(''.join(traceback.format_exception(error)))
        buffer.read_only = True
        buffer.set_modified(False)
        buffer.goto_char(0)
        self.display_buffer(buffer)

    def _after_command(self, buffer, version):
        try:
            self.run_hooks('post_command_hook')
        except KeyboardInterrupt:
            pass
        current = self.current_buffer = self.selected_window.buffer
        if current.mark_active and self.var('transient_mark_mode') and not self.keep_mark_active:
            if self.deactivate_mark or (current is buffer and current.text.version != version):
                current.mark_active = False
        if self.prefix_arg is None:
            self.last_command = self.this_command
            self.last_prefix_arg = self.current_prefix_arg

    def quit(self):
        """What ``C-g`` does to whatever was going on."""
        buffer = self.current_buffer
        if buffer is not None:
            buffer.mark_active = False
        self.prefix_arg = None
        self.transient_maps.clear()
        if self.macro_stack:
            self.macro_abort = True
        if self.defining_macro is not None:
            self.defining_macro = None
        self.message('Quit', log=False)
        if self.var('ring_bell') and self.tty is not None:
            self.tty.bell()

    def command_loop_once(self):
        self.current_buffer = self.selected_window.buffer
        if self.defining_macro is not None:
            self.macro_mark = len(self.defining_macro)
        try:
            keys, binding = self.read_key_sequence()
        except KeyboardInterrupt:
            self.quit()
            return
        if binding is None:
            self.prefix_arg = None
            self.message(f'{key_description(keys)} is undefined', log=False)
            self.ding()
            return
        self.execute(binding, keys)

    def command_loop(self):
        while True:
            self.command_loop_once()

    def recursive_edit(self):
        """Run the command loop until ``exit-recursive-edit``.

        If the edit is aborted instead, this raises ``Quit``, which carries on
        up through whatever asked for the edit.
        """
        saved = (self.this_command, self.last_command, self.current_prefix_arg, self.this_keys)
        self.recursion_depth += 1
        try:
            self.command_loop()
        except ExitRecursiveEdit:
            return
        except AbortRecursiveEdit:
            raise Quit() from None
        finally:
            self.recursion_depth -= 1
            self.this_command, self.last_command, self.current_prefix_arg, self.this_keys = saved
            self.prefix_arg = None

    def run(self):
        """The top-level loop.  Returns the exit status."""
        try:
            while True:
                try:
                    self.command_loop()
                except (ExitRecursiveEdit, AbortRecursiveEdit):
                    self.message('No recursive edit is in progress', log=False)
                except KeyboardInterrupt:
                    self.quit()
                except ExitEditor:
                    raise
                except Exception as error:
                    self.report_error(error, 'command loop')
        except ExitEditor as leave:
            return leave.code

    # -- keyboard macros ----------------------------------------------------------------

    def execute_events(self, events):
        """Feed ``events`` through the command loop.  Returns False if an error
        or ``C-g`` cut them short."""
        frame = _MacroFrame(list(events))
        saved = (self.this_command, self.current_prefix_arg, self.this_keys)
        if not self.macro_stack:
            self.macro_abort = False
        self.macro_stack.append(frame)
        try:
            while frame.index < len(frame.events) and not self.macro_abort:
                self.command_loop_once()
        finally:
            if frame in self.macro_stack:
                self.macro_stack.remove(frame)
            self.this_command, self.current_prefix_arg, self.this_keys = saved
        completed = not self.macro_abort
        if not self.macro_stack:
            self.macro_abort = False
        return completed

    @property
    def executing_macro(self):
        return bool(self.macro_stack)

    # -- the kill ring -------------------------------------------------------------------

    def kill_new(self, text):
        """Make ``text`` the latest kill."""
        if not text:
            return
        self.kill_ring.insert(0, text)
        limit = self.var('kill_ring_max')
        if limit is not None:
            del self.kill_ring[limit:]
        self.kill_index = 0
        if self.var('select_enable_clipboard') and self.tty is not None and not self.macro_stack:
            self.tty.copy(text)

    def kill_append(self, text, before=False):
        """Add ``text`` to the latest kill, at its end or its start."""
        if not self.kill_ring:
            self.kill_new(text)
            return
        self.kill_ring[0] = text + self.kill_ring[0] if before else self.kill_ring[0] + text
        self.kill_index = 0
        if self.var('select_enable_clipboard') and self.tty is not None and not self.macro_stack:
            self.tty.copy(self.kill_ring[0])

    def current_kill(self, n=0):
        """The kill ``n`` entries back from the last one yanked; rotates the ring."""
        if not self.kill_ring:
            raise EditorError('Kill ring is empty')
        self.kill_index = (self.kill_index + n) % len(self.kill_ring)
        return self.kill_ring[self.kill_index]

    # -- leaving -------------------------------------------------------------------------

    def exit(self, code=0):
        raise ExitEditor(code)

    active_minor_modes = staticmethod(active_minor_modes)
    command_name = staticmethod(command_name)


ed = Editor()
variables.current_buffer = lambda: ed.current_buffer
