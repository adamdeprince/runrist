# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Folding commands.

``C-c @`` is the prefix Emacs's hideshow uses, and the keys under it are the
same: ``C-c @ C-c`` toggles the block at point, ``C-c @ C-h`` hides it,
``C-c @ C-s`` shows it, ``C-c @ C-M-h`` hides every definition and
``C-c @ C-M-s`` shows everything again.  Two keys from outline mode are here
as well: ``<backtab>`` cycles the whole buffer between folded and open, and
``C-<tab>`` toggles the block at point.

A folded block shows its first line followed by a badge saying how many lines
are hidden; clicking the mark in the margin opens it.  Searching into a fold
opens it, and so does moving point into one.
"""

import re

from .. import fold
from ..command import command
from ..editor import ed, global_map
from ..errors import EditorError
from ..keymap import Keymap
from ..variables import add_hook

hs_map = Keymap('hs-minor-mode-map')


def buf():
    return ed.current_buffer


def _block_at(buffer, line):
    """``(head, last)`` of the block to fold for a command given at ``line``."""
    found = fold.enclosing_block(buffer, line)
    if found is None:
        raise EditorError('No block to fold here')
    return found


def definition_heads(buffer):
    """Lines on which a definition starts, outermost and nested alike."""
    pattern = buffer.get('defun_start_regexp')
    text = buffer.text
    if pattern:
        compiled = re.compile(pattern)
        return [i for i, raw in enumerate(text.lines_raw()) if compiled.match(raw)]
    heads = []
    for i, raw in enumerate(text.lines_raw()):
        if raw.strip() and not raw[0].isspace() and raw[0] not in '#;/*)]}' \
                and fold.indent_block(buffer, i) is not None:
            heads.append(i)
    return heads


def _leave_fold(buffer):
    """If point is in hidden text, put it at the end of the fold's first line."""
    hidden = fold.hidden(buffer)
    if not hidden:
        return
    line = buffer.line_at()
    if hidden.is_hidden(line):
        buffer.goto_char(buffer.text.line_end(hidden.head_of(line)))


@command
def fold_hide_block():
    """Fold the block point is in, leaving its first line."""
    b = buf()
    head, last = _block_at(b, b.line_at())
    fold.fold_lines(b, head, last)
    _leave_fold(b)


@command
def fold_show_block():
    """Open the fold at point."""
    b = buf()
    found = fold.fold_containing(b, b.pt)
    if found is None:
        raise EditorError('Nothing is folded here')
    found.delete()


@command
def fold_toggle():
    """Fold the block at point, or open it if it is folded."""
    b = buf()
    found = fold.fold_containing(b, b.pt)
    if found is not None:
        found.delete()
    else:
        fold_hide_block()


@command
def fold_hide_all():
    """Fold every definition in the buffer, so that it reads as an outline."""
    b = buf()
    count = 0
    for head in definition_heads(b):
        last = fold.block_end(b, head)
        if last is None:
            last = fold.indent_block(b, head)
        if last is not None and last > head:
            fold.fold_lines(b, head, last)
            count += 1
    _leave_fold(b)
    ed.message(f'Folded {count} definition{"s" * (count != 1)}', log=False)


@command
def fold_show_all():
    """Open every fold in the buffer."""
    n = fold.unfold_all(buf())
    ed.message(f'Opened {n} fold{"s" * (n != 1)}', log=False)


@command('p')
def fold_hide_level(level=1):
    """Fold every block nested LEVEL deep, leaving shallower code open."""
    b = buf()
    text = b.text
    count = 0
    stack = []
    for i, raw in enumerate(text.lines_raw()):
        depth = fold.indentation(raw)
        if depth is None:
            continue
        while stack and stack[-1] >= depth:
            stack.pop()
        if len(stack) == level - 1 and fold.block_end(b, i) is not None:
            last = fold.block_end(b, i)
            if last > i:
                fold.fold_lines(b, i, last)
                count += 1
        stack.append(depth)
    _leave_fold(b)
    ed.message(f'Folded {count} block{"s" * (count != 1)}', log=False)


@command
def fold_cycle_buffer():
    """Fold every definition; when everything is folded, open everything."""
    b = buf()
    if fold.folds(b):
        fold_show_all()
    else:
        fold_hide_all()


def _keep_point_visible():
    """After a command, open any fold point has moved into."""
    b = ed.current_buffer
    if b is None or not fold.hidden(b):
        return
    line = b.line_at()
    hidden = fold.hidden(b)
    if hidden.is_hidden(line):
        if ed.this_command in ('next-line', 'previous-line', 'forward-char', 'backward-char',
                               'scroll-up-command', 'scroll-down-command'):
            # Stepping onto a fold goes round it rather than opening it.
            head = hidden.head_of(line)
            after = hidden.next(head)
            forward = ed.this_command in ('next-line', 'forward-char', 'scroll-up-command')
            if forward and after < b.text.line_count:
                b.goto_char(b.text.line_start(after))
            else:
                b.goto_char(b.text.line_end(head))
        else:
            fold.unfold_at(b, b.pt)


add_hook('post_command_hook', _keep_point_visible)

for _keys, _name in (
        ('C-c', 'fold-toggle'), ('C-e', 'fold-toggle'), ('C-t', 'fold-cycle-buffer'),
        ('C-h', 'fold-hide-block'), ('C-s', 'fold-show-block'),
        ('C-M-h', 'fold-hide-all'), ('C-M-s', 'fold-show-all'), ('C-l', 'fold-hide-level'),
        ('C-a', 'fold-show-all')):
    hs_map.bind(_keys, _name)
global_map.bind('C-c @', hs_map)
global_map.bind('<backtab>', 'fold-cycle-buffer')
global_map.bind('C-<tab>', 'fold-toggle')
global_map.bind('C-TAB', 'fold-toggle')

from ..command import defalias  # noqa: E402

for _alias, _name in (('hs-toggle-hiding', 'fold-toggle'), ('hs-hide-block', 'fold-hide-block'),
                      ('hs-show-block', 'fold-show-block'), ('hs-hide-all', 'fold-hide-all'),
                      ('hs-show-all', 'fold-show-all'), ('hs-hide-level', 'fold-hide-level')):
    defalias(_alias, _name)
