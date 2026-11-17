# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The modes other modes are derived from."""

from ..mode import major_mode, major_modes
from ..variables import defvar

defvar('comment_start', None, 'String that begins a comment, or None.', local=True)
defvar('comment_end', '', 'String that ends a comment; empty if the line end does.', local=True)
defvar('comment_start_skip', None,
       'Regular expression matching the start of a comment and the space after it.', local=True)


@major_mode('fundamental-mode', 'Fundamental')
def fundamental_mode(buffer):
    """The mode with no special behaviour, from which every other is derived."""


@major_mode('text-mode', 'Text', parent='fundamental-mode',
            auto=[r'\.te?xt$', r'/(README|NEWS|TODO|COPYING|LICENSE|AUTHORS|INSTALL)$'])
def text_mode(buffer):
    """For text people read: words wrap whole, and paragraphs fill."""
    buffer.set('word_wrap', True)


@major_mode('prog-mode', 'Prog', parent='fundamental-mode')
def prog_mode(buffer):
    """For source code.  Language modes are derived from this."""
    from ..commands.basic import indent_relative_line
    buffer.set('indent_line_function', indent_relative_line)


@major_mode('special-mode', 'Special', parent='fundamental-mode')
def special_mode(buffer):
    """For buffers that are looked at rather than edited: q leaves, g refreshes."""
    buffer.read_only = True
    buffer.set('display_line_numbers', False)
    buffer.set('hl_line_mode', False)
    buffer.set('minimap_mode', False)


_special = major_modes['special-mode'].keymap
for _keys, _name in (('q', 'quit-window'), ('SPC', 'scroll-up-command'),
                     ('DEL', 'scroll-down-command'), ('S-SPC', 'scroll-down-command'),
                     ('<', 'beginning-of-buffer'), ('>', 'end-of-buffer'),
                     ('g', 'revert-buffer'), ('n', 'next-line'), ('p', 'previous-line'),
                     ('h', 'describe-mode'), ('?', 'describe-mode')):
    _special.bind(_keys, _name)
