# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Commands for windows and buffers, and for the mouse."""

from .. import faces
from ..buffer import inhibit_read_only
from ..command import command
from ..editor import ed, global_map
from ..errors import EditorError
from ..minibuffer import abbreviate_file_name, read_buffer, yes_or_no_p
from ..mode import major_mode
from ..variables import defvar

defvar('mouse_wheel_scroll_amount', 3, 'Lines scrolled by one notch of the mouse wheel.')


def buf():
    return ed.current_buffer


# -- windows -----------------------------------------------------------------------------

@command('P')
def split_window_below(arg=None):
    """Split the selected window into two, one above the other."""
    w = ed.selected_window
    if w.is_minibuffer:
        raise EditorError('Cannot split the minibuffer window')
    ed.frame.split(w, horizontal=False, size=ed.prefix_numeric_value(arg) if arg else None)


@command('P')
def split_window_right(arg=None):
    """Split the selected window into two, side by side."""
    w = ed.selected_window
    if w.is_minibuffer:
        raise EditorError('Cannot split the minibuffer window')
    ed.frame.split(w, horizontal=True, size=ed.prefix_numeric_value(arg) if arg else None)


@command
def delete_window():
    """Remove the selected window."""
    w = ed.selected_window
    if w.is_minibuffer:
        raise EditorError('Attempt to delete the minibuffer window')
    successor = ed.frame.delete(w)
    ed.selected_window = successor
    ed.current_buffer = successor.buffer
    successor.buffer.pt = successor.buffer.clamp(successor.pointm.pos)
    ed.record_buffer(successor.buffer)


@command
def delete_other_windows():
    """Make the selected window fill the frame."""
    w = ed.active_window()
    ed.frame.delete_others(w)


@command('p')
def other_window(n=1):
    """Select the Nth next window."""
    windows = list(ed.frame.windows())
    if ed.minibuffer_depth > 0:
        windows.append(ed.frame.minibuffer_window)
    here = ed.selected_window
    index = windows.index(here) if here in windows else 0
    ed.select_window(windows[(index + n) % len(windows)])


@command('p')
def enlarge_window(n=1):
    """Make the selected window N lines taller."""
    if not ed.frame.enlarge(ed.selected_window, n, horizontal=False):
        raise EditorError('Cannot resize this window')


@command('p')
def shrink_window(n=1):
    """Make the selected window N lines shorter."""
    enlarge_window(-n)


@command('p')
def enlarge_window_horizontally(n=1):
    """Make the selected window N columns wider."""
    if not ed.frame.enlarge(ed.selected_window, n, horizontal=True):
        raise EditorError('Cannot resize this window')


@command('p')
def shrink_window_horizontally(n=1):
    """Make the selected window N columns narrower."""
    enlarge_window_horizontally(-n)


@command
def balance_windows():
    """Make all windows the same size."""
    ed.frame.balance()


@command
def kill_buffer_and_window():
    """Kill the current buffer and delete the selected window."""
    buffer = buf()
    if len(ed.frame.windows()) > 1:
        delete_window()
    kill_buffer(buffer.name)


@command
def quit_window():
    """Leave the buffer in this window: bury it, and close the window if it was made for it."""
    w = ed.selected_window
    buffer = w.buffer
    if buffer in ed.buffers:
        ed.buffers.remove(buffer)
        ed.buffers.append(buffer)
    if len(ed.frame.windows()) > 1 and not w.previous:
        delete_window()
    else:
        ed.switch_to_buffer(next((b for b in reversed(w.previous) if b.live and b is not buffer),
                                 ed.other_buffer(buffer)))


# -- buffers -----------------------------------------------------------------------------

@command(interactive=lambda: [read_buffer('Switch to buffer: ', default=ed.other_buffer(buf()))])
def switch_to_buffer(name):
    """Show buffer NAME in the selected window, creating it if need be."""
    ed.switch_to_buffer(name)


@command(interactive=lambda: [read_buffer('Switch to buffer in other window: ',
                                          default=ed.other_buffer(buf()))])
def switch_to_buffer_other_window(name):
    """Show buffer NAME in another window and select it."""
    ed.display_buffer(ed.get_buffer(name) or ed.get_buffer_create(name), select=True)


@command(interactive=lambda: [read_buffer('Display buffer: ', default=ed.other_buffer(buf()))])
def display_buffer(name):
    """Show buffer NAME in another window without selecting it."""
    ed.display_buffer(ed.get_buffer(name) or ed.get_buffer_create(name))


