# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Incremental search and replacement.

``C-s`` and ``C-r`` search as you type, the way Emacs does: each character
extends the search, ``DEL`` takes back the last step, ``C-s`` again moves to
the next match and wraps at the end, ``RET`` stays where the search arrived
and ``C-g`` goes back to where it began.  Every match on screen is
highlighted, and the prompt counts them.  Any key that is not a search key
ends the search and then does what it normally does.

Regular expressions are Python's, in searches and in ``query-replace-regexp``
alike; a replacement may use ``\\1`` and ``\\g<name>``, and ``\\&`` for the
whole match.

A search is a loop reading events inside one command, so a keyboard macro
records and replays it key for key.
"""

import re
import time

from . import fold
from .command import command
from .editor import ed, global_map
from .errors import EditorError, Quit
from .keys import self_insert_text
from .minibuffer import add_to_history, histories, read_from_minibuffer
from .variables import defvar

defvar('case_fold_search', True,
       'Whether searches ignore case when the search string has no capitals.')
defvar('case_replace', True,
       'Whether a replacement takes on the capitalization of what it replaces.')
defvar('isearch_lazy_count', True, 'Whether incremental search counts the matches.')
defvar('search_whitespace_regexp', None,
       'If a string, a space typed in a plain incremental search matches this pattern.')

_COUNT_BUDGET = 0.05        # seconds spent counting matches before giving up on a number


def _folds(string, setting):
    """Whether a search for ``string`` ignores case."""
    if setting is not None:
        return setting
    return bool(ed.var('case_fold_search')) and string == string.lower()


def compile_search(string, regexp, fold_case):
    source = string if regexp else re.escape(string)
    lax = ed.var('search_whitespace_regexp')
    if not regexp and lax:
        source = source.replace('\\ ', lax)
    flags = re.MULTILINE | (re.IGNORECASE if fold_case else 0)
    return re.compile(source, flags)


class Isearch:
    def __init__(self, forward, regexp):
        self.buffer = ed.current_buffer
        self.window = ed.selected_window
        self.forward = forward
        self.regexp = regexp
        self.string = ''
        self.case = None                # None: decide from the string
        self.origin = self.buffer.pt
        self.origin_start = (self.window.start.pos, self.window.start_row)
        self.match = None               # (start, end) of the current match
        self.success = True
        self.wrapped = False
        self.error = None
        self.stack = []
        self.pattern = None
        self.count = None
        self._count_key = None

    # -- state ---------------------------------------------------------------------------

    def snapshot(self):
        return (self.string, self.forward, self.match, self.success, self.wrapped,
                self.buffer.pt, self.error)

    def restore(self, saved):
        (self.string, self.forward, self.match, self.success, self.wrapped, point,
         self.error) = saved
        self.buffer.goto_char(point)
        self._compile()

    def _compile(self):
        self.pattern = None
        self.error = None
        if not self.string:
            return
        try:
            self.pattern = compile_search(self.string, self.regexp,
                                          _folds(self.string, self.case))
        except re.error as error:
            self.error = str(error)

    def prompt(self):
        parts = []
        if not self.success:
            parts.append('failing')
        if self.wrapped:
            parts.append('wrapped')
        if self.regexp:
            parts.append('regexp')
        parts.append('I-search')
        if not self.forward:
            parts.append('backward')
        text = ' '.join(parts)
        text = text[0].upper() + text[1:]
        count = ''
        if self.count is not None and self.string:
            index, total = self.count
            count = f'{index}/{total} ' if total is not None else ''
        tail = f' [{self.error}]' if self.error else ''
        return [(count, 'completions-count'), (text + ': ', 'minibuffer-prompt'),
                (self.string, None if self.success else 'isearch-fail'), (tail, 'error')]

    # -- searching -------------------------------------------------------------------------

    def search(self, start):
        """Find the next match from ``start`` in the current direction."""
        b = self.buffer
        if self.pattern is None:
            self.success = not self.string and self.error is None
            if not self.string:
                self.match = None
            return self.success
        text = b.text.snapshot()
        if self.forward:
            found = self.pattern.search(text, max(b.point_min, start), b.point_max)
        else:
            found = b._last_match(self.pattern, b.point_min, min(b.point_max, start))
        if found is None:
            self.success = False
            return False
        self.success = True
        self.match = found.span()
        b.goto_char(found.end() if self.forward else found.start())
        if fold.hidden(b):
            fold.unfold_at(b, found.start() + 1)
        return True

    def extend(self, text):
        self.stack.append(self.snapshot())
        self.string += text
        self._compile()
        b = self.buffer
        if self.match is not None and self.success:
            start = self.match[0] if self.forward else min(b.point_max,
                                                            self.match[1] + len(text))
        else:
            start = b.pt
        if not self.search(start):
            self.fail()

    def fail(self):
        ed.ding()

    def repeat(self, forward):
        b = self.buffer
        if not self.string:
            previous = histories['regexp-search' if self.regexp else 'search']
            if not previous:
                raise EditorError('No previous search string')
            self.stack.append(self.snapshot())
            self.forward = forward
            self.string = previous[-1]
            self._compile()
            if not self.search(b.pt):
                self.fail()
            return
        self.stack.append(self.snapshot())
        if forward != self.forward:
            self.forward = forward
            self.success = True
            start = b.pt
        elif not self.success:
            self.wrapped = True
            start = b.point_min if forward else b.point_max
        elif self.match is not None and self.match[0] == self.match[1]:
            start = b.pt + (1 if forward else -1)       # step over an empty match
        else:
            start = b.pt
        if not self.search(start):
            self.fail()

    def recount(self):
        """Work out which match this is and how many there are."""
        if not ed.var('isearch_lazy_count') or self.pattern is None or self.match is None \
                or not self.success:
            self.count = None
            return
        key = (self.pattern, self.buffer.text.version, self.match)
        if key == self._count_key:
            return
        self._count_key = key
        text = self.buffer.text.snapshot()
        deadline = time.monotonic() + _COUNT_BUDGET
        total = index = 0
        here = self.match[0]
        for n, found in enumerate(self.pattern.finditer(text, self.buffer.point_min,
                                                        self.buffer.point_max)):
            total += 1
            if found.start() <= here:
                index = total
            if not n % 512 and time.monotonic() > deadline:
                self.count = (index, None)      # too many to count while typing
                return
        self.count = (index, total)

    def highlights(self, window, buffer, line, bol, ltext):
        if buffer is not self.buffer or self.pattern is None or not self.string:
            return None
        found = []
        current = self.match if self.success else None
        for match in self.pattern.finditer(ltext):
            if match.start() == match.end():
                continue
            if current and current[0] == bol + match.start():
                continue
            found.append((match.start(), match.end(), 'lazy-highlight', 60))
        if current and current[0] <= bol + len(ltext) and current[1] >= bol:
            found.append((max(0, current[0] - bol), min(len(ltext), current[1] - bol),
                          'isearch', 70))
        return found

    # -- the loop --------------------------------------------------------------------------

    def run(self):
        b = self.buffer
        ed.highlighters.append(self.highlights)
        try:
            while True:
                self.recount()
                ed.echo = self.prompt()
                ed.cursor_in_echo = True
                event = ed.read_key()
                key = event.key
                if key == 'C-s':
                    self.repeat(True)
                elif key == 'C-r':
                    self.repeat(False)
                elif key == 'DEL':
                    if not self.stack:
                        ed.ding()
                    else:
                        self.restore(self.stack.pop())
                elif key in ('RET', 'C-m'):
                    break
                elif key == 'C-g':
                    if not self.success and self.stack:
                        while self.stack and not self.success:
                            self.restore(self.stack.pop())
                        continue
                    b.goto_char(self.origin)
                    self.window.start.set(self.origin_start[0])
                    self.window.start_row = self.origin_start[1]
                    raise Quit()
                elif key == 'C-w':
                    start = b.pt if self.forward else (self.match[1] if self.match else b.pt)
                    found = re.compile(r'\W*\w+|\W+').match(b.text.snapshot(), start,
                                                             b.eol(start))
                    if found:
                        self.extend(found.group().lower() if _folds(self.string, self.case)
                                    else found.group())
                elif key == 'C-y':
                    if ed.kill_ring:
                        self.extend(ed.kill_ring[0])
                elif key == '<paste>':
                    self.extend((event.text or '').replace('\r', ''))
                elif key == 'M-c':
                    self.case = not _folds(self.string, self.case)
                    self._compile()
                    self.search(self.match[0] if self.match and self.forward else b.pt)
                elif key == 'M-r':
                    self.regexp = not self.regexp
                    self._compile()
                    self.search(self.match[0] if self.match and self.forward else b.pt)
                elif key == 'M-e':
                    edited = read_from_minibuffer('Edit search string: ', self.string,
                                                  history='search')
                    self.stack.append(self.snapshot())
                    self.string = edited
                    self._compile()
                    if not self.search(self.origin if self.forward else b.pt):
                        self.fail()
                elif key in ('M-p', 'M-n'):
                    entries = histories['regexp-search' if self.regexp else 'search']
                    if not entries:
                        ed.ding()
                        continue
                    index = getattr(self, '_ring', len(entries)) + (-1 if key == 'M-p' else 1)
                    self._ring = index % len(entries)
                    self.stack.append(self.snapshot())
                    self.string = entries[self._ring]
                    self._compile()
                    if not self.search(self.origin):
                        self.fail()
                elif key == 'C-q':
                    following = ed.read_event()
                    literal = {'RET': '\r', 'TAB': '\t', 'C-j': '\n', 'SPC': ' '}.get(
                        following.key, following.key)
                    if len(literal) == 1:
                        self.extend(literal)
                elif key == 'C-j':
                    self.extend('\n')
                elif key == 'M-%':
                    self.finish()
                    b.goto_char(self.match[0] if self.match and self.forward else b.pt)
                    replacement = read_from_minibuffer(
                        f'Query replace {self.string} with: ', history='replace')
                    query_replace(self.string, replacement, regexp=self.regexp)
                    return
                elif self_insert_text(key) is not None:
                    self.extend(self_insert_text(key))
                else:
                    ed.unread.appendleft(event)     # not a search key: do what it normally does
                    break
            self.finish()
        finally:
            if self.highlights in ed.highlighters:
                ed.highlighters.remove(self.highlights)
            ed.echo = None
            ed.cursor_in_echo = False

    def finish(self):
        b = self.buffer
        if self.string:
            add_to_history('regexp-search' if self.regexp else 'search', self.string)
        if b.pt != self.origin and not b.region_active():
            b.push_mark(self.origin)
            ed.message('Mark saved where search started', log=False)


@command
def isearch_forward():
    """Search forward as you type.  ``C-s`` again finds the next match."""
    Isearch(True, False).run()


@command
def isearch_backward():
    """Search backward as you type.  ``C-r`` again finds the previous match."""
    Isearch(False, False).run()


@command
def isearch_forward_regexp():
    """Search forward as you type, for a regular expression."""
    Isearch(True, True).run()


@command
def isearch_backward_regexp():
    """Search backward as you type, for a regular expression."""
    Isearch(False, True).run()


@command
def isearch_forward_symbol_at_point():
    """Search forward for the symbol at point."""
    b = ed.current_buffer
    line = b.line_at()
    start = b.text.line_start(line)
    for found in re.finditer(r'[\w.]*\w', b.text.line(line)):
        if start + found.start() <= b.pt <= start + found.end():
            search = Isearch(True, True)
            b.goto_char(start + found.start())
            search.origin = b.pt
            search.extend(r'\b' + re.escape(found.group()) + r'\b')
            search.run()
            return
    raise EditorError('No symbol at point')


# -- replacing -------------------------------------------------------------------------------

def _template(replacement, regexp):
    """A function of a match giving its replacement text."""
    if not regexp:
        return lambda match: replacement
    converted = replacement.replace('\\&', '\\g<0>')
    return lambda match: match.expand(converted)


def _match_case(original, replacement):
    """``replacement`` capitalized the way ``original`` is."""
    letters = [c for c in original if c.isalpha()]
    if not letters:
        return replacement
    if all(c.isupper() for c in letters) and len(letters) > 1:
        return replacement.upper()
    if letters[0].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _replacer(source, replacement, regexp):
    fold_case = _folds(source, None)
    pattern = compile_search(source, regexp, fold_case)
    expand = _template(replacement, regexp)
    adapt = fold_case and ed.var('case_replace') and replacement == replacement.lower()

    def text_for(match):
        new = expand(match)
        return _match_case(match.group(), new) if adapt else new

    return pattern, text_for


def replace_all(source, replacement, regexp=False, start=None, end=None):
    """Replace every match between two positions (point and the end of the
    buffer if not given).  Returns the number replaced."""
    b = ed.current_buffer
    start = b.pt if start is None else start
    end = b.point_max if end is None else end
    pattern, text_for = _replacer(source, replacement, regexp)
    text = b.text.snapshot()
    pieces = []
    at = start
    count = 0
    for match in pattern.finditer(text, start, end):
        if match.start() == match.end() and match.start() >= end:
            break
        pieces.append(text[at:match.start()])
        pieces.append(text_for(match))
        at = match.end()
        count += 1
    if count:
        pieces.append(text[at:end])
        new = ''.join(pieces)
        b.replace_region(start, end, new)
        b.goto_char(start + len(new))
    return count


def _read_replacement(what, regexp):
    b = ed.current_buffer
    history = 'regexp-search' if regexp else 'search'
    previous = getattr(ed, 'last_replacement', None)
    hint = f' (default {previous[0]} → {previous[1]})' if previous else ''
    scope = ' in region' if b.region_active() else ''
    source = read_from_minibuffer(f'{what}{scope}{hint}: ', history=history)
    if not source:
        if previous is None:
            raise EditorError('Nothing to replace')
        return [*previous]
    replacement = read_from_minibuffer(f'{what} {source} with: ', history='replace')
    ed.last_replacement = (source, replacement)
    return [source, replacement]


def _bounds():
    b = ed.current_buffer
    if b.region_active():
        start, end = b.region()
        b.mark_active = False
        return start, end
    return b.pt, b.point_max


@command(interactive=lambda: _read_replacement('Replace string', False))
def replace_string(source, replacement):
    """Replace every later occurrence of a string, or every one in the region."""
    start, end = _bounds()
    count = replace_all(source, replacement, False, start, end)
    ed.message(f'Replaced {count} occurrence{"s" * (count != 1)}', log=False)


@command(interactive=lambda: _read_replacement('Replace regexp', True))
def replace_regexp(source, replacement):
    """Replace every later match of a regular expression, or every one in the region."""
    start, end = _bounds()
    count = replace_all(source, replacement, True, start, end)
    ed.message(f'Replaced {count} occurrence{"s" * (count != 1)}', log=False)


class _Done(Exception):
    """The user has finished with a query-replace."""


_HELP = ('y or SPC replace, n or DEL skip, ! replace all, . replace and stop, '
         ', replace and stay, ^ go back, e edit the replacement, q or RET stop')


def query_replace(source, replacement, regexp=False, start=None, end=None):
    """Replace matches one at a time, asking about each."""
    from .buffer import Marker
    b = ed.current_buffer
    if start is None:
        start, end = _bounds()
    pattern, text_for = _replacer(source, replacement, regexp)
    limit = Marker(b, end, True)
    b.goto_char(start)
    origin = start
    count = 0
    history = []
    highlight = b.make_overlay(start, start, face='query-replace', priority=80)
    lazy = Isearch(True, regexp)
    lazy.string, lazy.pattern, lazy.match = source, pattern, None
    ed.highlighters.append(lazy.highlights)
    replace_rest = False
    try:
        while True:
            match = pattern.search(b.text.snapshot(), b.pt, limit.pos)
            if match is None or (match.start() == match.end() and match.start() >= limit.pos):
                break
            if match.start() == match.end() and history and history[-1] == match.start():
                if b.pt >= limit.pos:
                    break
                b.goto_char(b.pt + 1)
                continue
            new = text_for(match)
            if replace_rest:
                b.replace_region(match.start(), match.end(), new)
                b.goto_char(match.start() + len(new))
                count += 1
                if match.start() == match.end():
                    history.append(match.start())
                continue
            highlight.move(match.start(), match.end())
            lazy.match = match.span()
            b.goto_char(match.end())
            fold.unfold_at(b, match.start() + 1)
            shown = new.replace('\n', '⏎')
            while True:
                event = ed.read_key([(f'Query replacing {source} with {shown}: ',
                                      'minibuffer-prompt'), ('(? for help) ', 'shadow')])
                key = event.key
                if key in ('y', 'Y', 'SPC', ',', '.', '!'):
                    history.append(match.start())
                    b.replace_region(match.start(), match.end(), new)
                    b.goto_char(match.start() + len(new))
                    count += 1
                    if key == '!':
                        replace_rest = True
                    elif key == '.':
                        raise _Done
                    elif key == ',':
                        highlight.move(match.start(), match.start() + len(new))
                        ed.read_key([('Replaced; any key continues ', 'shadow')])
                    break
                if key in ('n', 'N', 'DEL', '<delete>'):
                    history.append(match.start())
                    if match.start() == match.end():
                        b.goto_char(min(limit.pos, match.end() + 1))
                    break
                if key in ('q', 'Q', 'RET', 'C-m'):
                    raise _Done
                if key == '^':
                    if history:
                        b.goto_char(history.pop())
                        break
                    ed.ding()
                    continue
                if key == 'e':
                    replacement = read_from_minibuffer('Edit replacement string: ',
                                                       replacement, history='replace')
                    pattern, text_for = _replacer(source, replacement, regexp)
                    new = text_for(match)
                    shown = new.replace('\n', '⏎')
                    continue
                if key == 'C-l':
                    ed.selected_window.recenter = max(0, ed.selected_window.body_h // 2)
                    continue
                if key == 'C-g':
                    raise Quit()
                if key == '?':
                    ed.message(_HELP, log=False)
                    ed.read_key(_HELP)
                    continue
                if event.is_mouse:
                    continue
                ed.unread.appendleft(event)
                raise _Done
    except _Done:
        pass
    finally:
        highlight.delete()
        limit.delete()
        if lazy.highlights in ed.highlighters:
            ed.highlighters.remove(lazy.highlights)
        ed.echo = None
    if origin != b.pt and not b.region_active():
        b.push_mark(origin)
    ed.message(f'Replaced {count} occurrence{"s" * (count != 1)}', log=False)
    return count


@command(name='query-replace', interactive=lambda: _read_replacement('Query replace', False))
def query_replace_command(source, replacement):
    """Replace some occurrences of a string, asking about each."""
    query_replace(source, replacement, False)


@command(interactive=lambda: _read_replacement('Query replace regexp', True))
def query_replace_regexp(source, replacement):
    """Replace some matches of a regular expression, asking about each."""
    query_replace(source, replacement, True)


def _lines_matching(prompt):
    return [read_from_minibuffer(prompt, history='regexp-search')]


def _filter_lines(pattern, keep):
    b = ed.current_buffer
    start, end = b.region() if b.region_active() else (b.pt, b.point_max)
    first, last = b.line_at(start), b.line_at(max(start, end - 1))
    compiled = compile_search(pattern, True, _folds(pattern, None))
    lines = [b.text.line(i) for i in range(first, last + 1)]
    kept = [line for line in lines if bool(compiled.search(line)) == keep]
    removed = len(lines) - len(kept)
    if removed:
        tail = '\n' if last + 1 < b.text.line_count else ''
        stop = b.text.line_start(last + 1) if tail else b.text.line_end(last)
        b.replace_region(b.text.line_start(first), stop,
                         '\n'.join(kept) + (tail if kept else ''))
        b.goto_char(b.text.line_start(first))
    return removed


@command(interactive=lambda: _lines_matching('Flush lines containing match for regexp: '))
def flush_lines(pattern):
    """Delete the lines after point, or in the region, that match a regular expression."""
    n = _filter_lines(pattern, keep=False)
    ed.message(f'Deleted {n} matching line{"s" * (n != 1)}', log=False)


@command(interactive=lambda: _lines_matching('Keep lines containing match for regexp: '))
def keep_lines(pattern):
    """Delete the lines after point, or in the region, that do not match a regular expression."""
    n = _filter_lines(pattern, keep=True)
    ed.message(f'Deleted {n} line{"s" * (n != 1)}', log=False)


@command(interactive=lambda: _lines_matching('How many matches for regexp: '))
def how_many(pattern):
    """Count the matches of a regular expression after point, or in the region."""
    b = ed.current_buffer
    start, end = b.region() if b.region_active() else (b.pt, b.point_max)
    compiled = compile_search(pattern, True, _folds(pattern, None))
    n = sum(1 for _ in compiled.finditer(b.text.snapshot(), start, end))
    ed.message(f'{n} occurrence{"s" * (n != 1)}', log=False)
    ed.keep_mark_active = True
    return n


for _keys, _name in (
        ('C-s', 'isearch-forward'), ('C-r', 'isearch-backward'),
        ('C-M-s', 'isearch-forward-regexp'), ('C-M-r', 'isearch-backward-regexp'),
        ('M-s .', 'isearch-forward-symbol-at-point'),
        ('M-%', 'query-replace'), ('C-M-%', 'query-replace-regexp')):
    global_map.bind(_keys, _name)
