# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The editor's conditions.

``EditorError`` is what Emacs calls a ``user-error``: a command could not do
what was asked, and saying so in the echo area is the whole of the handling.
Anything else that escapes a command is a bug in that command; the command
loop reports it and keeps the traceback, and the editor carries on.

``Quit`` is ``C-g``.  It derives from ``KeyboardInterrupt`` so that code
catching ``Exception`` does not swallow it, and so that a real SIGINT is
treated the same way.
"""


class EditorError(Exception):
    """A command could not proceed; the message is shown to the user."""


class BufferReadOnly(EditorError):
    def __init__(self, name=''):
        super().__init__(f'Buffer is read-only: {name}' if name else 'Buffer is read-only')


class BeginningOfBuffer(EditorError):
    def __init__(self):
        super().__init__('Beginning of buffer')


class EndOfBuffer(EditorError):
    def __init__(self):
        super().__init__('End of buffer')


class SearchFailed(EditorError):
    def __init__(self, what=''):
        super().__init__(f'Search failed: {what}' if what else 'Search failed')
        self.what = what


class ArgsOutOfRange(EditorError):
    def __init__(self, *values):
        super().__init__('Args out of range: ' + ', '.join(map(repr, values)))


class Quit(KeyboardInterrupt):
    """``C-g``: abandon the command in progress."""


class ExitRecursiveEdit(BaseException):
    """Leave the innermost recursive edit normally."""


class AbortRecursiveEdit(BaseException):
    """Leave the innermost recursive edit and quit what entered it."""


class ExitEditor(BaseException):
    """Leave the editor; ``code`` is the process exit status."""

    def __init__(self, code=0):
        super().__init__(code)
        self.code = code