@command(interactive=lambda: [read_buffer('Kill buffer: ', default=buf(), require_match=True)])
def kill_buffer(name):
    """Get rid of buffer NAME."""
    buffer = ed.get_buffer(name)
    if buffer is None:
        raise EditorError(f'No such buffer: {name}')
    if buffer.modified and buffer.filename and not yes_or_no_p(
            f'Buffer {buffer.name} modified; kill anyway?'):
        return
    from .. import files
    files.delete_auto_save(buffer)
    ed.kill_buffer(buffer)


def _cycle(step):
    w = ed.selected_window
    candidates = [b for b in ed.buffers if not b.temporary]
    if len(candidates) < 2:
        raise EditorError('No other buffer')
    here = candidates.index(w.buffer) if w.buffer in candidates else 0
    ed.switch_to_buffer(candidates[(here + step) % len(candidates)])


@command
def previous_buffer():
    """Show the buffer that was shown before this one."""
    w = ed.selected_window
    earlier = [b for b in w.previous if b.live and b is not w.buffer]
    if earlier:
        target = earlier[-1]
        w.previous.remove(target)
        current = w.buffer
        w.set_buffer(target, target.pt)
        ed.current_buffer = target
        if current in w.previous:
            w.previous.remove(current)
        w.previous.insert(0, current)
    else:
        _cycle(1)


@command
def next_buffer():
    """Show the next buffer in the cycle ``previous-buffer`` walks backwards."""
    w = ed.selected_window
    if w.previous and w.previous[0].live and w.previous[0] is not w.buffer:
        target = w.previous.pop(0)
        w.set_buffer(target, target.pt)
        ed.current_buffer = target
    else:
        _cycle(-1)


@command
def bury_buffer():
    """Put the current buffer at the end of the list and show another."""
    buffer = buf()
    ed.buffers.remove(buffer)
    ed.buffers.append(buffer)
    ed.switch_to_buffer(ed.other_buffer(buffer))


@command('sRename buffer (to new name): ')
def rename_buffer(name):
    """Give the current buffer a new name."""
    if not name:
        raise EditorError('Empty string is invalid as a buffer name')
    if ed.get_buffer(name) not in (None, buf()):
        raise EditorError(f'Buffer name {name!r} is in use')
    buf().name = name


@command
def read_only_mode():
    """Toggle whether the current buffer can be changed."""
    b = buf()
    b.read_only = not b.read_only
    ed.message('Read-only ' + ('enabled' if b.read_only else 'disabled'), log=False)


@major_mode('buffer-menu-mode', 'Buffer Menu', parent='special-mode')
def buffer_menu_mode(buffer):
    """List of buffers.  RET visits the buffer on the line; d kills it; g refreshes."""
    buffer.set('truncate_lines', True)
    buffer.set('display_line_numbers', False)


def _fill_buffer_menu(menu):
    lines = []
    listed = []
    for b in ed.buffers:
        if b.temporary or b is menu:
            continue
        flags = ('%' if b.read_only else ' ') + ('*' if b.modified and b.filename else ' ')
        mode = b.major_mode.title if b.major_mode else ''
        lines.append(f' {flags} {b.name:<28} {b.size:>9}  {mode:<16} '
                     f'{abbreviate_file_name(b.filename) if b.filename else ""}')
        listed.append(b)
    header = f' {"":2} {"Buffer":<28} {"Size":>9}  {"Mode":<16} File'
    with inhibit_read_only():
        menu.erase()
        menu.insert(header + '\n' + '\n'.join(lines) + '\n')
    menu.remove_overlays()
    menu.make_overlay(0, len(header), face='help-heading')
    menu.locals['_buffer_menu'] = listed
    menu.set_modified(False)
    menu.goto_char(menu.text.line_start(1) if lines else 0)


@command
def list_buffers():
    """Show a list of the buffers; RET on a line visits that buffer."""
    menu = ed.get_buffer('*Buffer List*') or ed.new_buffer('*Buffer List*', 'buffer-menu-mode')
    menu.read_only = True
    menu.undo_enabled = False
    _fill_buffer_menu(menu)
    ed.display_buffer(menu, select=True)


def _menu_buffer():
    b = buf()
    listed = b.locals.get('_buffer_menu') or []
    index = b.line_at() - 1
    if not 0 <= index < len(listed) or not listed[index].live:
        raise EditorError('No buffer on this line')
    return listed[index]


@command
def buffer_menu_select():
    """Visit the buffer on this line."""
    ed.switch_to_buffer(_menu_buffer())


@command
def buffer_menu_kill():
    """Kill the buffer on this line."""
    menu = buf()
    line = menu.line_at()
    kill_buffer(_menu_buffer().name)
    _fill_buffer_menu(menu)
    menu.goto_line(line + 1)


@command
def buffer_menu_refresh():
    """Bring the list of buffers up to date."""
    _fill_buffer_menu(buf())


