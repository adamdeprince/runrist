# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The terminal.

``Tty`` is the real one, reached through hamingjur.  It puts the terminal in
the state the editor needs and takes it out again, asks what the terminal can
do, and turns what arrives into events.  ``FakeTty`` has the same interface
with a fixed size, scripted input and captured output; the tests and
``--batch`` use it.

Waiting for input is the one place the editor is idle, so that is where the
asyncio loop runs: timers fire and subprocess output arrives while a key is
awaited, and never in the middle of a command.
"""

import asyncio
import contextlib
import os
import signal
import sys
import time

from . import faces
from .keys import Decoder, Event


class Capabilities:
    """What was learned about the terminal at startup."""

    def __init__(self):
        self.kitty_keyboard = False
        self.graphics = False           # kitty graphics protocol
        self.palette_images = False     # Silkie f=408
        self.silkie = False
        self.fonts = frozenset()
        self.scroll = False             # Silkie pixel scroll reports
        self.cell = None                # (width, height) of a cell in pixels
        self.sync = True
        self.clipboard = True
        self.name = ''

    def describe(self):
        cell = f'{self.cell[0]}x{self.cell[1]}px cells' if self.cell else 'cell size unknown'
        parts = [self.name or 'terminal', cell]
        for flag, label in ((self.kitty_keyboard, 'kitty keyboard'), (self.graphics, 'graphics'),
                            (self.palette_images, 'palette images'), (self.silkie, 'silkie'),
                            (self.scroll, 'pixel scroll')):
            if flag:
                parts.append(label)
        if self.fonts:
            parts.append('fonts: ' + ','.join(sorted(self.fonts)))
        return ', '.join(parts)


class Tty:
    """The controlling terminal."""

    is_fake = False

    def __init__(self, loop):
        from hamingjur import Terminal
        self.term = Terminal()
        self.loop = loop
        self.caps = Capabilities()
        self.decoder = Decoder(cell_size=lambda: self.caps.cell)
        self._out = []
        self._stack = None
        self._pending = []
        self._waiter = None

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        term = self.term
        stack = self._stack = contextlib.ExitStack()
        try:
            stack.enter_context(term.raw())
            stack.enter_context(term.fullscreen())
            self._probe()
            stack.enter_context(term.enable_kitty_keyboard(
                disambiguate=True, report_alternates=True, timeout=1.0))
            stack.enter_context(term.mouse_enabled(report_drag=True, timeout=1.0))
            stack.enter_context(term.bracketed_paste(timeout=1.0))
            stack.enter_context(term.focus_events(timeout=1.0))
            stack.enter_context(term.notify_on_resize(timeout=1.0))
            if self.caps.scroll:
                try:
                    granted = stack.enter_context(term.scroll.enabled(1, timeout=1.0))
                    self.caps.scroll = bool(granted)
                except RuntimeError:
                    self.caps.scroll = False
            stack.callback(self._restore)
            term.stream.write('\x1b[2J\x1b[H')
            term.stream.flush()
        except BaseException:
            stack.close()
            self._stack = None
            raise

    def _restore(self):
        stream = self.term.stream
        # Free our images, then put back the cursor and attributes.
        stream.write('\x1b_Ga=d,d=A,q=2\x1b\\' if self.caps.graphics else '')
        stream.write('\x1b[0m\x1b[0 q\x1b]112\x07\x1b[?25h')
        stream.flush()

    def stop(self):
        if self._stack is not None:
            self._out.clear()
            stack, self._stack = self._stack, None
            stack.close()

    def _probe(self):
        term = self.term
        caps = self.caps
        caps.name = os.environ.get('TERM_PROGRAM') or os.environ.get('TERM', '')
        faces.caps.truecolor = term.number_of_colors >= 1 << 24
        try:
            listed = term.silkie.capabilities(timeout=0.5)
        except Exception:
            listed = None
        if listed is not None:
            caps.silkie = True
            caps.name = 'silkie'
            caps.palette_images = 408 in listed.formats
            caps.scroll = listed.supports('scroll')
            caps.graphics = bool(listed.formats)
            if listed.supports('font'):
                try:
                    report = term.font.query(timeout=0.5)
                except Exception:
                    report = None
                if report is not None and report.integer('caps', 0):
                    roles = report.fields.get('roles', '')
                    caps.fonts = frozenset(filter(None, roles.split(',')))
        if not caps.graphics:
            try:
                caps.graphics = bool(term.does_kitty_graphics(timeout=0.5))
            except Exception:
                caps.graphics = False
        try:
            caps.kitty_keyboard = term.get_kitty_keyboard_state(timeout=0.5) is not None
        except Exception:
            caps.kitty_keyboard = False
        self.measure_cell()
        faces.caps.fonts = caps.fonts
        faces.caps.font_weights = bool(caps.fonts)
        faces.caps.styled_underline = caps.kitty_keyboard or caps.graphics
        faces.refresh()

    def measure_cell(self):
        try:
            size = self.term.get_cell_size(timeout=0.5, force=True)
        except Exception:
            size = None
        self.caps.cell = (size.width, size.height) if size else None

    @contextlib.contextmanager
    def suspended(self):
        """Give the terminal back for a while, as when the editor is stopped."""
        self.stop()
        try:
            yield
        finally:
            self.start()

    # -- size ----------------------------------------------------------------

    @property
    def width(self):
        return max(1, self.term.width)

    @property
    def height(self):
        return max(1, self.term.height)

    # -- output --------------------------------------------------------------

    def write(self, text):
        self._out.append(text)

    def flush(self):
        if not self._out:
            return
        data = ''.join(self._out)
        self._out.clear()
        stream = self.term.stream
        if self.caps.sync:
            stream.write('\x1b[?2026h' + data + '\x1b[?2026l')
        else:
            stream.write(data)
        stream.flush()

    def send_now(self, text):
        """Write outside the frame being built, for queries and one-off commands."""
        self.term.stream.write(text)
        self.term.stream.flush()

    def move(self, y, x):
        return f'\x1b[{y + 1};{x + 1}H'

    def bell(self):
        self.send_now('\x07')

    def set_title(self, title):
        clean = ''.join(c for c in title if c >= ' ' and c != '\x7f')
        self.send_now(f'\x1b]2;{clean}\x1b\\')

    def copy(self, text):
        try:
            self.term.clipboard_copy(text)
        except Exception:
            pass

    # -- input ---------------------------------------------------------------

    def input_pending(self):
        if self._pending:
            return True
        try:
            return bool(self.term._keyboard_buf) or self.term.kbhit(timeout=0)
        except Exception:
            return False

    def read(self, timeout=None):
        """The next event, or None if ``timeout`` seconds pass without one or
        ``wake`` is called.

        While this waits the asyncio loop runs, which is when timers fire and
        output from subprocesses is read.
        """
        if self._pending:
            return self._pending.pop(0)
        deadline = None if timeout is None else time.monotonic() + timeout
        term = self.term
        while True:
            ks = term.inkey(timeout=0)
            if ks or ks.name:
                event = self.decoder.decode(ks, time.monotonic())
                if event is not None:
                    if event.key == '<resize>':
                        self.measure_cell()
                    return event
                continue                    # a reply to a query, say; there may be more
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                return None
            if not self.loop.run_until_complete(self._readable(left)):
                return None

    async def _readable(self, timeout):
        loop = self.loop
        waiter = self._waiter = loop.create_future()

        def ready():
            if not waiter.done():
                waiter.set_result(True)

        term = self.term
        descriptors = [fd for fd in (getattr(term, '_keyboard_fd', None),
                                     getattr(term, '_resize_read_fd', None)) if fd is not None]
        for fd in descriptors:
            loop.add_reader(fd, ready)
        handle = loop.call_later(timeout, waiter.cancel) if timeout is not None else None
        try:
            return await waiter
        except asyncio.CancelledError:
            return False
        finally:
            if handle is not None:
                handle.cancel()
            for fd in descriptors:
                loop.remove_reader(fd)
            self._waiter = None

    def wake(self):
        """Make a ``read`` in progress return None.  Call from the loop's own thread."""
        waiter = self._waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(False)

    def unread(self, event):
        self._pending.insert(0, event)

    def suspend_process(self):
        """Stop the editor's process group, as ``C-z`` does in a shell."""
        with self.suspended():
            os.kill(0, signal.SIGTSTP)


