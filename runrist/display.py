# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Redisplay.

Each redisplay works out afresh what every row of the terminal should show,
compares that with what the terminal shows now, and writes the rows that
differ.  A row is built from *runs*, pairs of a style and text, and every
part of the screen produces runs exactly as wide as the space it owns, so no
row ever needs erasing and images placed over the text are left alone.

A window's text area keeps its last column free.  That is where a wrapped
line shows its continuation mark and a truncated one its ellipsis, and where
the cursor sits at the end of a line that fills the row.

A long line is wrapped into rows at the indexes ``row_starts`` gives; a
position in the buffer is a line, a row of that line and a column in that
row.  The scrolling and hit-testing here, and the commands that move by
screen lines, all go through that one function.
"""

from bisect import bisect_left, bisect_right

from . import faces, fold, fontlock
from .chars import clip, text_width
from .variables import defvar

defvar('truncate_lines', False,
       'If true, long lines run off the edge of the window instead of wrapping.', local=True)
defvar('word_wrap', False, 'If true, wrapped lines break after spaces.', local=True)
defvar('display_line_numbers', True, 'Whether lines are numbered in the margin.', local=True)
defvar('hl_line_mode', True, 'Whether the line point is on is highlighted.', local=True)
defvar('show_trailing_whitespace', False,
       'Whether whitespace at the end of a line is highlighted.', local=True)
defvar('scroll_margin', 0,
       'How many lines point is kept from the top and bottom of the window.')
defvar('scroll_conservatively', True,
       'If true, scroll only as far as needed when point leaves the window by a '
       'little; otherwise recenter, as Emacs does by default.')
defvar('cursor_type', 'bar', "The shape of the cursor: 'bar', 'box' or 'underline'.")
defvar('cursor_blink', False, 'Whether the cursor blinks.')
defvar('max_mini_window_height', 0.4,
       'The largest share of the frame the minibuffer window may grow to.')
defvar('highlight_nonselected_windows', False,
       'Whether the region is highlighted in windows other than the selected one.')
defvar('minimap_mode', True, 'Whether this buffer shows a minimap.', local=True)
defvar('minimap_width', 14, 'Width of the minimap, in columns.')

_ONE = (0,)
_CURSOR = {'box': 2, 'underline': 4, 'bar': 6}

# Functions called as f(window, buffer, line, line start, text) for each line
# drawn; each returns extra highlights as (start, end, face, priority) or None.
highlighters = []

# Functions called as f(display, out, full) once the rows are drawn, to add
# their own output -- images, say -- to the frame.
after_draw = []


def render(runs):
    """``(escape string, plain text)`` for a list of runs."""
    out = []
    plain = []
    last = None
    for style_, text in runs:
        if not text:
            continue
        if style_ != last:
            out.append(faces.sgr(style_))
            last = style_
        out.append(text)
        plain.append(text)
    return ''.join(out), ''.join(plain)


def fit(runs, width, fill):
    """``runs`` cut or padded to exactly ``width`` cells; padding has style ``fill``."""
    out = []
    used = 0
    for style_, text in runs:
        if used >= width:
            break
        piece, cells = clip(text, width - used)
        if piece:
            out.append((style_, piece))
            used += cells
        if len(piece) < len(text):
            break
    if used < width:
        out.append((fill, ' ' * (width - used)))
    return out


class Display:
    def __init__(self, ed):
        self.ed = ed
        self.screen = []
        self.plain = []
        self.size = (0, 0)
        self.full = True
        self.face_generation = -1
        self.rows = []
        self.cursor = None
        self._cursor_sent = None

    def invalidate(self):
        """Redraw everything at the next redisplay."""
        self.full = True

    # -- layout of one line ------------------------------------------------------

    def row_starts(self, buf, line, width, truncate=False):
        """Indexes into ``line`` at which each of its screen rows starts."""
        if truncate or width <= 0:
            return _ONE
        info = buf.line_info(line)
        n = buf.text.line_length(line)
        cols = info.cols
        if n <= width and (cols is None or cols[n] <= width):
            return _ONE
        word = bool(buf.get('word_wrap'))
        key = (width, word)
        if info.wrap_key == key:
            return info.breaks
        text = buf.text.line(line) if word else None
        starts = [0]
        k = 0
        while True:
            j = k + width if cols is None else bisect_right(cols, cols[k] + width, k) - 1
            if j >= n:
                break
            if j <= k:
                j = k + 1               # a character wider than the window still takes a row
            if word:
                space = text.rfind(' ', k, j)
                if space > k:
                    j = space + 1
            starts.append(j)
            k = j
        info.wrap_key = key
        info.breaks = starts
        return starts

    def wrap_params(self, window):
        """``(columns of text per row, whether lines are truncated)`` for a window."""
        self.geometry(window)
        truncate = bool(window.buffer.get('truncate_lines')) or window.is_minibuffer
        return max(1, window.text_w - 1), truncate

    @staticmethod
    def _col(info, index):
        return index if info.cols is None else info.cols[index]

    def geometry(self, w):
        buf = w.buffer
        w.body_h = max(0, w.h - (1 if w.has_mode_line else 0))
        if w.is_minibuffer:
            w.gutter_w = w.minimap_w = 0
        else:
            if buf.get('display_line_numbers'):
                w.gutter_w = 2 + max(3, len(str(buf.line_count)))
            else:
                w.gutter_w = 1
            width = 0
            caps = self.ed.tty.caps
            if buf.get('minimap_mode') and caps.graphics and caps.cell:
                width = max(0, int(buf.get('minimap_width')))
                if w.w - w.gutter_w - width < 20:
                    width = 0               # too narrow to give any of it up
            w.minimap_w = width
        w.text_x = w.x + w.gutter_w
        w.text_w = max(0, w.w - w.gutter_w - w.minimap_w)

    # -- moving by screen rows ---------------------------------------------------

    def _limits(self, buf):
        text = buf.text
        return text.pos_to_line(buf.point_min), text.pos_to_line(buf.point_max)

    def advance(self, buf, line, row, k, width, truncate):
        """``(line, row, rows not moved)`` after moving ``k`` screen rows down."""
        hidden = fold.hidden(buf)
        _, last = self._limits(buf)
        while k > 0:
            count = len(self.row_starts(buf, line, width, truncate))
            if row + k < count:
                return line, row + k, 0
            following = hidden.next(line)
            if following > last:
                moved = count - 1 - row
                return line, count - 1, k - moved
            k -= count - row
            line, row = following, 0
        return line, row, 0

    def retreat(self, buf, line, row, k, width, truncate):
        """``(line, row, rows not moved)`` after moving ``k`` screen rows up."""
        hidden = fold.hidden(buf)
        first, _ = self._limits(buf)
        while k > 0:
            if row >= k:
                return line, row - k, 0
            before = hidden.prev(line)
            if before < first:
                return line, 0, k - row
            k -= row + 1
            line = before
            row = len(self.row_starts(buf, line, width, truncate)) - 1
        return line, row, 0

    def row_distance(self, buf, top, target, width, truncate, limit):
        """Screen rows from ``top`` down to ``target``, both ``(line, row)``;
        negative if the target is above.  None if it is more than ``limit``
        rows away."""
        hidden = fold.hidden(buf)
        if target >= top:
            (line, row), (end_line, end_row), sign = top, target, 1
        else:
            (line, row), (end_line, end_row), sign = target, top, -1
        n = -row
        while line < end_line:
            n += len(self.row_starts(buf, line, width, truncate))
            if n > limit:
                return None
            line = hidden.next(line)
        n += end_row
        return None if n > limit else sign * n

    def vertical_motion(self, window, pos, n, goal=None):
        """Position ``n`` screen rows below ``pos`` (above if negative), keeping
        to screen column ``goal``.  Returns ``(position, goal, rows not moved)``."""
        buf = window.buffer
        text = buf.text
        width, truncate = self.wrap_params(window)
        hidden = fold.hidden(buf)
        line, index = text.pos_to_linecol(pos)
        if hidden.is_hidden(line):
            line = hidden.head_of(line)
            index = text.line_length(line)
        info = buf.line_info(line)
        starts = self.row_starts(buf, line, width, truncate)
        row = bisect_right(starts, index) - 1
        if goal is None:
            goal = self._col(info, index) - self._col(info, starts[row])
        if n >= 0:
            line, row, short = self.advance(buf, line, row, n, width, truncate)
        else:
            line, row, short = self.retreat(buf, line, row, -n, width, truncate)
        info = buf.line_info(line)
        starts = self.row_starts(buf, line, width, truncate)
        length = text.line_length(line)
        a = starts[row]
        b = starts[row + 1] - 1 if row + 1 < len(starts) else length
        index = buf.column_to_index(line, self._col(info, a) + goal)
        index = max(a, min(index, b))
        return buf.clamp(text.line_start(line) + index), goal, short

    def scroll(self, window, rows):
        """Move the window's text ``rows`` screen rows up (down if negative),
        then bring point back inside.  Returns the rows actually scrolled."""
        buf = window.buffer
        text = buf.text
        width, truncate = self.wrap_params(window)
        line = text.pos_to_line(window.start.pos)
        if rows >= 0:
            line, row, short = self.advance(buf, line, window.start_row, rows, width, truncate)
        else:
            line, row, short = self.retreat(buf, line, window.start_row, -rows, width, truncate)
        window.start.set(text.line_start(line))
        window.start_row = row
        window.keep_start = True
        return abs(rows) - short

    def point_into_view(self, window, point):
        """The position nearest ``point`` that the window, as now scrolled, shows."""
        buf = window.buffer
        text = buf.text
        width, truncate = self.wrap_params(window)
        height = max(1, window.body_h)
        top = (text.pos_to_line(window.start.pos), window.start_row)
        margin = min(int(self.ed.var('scroll_margin')), (height - 1) // 2)
        line, index = text.pos_to_linecol(point)
        row = bisect_right(self.row_starts(buf, line, width, truncate), index) - 1
        distance = self.row_distance(buf, top, (line, row), width, truncate, 4 * height + 8)
        if distance is not None and margin <= distance < height - margin:
            return point
        if distance is not None and distance < margin:
            target = margin
        else:
            target = height - 1 - margin
        line, row, _ = self.advance(buf, top[0], top[1], target, width, truncate)
        starts = self.row_starts(buf, line, width, truncate)
        return buf.clamp(text.line_start(line) + starts[row])

    # -- keeping point on the screen -----------------------------------------------

    def _scroll_to_point(self, w, pl, pc, height, width, truncate):
        buf = w.buffer
        text = buf.text
        first, last = self._limits(buf)
        prow = bisect_right(self.row_starts(buf, pl, width, truncate), pc) - 1
        target = (pl, prow)
        top_line = max(first, min(text.pos_to_line(w.start.pos), last))
        hidden = fold.hidden(buf)
        if hidden.is_hidden(top_line):
            top_line = hidden.head_of(top_line)
        top_row = min(w.start_row, len(self.row_starts(buf, top_line, width, truncate)) - 1)
        top = (top_line, top_row)
        margin = min(int(self.ed.var('scroll_margin')), (height - 1) // 2)
        if w.recenter is not None:
            wanted = w.recenter if w.recenter >= 0 else height + w.recenter
            wanted = max(0, min(wanted, height - 1))
            w.recenter = None
            top = self.retreat(buf, pl, prow, wanted, width, truncate)[:2]
        else:
            distance = self.row_distance(buf, top, target, width, truncate, 3 * height)
            if distance is not None and margin <= distance < height - margin:
                pass
            elif not self.ed.var('scroll_conservatively') or distance is None \
                    or distance < -height or distance >= 2 * height:
                top = self.retreat(buf, pl, prow, height // 2, width, truncate)[:2]
            elif distance < margin:
                top = self.retreat(buf, pl, prow, margin, width, truncate)[:2]
            else:
                top = self.retreat(buf, pl, prow, height - 1 - margin, width, truncate)[:2]
        w.keep_start = False
        w.start.set(text.line_start(top[0]))
        w.start_row = top[1]
        return top

    # -- the pieces of a row -------------------------------------------------------

    def _layers(self, w, buf, line, bol, ltext, info, region, face_overlays, show_trailing):
        """Highlights over line ``line``: ``(start, end, face, priority)``, lowest first."""
        n = len(ltext)
        end = bol + n
        layers = []
        if info.escapes:
            layers.extend((k, k + 1, 'escape-glyph', 5) for k in info.escapes)
        if show_trailing and n and ltext[-1] in ' \t':
            layers.append((len(ltext.rstrip(' \t')), n, 'trailing-whitespace', 3))
        for overlay in face_overlays:
            s, e = overlay.start, overlay.end
            if s < end + 1 and e > bol and s < e:
                layers.append((max(0, s - bol), min(n, e - bol), overlay.props['face'],
                               overlay.props.get('priority', 10)))
        if region is not None and region[0] < end and region[1] > bol:
            layers.append((max(0, region[0] - bol), min(n, region[1] - bol), 'region', 40))
        for highlighter in highlighters:
            found = highlighter(w, buf, line, bol, ltext)
            if found:
                layers.extend(found)
        if len(layers) > 1:
            layers.sort(key=lambda layer: layer[3])
        return layers

    @staticmethod
    def _text_runs(runs, ltext, info, spans, layers, a, b, base):
        """Append the runs showing ``ltext[a:b]``; return the cells they take."""
        if a >= b:
            return 0
        widths = info.widths
        merge = faces.merge
        if widths is None:
            def chunk(s, e):
                return ltext[s:e]
        else:
            escapes = info.escapes

            def chunk(s, e):
                out = []
                for k in range(s, e):
                    c = ltext[k]
                    if c == '\t':
                        out.append(' ' * widths[k])
                    elif k in escapes:
                        out.append(escapes[k])
                    else:
                        out.append(c)
                return ''.join(out)
        if not layers:
            pos = a
            for s, e, face in spans or ():
                if e <= a:
                    continue
                if s >= b:
                    break
                if s < a:
                    s = a
                if e > b:
                    e = b
                if s > pos:
                    runs.append((base, chunk(pos, s)))
                runs.append((merge(base, face), chunk(s, e)))
                pos = e
            if pos < b:
                runs.append((base, chunk(pos, b)))
        else:
            cuts = {a, b}
            for layer in layers:
                if a < layer[0] < b:
                    cuts.add(layer[0])
                if a < layer[1] < b:
                    cuts.add(layer[1])
            for span in spans or ():
                if a < span[0] < b:
                    cuts.add(span[0])
                if a < span[1] < b:
                    cuts.add(span[1])
            cuts = sorted(cuts)
            si = 0
            nspans = len(spans) if spans else 0
            for i in range(len(cuts) - 1):
                s, e = cuts[i], cuts[i + 1]
                style_ = base
                while si < nspans and spans[si][1] <= s:
                    si += 1
                if si < nspans and spans[si][0] <= s:
                    style_ = merge(style_, spans[si][2])
                for ls, le, face, _ in layers:
                    if ls <= s and le >= e:
                        style_ = merge(style_, face)
                runs.append((style_, chunk(s, e)))
        return b - a if widths is None else info.cols[b] - info.cols[a]

    def _put(self, y, runs):
        if 0 <= y < len(self.rows):
            escaped, plain = render(runs)
            self.rows[y].append(escaped)
            self.plain[y].append(plain)

    # -- a window ------------------------------------------------------------------

    def draw_window(self, w):
        ed = self.ed
        self.geometry(w)
        w.cursor = None
        w.rows = []
        default = faces.style()
        if w.body_h > 0 and w.text_w >= 2:
            try:
                self._draw_body(w)
            except Exception as error:      # a bug in drawing must not lose the user's work
                ed.report_error(error, 'redisplay')
                del w.rows[:]
                w.cursor = None
                for i in range(w.body_h):
                    self.rows[w.y + i] = self.rows[w.y + i][:self._marks[w.y + i]]
                    self.plain[w.y + i] = self.plain[w.y + i][:self._marks[w.y + i]]
                    self._put(w.y + i, fit([(faces.style('error'),
                                             f' redisplay: {type(error).__name__}: {error}'
                                             if i == 0 else '')], w.w, default))
        else:
            for i in range(w.body_h):
                self._put(w.y + i, [(default, ' ' * w.w)])
        if w.has_mode_line and w.h > 0:
            from . import modeline
            try:
                runs = modeline.render(ed, w, w.w)
            except Exception as error:
                ed.report_error(error, 'mode line')
                runs = fit([(faces.style('mode-line'), f' {w.buffer.name}')], w.w,
                           faces.style('mode-line'))
            self._put(w.y + w.h - 1, runs)

    def _draw_body(self, w):
        ed = self.ed
        buf = w.buffer
        text = buf.text
        height, text_w, gutter_w = w.body_h, w.text_w, w.gutter_w
        width = text_w - 1
        selected = w is ed.selected_window
        point = ed.window_point(w)
        truncate = bool(buf.get('truncate_lines'))
        hidden = fold.hidden(buf)
        first_line, last_line = self._limits(buf)
        pl, pc = text.pos_to_linecol(max(buf.point_min, min(point, buf.point_max)))
        if hidden.is_hidden(pl):
            pl = hidden.head_of(pl)
            pc = text.line_length(pl)
        self._marks = {y: len(self.rows[y]) for y in range(w.y, w.y + height)}
        line, sub = self._scroll_to_point(w, pl, pc, height, width, truncate)

        hscroll = 0
        if truncate:
            col = self._col(buf.line_info(pl), pc)
            hscroll = w.hscroll
            if col < hscroll:
                hscroll = max(0, col - width // 3)
            elif col >= hscroll + width:
                hscroll = col - width + 1 + min(width // 3, 10)
            w.hscroll = hscroll

        fontlock.fontify(buf, line, min(last_line, line + height))
        region = None
        if buf.region_active() and (selected or ed.var('highlight_nonselected_windows')):
            region = tuple(sorted((buf.mark.pos, point)))
            if region[0] == region[1]:
                region = None
        hl = selected and bool(buf.get('hl_line_mode'))
        show_trailing = bool(buf.get('show_trailing_whitespace'))
        numbers = gutter_w - 2 if gutter_w > 1 else 0
        merge = faces.merge
        s_default = faces.style()
        s_hl = faces.style('hl-line')
        s_num = faces.style('line-number')
        s_num_cur = faces.style('line-number-current-line')
        s_fringe = faces.style('fringe')
        s_cont = faces.style('continuation')
        s_mark = faces.style('fold-marker')
        s_fold = faces.style('fold')
        face_overlays = [o for o in buf.overlays
                         if 'face' in o.props and o.props.get('kind') != 'fold']
        rows = w.rows
        y = w.y
        end_y = w.y + height
        blank_tail = [(s_default, ' ' * w.minimap_w)] if w.minimap_w else []

        while y < end_y:
            if line > last_line:
                self._put(y, [(s_default, ' ' * (gutter_w + text_w)), *blank_tail])
                rows.append((-1, 0, 0))
                y += 1
                continue
            info = buf.line_info(line)
            if info.spans is None:
                fontlock.fontify(buf, line, line)
            ltext = text.line(line)
            n = len(ltext)
            bol = text.line_start(line)
            lo = max(0, buf.point_min - bol) if line == first_line else 0
            hi = max(lo, min(n, buf.point_max - bol)) if line == last_line else n
            starts = self.row_starts(buf, line, width, truncate)
            nrows = len(starts)
            layers = self._layers(w, buf, line, bol, ltext, info, region, face_overlays,
                                  show_trailing)
            on_point = line == pl
            base = s_hl if on_point and hl else s_default
            folded = hidden.fold_headed_by(line)
            prow = bisect_right(starts, pc) - 1 if on_point else -1
            for r in range(sub, nrows):
                if y >= end_y:
                    break
                last_row = r == nrows - 1
                runs = []
                if gutter_w:
                    if folded and r == 0:
                        runs.append((s_mark, '▸'))
                    else:
                        runs.append((merge(base, 'fringe') if on_point and hl else s_fringe, ' '))
                    if numbers:
                        label = f'{line + 1:>{numbers}} ' if r == 0 else ' ' * (numbers + 1)
                        runs.append((s_num_cur if on_point else s_num, label))
                lead = 0
                if truncate:
                    if info.cols is None:
                        a, b = min(hscroll, n), min(n, hscroll + width)
                    else:
                        cols = info.cols
                        a = min(bisect_left(cols, hscroll), n)
                        b = max(a, bisect_right(cols, hscroll + width) - 1)
                        lead = min(width, cols[a] - hscroll) if a < n else 0
                    origin = hscroll
                else:
                    a = starts[r]
                    b = starts[r + 1] if not last_row else n
                    origin = self._col(info, a)
                a = max(a, lo)
                b = max(a, min(b, hi))
                if lead:
                    runs.append((base, ' ' * lead))
                used = lead + self._text_runs(runs, ltext, info, info.spans, layers, a, b, base)
                if folded and last_row and used < width:
                    badge, cells = clip(f' ⋯ {folded[1] - folded[0] + 1} lines ', width - used)
                    runs.append((s_fold, badge))
                    used += cells
                tail = base
                if region is not None and last_row and region[0] <= bol + n < region[1] \
                        and not folded:
                    tail = merge(base, 'region')
                if used < width:
                    runs.append((tail, ' ' * (width - used)))
                if not last_row:
                    runs.append((merge(base, 'continuation'), '↩'))
                elif truncate and b < hi:
                    runs.append((merge(base, 'continuation'), '…'))
                else:
                    runs.append((tail, ' '))
                runs.extend(blank_tail)
                self._put(y, runs)
                rows.append((line, a, b))
                if on_point and w.cursor is None and (r == prow or truncate):
                    cx = self._col(info, min(pc, n)) - origin
                    if 0 <= cx <= width:
                        w.cursor = (y, w.x + gutter_w + cx)
                y += 1
            line = hidden.next(line)
            sub = 0

    def draw_tree(self, node):
        from .window import Window
        if isinstance(node, Window):
            self.draw_window(node)
            return
        border = faces.style('vertical-border')
        for i, child in enumerate(node.children):
            self.draw_tree(child)
            if node.horizontal and i + 1 < len(node.children):
                for y in range(node.y, node.y + node.h):
                    self._put(y, [(border, '│')])

    # -- the minibuffer and the echo area ------------------------------------------

    def _mini_rows(self, width):
        """Rows for the bottom of the frame, and where the cursor goes in them."""
        ed = self.ed
        default = faces.style()
        rows = []
        cursor = None
        if ed.minibuffer_depth > 0:
            w = ed.frame.minibuffer_window
            buf = w.buffer
            text = buf.text
            prompt_style = faces.style('minibuffer-prompt')
            point = ed.window_point(w)
            pl, pc = text.pos_to_linecol(point)
            region = None
            if buf.region_active() and w is ed.selected_window:
                region = tuple(sorted((buf.mark.pos, point)))
            for line in range(text.line_count):
                ltext = text.line(line)
                info = buf.line_info(line)
                n = len(ltext)
                bol = text.line_start(line)
                prefix = (w.prompt or '') if line == 0 else ''
                if text_width(prefix) > width // 2:
                    prefix = '…' + prefix[-(max(1, width // 2) - 1):]
                pw = text_width(prefix)
                avail = max(1, width - pw - 1)
                col = self._col(info, pc) if line == pl else 0
                h = 0 if col < avail else col - avail + 1
                if info.cols is None:
                    a, b = min(h, n), min(n, h + avail)
                else:
                    a = min(bisect_left(info.cols, h), n)
                    b = max(a, bisect_right(info.cols, h + avail) - 1)
                runs = [(prompt_style, prefix)] if prefix else []
                layers = []
                if region and region[0] < bol + n and region[1] > bol:
                    layers.append((max(0, region[0] - bol), min(n, region[1] - bol), 'region', 40))
                if info.escapes:
                    layers.extend((k, k + 1, 'escape-glyph', 5) for k in info.escapes)
                self._text_runs(runs, ltext, info, info.spans, layers, a, b, default)
                if line == text.line_count - 1 and ed.minibuffer_message:
                    runs.append((faces.style('shadow'), f' [{ed.minibuffer_message}]'))
                if line == pl:
                    cursor = (len(rows), pw + col - h)
                rows.append(fit(runs, width, default))
            ui = ed.completion_rows(width) if ed.completion_rows else None
            if ui:
                rows.extend(fit(r, width, default) for r in ui)
        else:
            message = ed.echo
            if message:
                runs_list = message if isinstance(message, list) else None
                if runs_list is not None:
                    rows.append(fit([(faces.style(face) if face else default, part)
                                     for part, face in runs_list], width, default))
                else:
                    for part in str(message).split('\n'):
                        part = part.expandtabs(8)
                        while True:
                            piece, cells = clip(part, width)
                            rows.append(fit([(default, piece)], width, default))
                            part = part[len(piece):]
                            if not part or not piece:
                                break
                if ed.cursor_in_echo and rows:
                    used = text_width(''.join(t for _, t in rows[-1]).rstrip(' '))
                    cursor = (len(rows) - 1, min(width - 1, used + (1 if used else 0)))
            if not rows:
                rows.append([(default, ' ' * width)])
        limit = max(1, int(ed.frame.height * float(ed.var('max_mini_window_height'))))
        limit = max(1, min(limit, ed.frame.height - 2))
        if len(rows) > limit:
            if ed.minibuffer_depth > 0:
                rows = rows[:limit]
            else:
                rows = rows[:limit - 1] + [fit([(faces.style('shadow'), '…')], width, default)]
            if cursor and cursor[0] >= limit:
                cursor = None
        return rows, cursor

    # -- one redisplay ---------------------------------------------------------------

    def redisplay(self):
        ed = self.ed
        tty = ed.tty
        frame = ed.frame
        size = (tty.width, tty.height)
        if size != self.size:
            self.size = size
            frame.resize(*size)
            self.full = True
        if faces.generation != self.face_generation:
            self.face_generation = faces.generation
            self.full = True
        width, height = size
        mini, mini_cursor = self._mini_rows(width)
        if frame.mini_height != len(mini):
            frame.mini_height = len(mini)
            frame.layout()
        self.rows = [[] for _ in range(height)]
        self.plain = [[] for _ in range(height)]
        self._marks = {}
        self.draw_tree(frame.root)
        mw = frame.minibuffer_window
        for i, runs in enumerate(mini[:mw.h]):
            self._put(mw.y + i, runs)

        out = []
        if self.full or len(self.screen) != height:
            out.append(faces.sgr(faces.style()) + '\x1b[2J')
            self.screen = [None] * height
            self._cursor_sent = None
        for y in range(height):
            row = ''.join(self.rows[y])
            if row != self.screen[y]:
                out.append(tty.move(y, 0) + row)
                self.screen[y] = row
            self.plain[y] = ''.join(self.plain[y])
        full, self.full = self.full, False
        for hook in list(after_draw):
            try:
                hook(self, out, full)
            except Exception as error:
                ed.report_error(error, 'redisplay')

        cursor = None
        if ed.minibuffer_depth > 0 and ed.selected_window is mw:
            if mini_cursor is not None:
                cursor = (mw.y + mini_cursor[0], mini_cursor[1])
        elif ed.cursor_in_echo and mini_cursor is not None:
            cursor = (mw.y + mini_cursor[0], mini_cursor[1])
        else:
            cursor = ed.selected_window.cursor
        self.cursor = cursor
        shape = _CURSOR.get(ed.var('cursor_type'), 6) - (1 if ed.var('cursor_blink') else 0)
        color = faces.attribute('cursor', 'bg')
        look = (shape, color)
        if look != self._cursor_sent:
            out.append(f'\x1b[{shape} q')
            if color:
                out.append(f'\x1b]12;{color}\x1b\\')
            self._cursor_sent = look
        out.append('\x1b[0m')
        if cursor is not None and 0 <= cursor[0] < height and 0 <= cursor[1] < width:
            out.append(tty.move(*cursor) + '\x1b[?25h')
        else:
            out.append('\x1b[?25l')
        tty.write(''.join(out))
        tty.flush()

    # -- where things are --------------------------------------------------------------

    def locate(self, x, y):
        """``(window, area, position)`` for a cell of the screen.

        ``area`` is 'text', 'gutter', 'minimap', 'mode-line', 'divider' or
        'minibuffer'; ``position`` is the buffer position shown at or nearest the
        cell, or None where that means nothing.
        """
        frame = self.ed.frame
        w = frame.window_at(x, y)
        if w is None:
            return (None, 'divider', None) if frame.divider_at(x, y) else (None, None, None)
        if w.is_minibuffer:
            return w, 'minibuffer', None
        row = y - w.y
        if w.has_mode_line and row == w.h - 1:
            return w, 'mode-line', None
        buf = w.buffer
        if row >= len(w.rows):
            return w, 'text', None
        line, a, b = w.rows[row]
        if w.minimap_w and x >= w.x + w.w - w.minimap_w:
            return w, 'minimap', None
        if line < 0:
            return w, 'text' if x >= w.text_x else 'gutter', buf.point_max
        bol = buf.text.line_start(line)
        if x < w.text_x:
            return w, 'gutter', bol + a
        info = buf.line_info(line)
        truncate = bool(buf.get('truncate_lines'))
        origin = w.hscroll if truncate else self._col(info, a)
        index = buf.column_to_index(line, origin + (x - w.text_x))
        return w, 'text', buf.clamp(bol + max(a, min(index, b)))