# -- the mouse ---------------------------------------------------------------------------

def _locate(event):
    return ed.display.locate(event.x, event.y)


def _select_for(window):
    """Select the window the mouse is in; the minibuffer only while it is in use."""
    if window is None or window is ed.selected_window:
        return
    if window.is_minibuffer and ed.minibuffer_depth == 0:
        return
    ed.select_window(window)


@command
def mouse_drag_region():
    """Set point where the mouse is pressed; dragging selects a region.

    Pressing on the minimap moves the view there, a divider drags to resize
    the windows it separates, and the margin toggles a fold."""
    event = ed.last_event
    window, area, pos = _locate(event)
    if area == 'divider':
        _drag_divider(event)
        return
    if window is None:
        return
    if area == 'minimap':
        from .. import minimap
        minimap.mouse_drag(window, event)
        return
    if area == 'mode-line':
        _select_for(window)
        _swallow_release()
        return
    if area == 'minibuffer':
        if ed.minibuffer_depth > 0:
            ed.select_window(window)
        _swallow_release()
        return
    _select_for(window)
    b = ed.current_buffer
    if pos is None:
        _swallow_release()
        return
    if area == 'gutter':
        from .. import fold
        line = b.line_at(pos)
        if fold.hidden(b).fold_headed_by(line):
            fold.unfold_at(b, b.text.line_end(line) + 1)
            _swallow_release()
            return
    b.goto_char(pos)
    b.mark_active = False
    origin = pos
    while True:
        moved = ed.read_event(record=False)
        if moved.key == '<mouse-movement>':
            target_window, target_area, target = _locate(moved)
            if target_window is window and target is not None:
                b.goto_char(target)
            elif moved.y < window.y:
                ed.display.scroll(window, -1)
                b.goto_char(ed.display.point_into_view(window, b.pt))
            elif moved.y >= window.y + window.body_h:
                ed.display.scroll(window, 1)
                b.goto_char(ed.display.point_into_view(window, b.pt))
            if b.pt != origin:
                b.set_mark(origin)
                b.mark_active = True
            else:
                b.mark_active = False
            continue
        if moved.is_mouse:
            break
        ed.unread.appendleft(moved)
        break
    ed.keep_mark_active = True
    if b.mark_active:
        b.locals['_shift_region'] = False


def _swallow_release():
    while True:
        event = ed.read_event(record=False)
        if event.key == '<mouse-movement>':
            continue
        if not event.is_mouse:
            ed.unread.appendleft(event)
        return


def _drag_divider(event):
    found = ed.frame.divider_at(event.x, event.y)
    if not found:
        return
    split, index = found
    while True:
        moved = ed.read_event(record=False)
        if moved.key != '<mouse-movement>':
            if not moved.is_mouse:
                ed.unread.appendleft(moved)
            return
        edge = split.x + sum(split.sizes[:index + 1]) + index
        delta = moved.x - edge
        if delta and split.sizes[index] + delta >= 6 and split.sizes[index + 1] - delta >= 6:
            split.sizes[index] += delta
            split.sizes[index + 1] -= delta
            ed.frame.layout()


def _word_bounds(b, pos):
    line = b.line_at(pos)
    start = b.text.line_start(line)
    text = b.text.line(line)
    index = min(pos - start, len(text))
    lo = hi = index
    if index < len(text) and (text[index].isalnum() or text[index] == '_'):
        while lo > 0 and (text[lo - 1].isalnum() or text[lo - 1] == '_'):
            lo -= 1
        while hi < len(text) and (text[hi].isalnum() or text[hi] == '_'):
            hi += 1
    elif index < len(text):
        hi = index + 1
    return start + lo, start + hi


@command
def mouse_select_word():
    """Select the word under the mouse."""
    window, area, pos = _locate(ed.last_event)
    if window is None or area != 'text' or pos is None:
        return
    _select_for(window)
    b = ed.current_buffer
    start, end = _word_bounds(b, pos)
    b.set_mark(start)
    b.goto_char(end)
    b.mark_active = True
    ed.keep_mark_active = True


@command
def mouse_select_line():
    """Select the line under the mouse."""
    window, area, pos = _locate(ed.last_event)
    if window is None or pos is None:
        return
    _select_for(window)
    b = ed.current_buffer
    b.set_mark(b.bol(pos))
    b.goto_char(min(b.point_max, b.eol(pos) + 1))
    b.mark_active = True
    ed.keep_mark_active = True


@command
def mouse_yank():
    """Insert the last kill where the mouse was clicked."""
    window, area, pos = _locate(ed.last_event)
    if window is None or area != 'text' or pos is None:
        return
    _select_for(window)
    b = ed.current_buffer
    b.goto_char(pos)
    b.insert(ed.current_kill(0))


