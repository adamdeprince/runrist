# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Windows and the frame that holds them.

The frame is the whole terminal.  It is divided into windows by a tree of
splits; the bottom rows belong to the minibuffer window.  A window shows one
buffer from ``start`` on, and remembers its own point for that buffer while
another window is selected.
"""

from .buffer import Marker

MIN_HEIGHT = 2          # a line of text and a mode line
MIN_WIDTH = 6


class Window:
    def __init__(self, buffer, is_minibuffer=False):
        self.buffer = buffer
        self.pointm = Marker(buffer, buffer.pt)
        self.start = Marker(buffer, buffer.point_min)   # start of the first line shown
        self.start_row = 0                              # screen row of that line shown first
        self.hscroll = 0
        self.is_minibuffer = is_minibuffer
        self.parent = None
        self.x = self.y = self.w = self.h = 0
        self.dedicated = False
        self.previous = []              # buffers shown here before, most recent last
        self.goal_column = None
        self.recenter = None            # screen row to put point on at next redisplay
        self.keep_start = False         # a command chose ``start``; move point rather than scroll
        # Filled in by redisplay.
        self.rows = []                  # [(line, first index, last index)] for each body row
        self.cursor = None              # (y, x) on the screen
        self.text_x = self.text_w = self.body_h = 0
        self.gutter_w = 0
        self.minimap_w = 0
        self.minimap = None             # state owned by the minimap
        self.prompt = None              # the minibuffer window's prompt

    def __repr__(self):
        return f'<Window on {self.buffer.name} {self.w}x{self.h}+{self.x}+{self.y}>'

    def set_buffer(self, buffer, point=None):
        """Show ``buffer`` here."""
        if buffer is self.buffer:
            if point is not None:
                self.pointm.set(point)
            return
        if self.buffer.live and not self.buffer.minibuffer:
            if self.buffer in self.previous:
                self.previous.remove(self.buffer)
            self.previous.append(self.buffer)
        self.pointm.delete()
        self.start.delete()
        self.buffer = buffer
        self.pointm = Marker(buffer, buffer.pt if point is None else point)
        self.start = Marker(buffer, buffer.point_min)
        self.start_row = 0
        self.hscroll = 0
        self.rows = []
        self.goal_column = None
        self.recenter = None
        self.keep_start = False

    @property
    def has_mode_line(self):
        return not self.is_minibuffer

    def contains(self, x, y):
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


class Split:
    """Windows side by side (``horizontal``) or one above another."""

    def __init__(self, horizontal, children, sizes):
        self.horizontal = horizontal
        self.children = children
        self.sizes = sizes
        self.parent = None
        self.x = self.y = self.w = self.h = 0
        for child in children:
            child.parent = self


def _fit(sizes, available, minimum):
    """Scale ``sizes`` to add up to ``available``, each at least ``minimum``
    when there is room for that."""
    n = len(sizes)
    if available <= 0:
        return [0] * n
    floor = minimum if available >= n * minimum else max(1, available // n) if available >= n else 0
    total = sum(sizes) or n
    out = [max(floor, size * available // total) for size in sizes]
    # Hand out or take back what rounding and the minimum left over.
    i = 0
    while sum(out) < available:
        out[i % n] += 1
        i += 1
    guard = 0
    while sum(out) > available and guard < 10 * n + available:
        j = max(range(n), key=out.__getitem__)
        if out[j] <= 0:
            break
        out[j] -= 1
        guard += 1
    return out


class Frame:
    def __init__(self, buffer, minibuffer, width, height):
        self.width = width
        self.height = height
        self.root = Window(buffer)
        self.minibuffer_window = Window(minibuffer, is_minibuffer=True)
        self.mini_height = 1
        self.layout()

    # -- geometry ------------------------------------------------------------

    def layout(self):
        mini = max(1, min(self.mini_height, self.height - MIN_HEIGHT)) if self.height > 1 else 1
        mini = max(0, min(mini, self.height))
        body = self.height - mini
        self._place(self.root, 0, 0, self.width, max(0, body))
        w = self.minibuffer_window
        w.x, w.y, w.w, w.h = 0, body, self.width, mini

    def _place(self, node, x, y, w, h):
        node.x, node.y, node.w, node.h = x, y, w, h
        if isinstance(node, Window):
            return
        n = len(node.children)
        if node.horizontal:
            node.sizes = _fit(node.sizes, w - (n - 1), MIN_WIDTH)
            at = x
            for child, size in zip(node.children, node.sizes):
                self._place(child, at, y, size, h)
                at += size + 1
        else:
            node.sizes = _fit(node.sizes, h, MIN_HEIGHT)
            at = y
            for child, size in zip(node.children, node.sizes):
                self._place(child, x, at, w, size)
                at += size

    def resize(self, width, height):
        self.width, self.height = width, height
        self.layout()

    # -- walking -------------------------------------------------------------

    def windows(self, node=None):
        """The windows showing buffers, left to right and top to bottom."""
        node = self.root if node is None else node
        if isinstance(node, Window):
            return [node]
        out = []
        for child in node.children:
            out.extend(self.windows(child))
        return out

    def window_at(self, x, y):
        if self.minibuffer_window.contains(x, y):
            return self.minibuffer_window
        for window in self.windows():
            if window.contains(x, y):
                return window
        return None

    def divider_at(self, x, y, node=None):
        """``(split, index)`` of the vertical divider at a cell, if there is one."""
        node = self.root if node is None else node
        if isinstance(node, Window):
            return None
        if node.horizontal and node.y <= y < node.y + node.h:
            at = node.x
            for i, size in enumerate(node.sizes[:-1]):
                at += size
                if x == at:
                    return node, i
                at += 1
        for child in node.children:
            found = self.divider_at(x, y, child)
            if found:
                return found
        return None

    # -- changing the tree ---------------------------------------------------

    def split(self, window, horizontal, size=None):
        """Divide ``window``; the new window is to its right or below it and
        shows the same buffer.  ``size`` is what the old window keeps."""
        total = window.w if horizontal else window.h
        usable = total - (1 if horizontal else 0)
        minimum = MIN_WIDTH if horizontal else MIN_HEIGHT
        if usable < 2 * minimum:
            from .errors import EditorError
            raise EditorError('Window too small for splitting')
        if size is None:
            keep = (usable + 1) // 2
        elif size < 0:
            keep = usable + size
        else:
            keep = size
        keep = max(minimum, min(keep, usable - minimum))
        new = Window(window.buffer)
        new.pointm.set(window.pointm.pos)
        new.start.set(window.start.pos)
        new.start_row = window.start_row
        new.hscroll = window.hscroll
        parent = window.parent
        if parent is not None and parent.horizontal == horizontal:
            i = parent.children.index(window)
            parent.children.insert(i + 1, new)
            parent.sizes[i:i + 1] = [keep, usable - keep]
            new.parent = parent
        else:
            node = Split(horizontal, [window, new], [keep, usable - keep])
            node.parent = parent
            if parent is None:
                self.root = node
            else:
                parent.children[parent.children.index(window)] = node
        self.layout()
        return new

    def delete(self, window):
        """Remove ``window``; its space goes to a neighbour.  Returns the
        window to select if the deleted one was selected."""
        parent = window.parent
        if parent is None:
            from .errors import EditorError
            raise EditorError('Attempt to delete the sole window')
        i = parent.children.index(window)
        neighbour = i - 1 if i > 0 else 1
        parent.sizes[neighbour] += parent.sizes[i] + (1 if parent.horizontal else 0)
        del parent.children[i], parent.sizes[i]
        window.pointm.delete()
        window.start.delete()
        if len(parent.children) == 1:
            only = parent.children[0]
            only.parent = parent.parent
            if parent.parent is None:
                self.root = only
            else:
                siblings = parent.parent.children
                siblings[siblings.index(parent)] = only
            successor = only
        else:
            successor = parent.children[min(neighbour if neighbour < i else i, len(parent.children) - 1)]
        self.layout()
        while isinstance(successor, Split):
            successor = successor.children[0]
        return successor

    def delete_others(self, window):
        for other in self.windows():
            if other is not window:
                other.pointm.delete()
                other.start.delete()
        window.parent = None
        self.root = window
        self.layout()

    def enlarge(self, window, delta, horizontal):
        """Make ``window`` ``delta`` columns or rows larger at a neighbour's cost."""
        node = window
        parent = node.parent
        while parent is not None and parent.horizontal != horizontal:
            node, parent = parent, parent.parent
        if parent is None:
            return False
        i = parent.children.index(node)
        other = i + 1 if i + 1 < len(parent.children) else i - 1
        minimum = MIN_WIDTH if horizontal else MIN_HEIGHT
        delta = max(minimum - parent.sizes[i], min(delta, parent.sizes[other] - minimum))
        if delta == 0:
            return False
        parent.sizes[i] += delta
        parent.sizes[other] -= delta
        self.layout()
        return True

    def balance(self, node=None):
        node = self.root if node is None else node
        if isinstance(node, Split):
            node.sizes = [1] * len(node.children)
            for child in node.children:
                self.balance(child)
        if node is self.root:
            self.layout()