class FakeTty:
    """A terminal that is not there."""

    is_fake = True

    def __init__(self, loop=None, width=80, height=24):
        self.loop = loop or asyncio.new_event_loop()
        self._width = width
        self._height = height
        self.caps = Capabilities()
        self.caps.name = 'fake'
        self.decoder = Decoder()
        self.output = []
        self.events = []
        self.title = ''
        self.clipboard = None
        self.bells = 0

    def start(self):
        pass

    def stop(self):
        pass

    def measure_cell(self):
        pass

    @contextlib.contextmanager
    def suspended(self):
        yield

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    def resize(self, width, height):
        self._width, self._height = width, height
        self.events.insert(0, Event('<resize>'))

    def write(self, text):
        self.output.append(text)

    def flush(self):
        pass

    def send_now(self, text):
        self.output.append(text)

    def move(self, y, x):
        return f'\x1b[{y + 1};{x + 1}H'

    def bell(self):
        self.bells += 1

    def set_title(self, title):
        self.title = title

    def copy(self, text):
        self.clipboard = text

    def feed(self, *events):
        """Queue events: ``Event`` objects, or strings in ``kbd`` notation."""
        from .keys import kbd
        for item in events:
            if isinstance(item, Event):
                self.events.append(item)
            else:
                self.events.extend(Event(key) for key in kbd(item))

    def type(self, text):
        """Queue the keys that type ``text``."""
        for char in text:
            self.events.append(Event({'\n': 'RET', ' ': 'SPC', '\t': 'TAB'}.get(char, char)))

    def input_pending(self):
        return bool(self.events)

    def read(self, timeout=None):
        if self.events:
            return self.events.pop(0)
        if timeout is None:
            raise EOFError('the fake terminal has no more input')
        # Let timers that are due run, as waiting on a real terminal would.
        self.loop.run_until_complete(asyncio.sleep(0))
        return None

    def unread(self, event):
        self.events.insert(0, event)

    def wake(self):
        pass

    def suspend_process(self):
        pass


def stdio_is_terminal():
    return sys.__stdin__ is not None and sys.__stdin__.isatty() and sys.__stdout__.isatty()