def _wheel(direction):
    event = ed.last_event
    window = ed.frame.window_at(event.x, event.y) if event is not None and event.is_mouse \
        else ed.selected_window
    if window is None or window.is_minibuffer:
        if ed.minibuffer_depth > 0 and ed.completion_rows is not None:
            from .. import minibuffer
            minibuffer._move_selection(direction * 3, wrap=False)
        return
    rows = direction * int(ed.var('mouse_wheel_scroll_amount'))
    if not ed.display.scroll(window, rows):
        return
    target = ed.display.point_into_view(window, ed.window_point(window))
    ed.set_window_point(window, target)


@command
def mouse_wheel_up():
    """Scroll the window under the mouse towards the beginning of its buffer."""
    _wheel(-1)


@command
def mouse_wheel_down():
    """Scroll the window under the mouse towards the end of its buffer."""
    _wheel(1)


@command('P')
def load_theme(arg=None):
    """Choose a color theme."""
    from ..minibuffer import completing_read
    name = completing_read('Load theme: ', sorted(faces.themes), require_match=True,
                           annotate=lambda n: faces.themes[n].doc, category='theme')
    faces.load_theme(name)
    ed.display.invalidate()
    ed.message(f'Loaded theme {name}', log=False)


for _keys, _name in (('RET', 'buffer-menu-select'), ('f', 'buffer-menu-select'),
                     ('d', 'buffer-menu-kill'), ('k', 'buffer-menu-kill'),
                     ('g', 'buffer-menu-refresh')):
    buffer_menu_mode.mode.keymap.bind(_keys, _name)

for _keys, _name in (
        ('C-x 2', 'split-window-below'), ('C-x 3', 'split-window-right'),
        ('C-x 0', 'delete-window'), ('C-x 1', 'delete-other-windows'),
        ('C-x o', 'other-window'), ('C-x ^', 'enlarge-window'),
        ('C-x }', 'enlarge-window-horizontally'), ('C-x {', 'shrink-window-horizontally'),
        ('C-x +', 'balance-windows'), ('C-x 4 0', 'kill-buffer-and-window'),
        ('C-x b', 'switch-to-buffer'), ('C-x 4 b', 'switch-to-buffer-other-window'),
        ('C-x 4 C-o', 'display-buffer'), ('C-x k', 'kill-buffer'),
        ('C-x C-b', 'list-buffers'), ('C-x <left>', 'previous-buffer'),
        ('C-x <right>', 'next-buffer'), ('C-x C-<left>', 'previous-buffer'),
        ('C-x C-<right>', 'next-buffer'), ('C-x C-q', 'read-only-mode'),
        ('<down-mouse-1>', 'mouse-drag-region'), ('<double-down-mouse-1>', 'mouse-select-word'),
        ('<triple-down-mouse-1>', 'mouse-select-line'), ('<mouse-2>', 'mouse-yank'),
        ('<wheel-up>', 'mouse-wheel-up'), ('<wheel-down>', 'mouse-wheel-down'),
        ('S-<wheel-up>', 'mouse-wheel-up'), ('S-<wheel-down>', 'mouse-wheel-down')):
    global_map.bind(_keys, _name)


# -- Silkie's scroll reports ---------------------------------------------------------------

_scroll_remainder = {}


def _silkie_scroll(report):
    """Scroll by what a trackpad or wheel reports, in pixels, a line at a time.

    Silkie reports distances in hundredths of a pixel.  They are added up for
    the window under the pointer and turned into lines as they come to a
    line's height; what is left over stays for the next report, so a slow
    drag still moves the text."""
    cell = ed.tty.caps.cell
    if not cell:
        return
    dy = report.integer('dy', 0) or 0
    x = (report.integer('x', 0) or 0) // 256 // max(1, cell[0])
    y = (report.integer('y', 0) or 0) // 256 // max(1, cell[1])
    window = ed.frame.window_at(x, y) or ed.selected_window
    phase = report.integer('phase', 0) or 0
    if phase in (1, 7):
        _scroll_remainder.pop(window, None)
    if not dy:
        return
    total = _scroll_remainder.get(window, 0) + dy
    step = cell[1] * 100
    rows = int(total / step)
    _scroll_remainder[window] = total - rows * step
    if not rows:
        return
    if window.is_minibuffer:
        if ed.minibuffer_depth > 0 and ed.completion_rows is not None:
            from .. import minibuffer
            minibuffer._move_selection(rows, wrap=False)
        return
    if ed.display.scroll(window, rows):
        ed.set_window_point(window, ed.display.point_into_view(window, ed.window_point(window)))


ed.protocol_handlers['silkie:scroll'] = _silkie_scroll
