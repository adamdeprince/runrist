# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Reading from the minibuffer.

``read_from_minibuffer`` shows a prompt at the bottom of the frame, lets the
user edit a line there with all the usual keys, and returns it.  It does so
by running the command loop one level deeper, so to its caller it is an
ordinary function call.

``completing_read`` adds a list of candidates below the line, narrowed as the
user types.  Space-separated words must all occur in a candidate, in any
order; for file names the last component is matched against the directory
before it.  ``RET`` takes the highlighted candidate, ``TAB`` inserts it,
``C-n``/``C-p`` move the highlight, and ``M-RET`` takes exactly what was
typed.
"""

import os
import re
from collections import defaultdict

from . import faces
from .buffer import Buffer
from .command import command, commands
from .errors import AbortRecursiveEdit, EditorError, ExitRecursiveEdit, Quit
from .keymap import Keymap, where_is
from .keys import key_description
from .variables import defvar, variables

defvar('completions_max_height', 12, 'How many candidates the minibuffer shows at once.')
defvar('completion_ignored_extensions',
       ['.pyc', '.pyo', '.o', '.elc', '~', '.DS_Store', '.class', '.so', '.dylib'],
       'File name endings that completion lists last.')
defvar('history_delete_duplicates', True,
       'Whether a history keeps only the latest of equal entries.')

minibuffer_local_map = Keymap('minibuffer-local-map')
minibuffer_local_completion_map = Keymap('minibuffer-local-completion-map',
                                         parent=minibuffer_local_map)
minibuffer_local_filename_map = Keymap('minibuffer-local-filename-map',
                                       parent=minibuffer_local_completion_map)

histories = defaultdict(list)       # name -> entries, oldest first
_stack = []


def _ed():
    from .editor import ed
    return ed


# -- completion tables ---------------------------------------------------------------

class Table:
    """What can be completed, and how a candidate is described."""

    category = None
    require_match = False

    def __init__(self, candidates=(), *, category=None, annotate=None, require_match=False,
                 sort=True):
        self.candidates = candidates
        self.category = category
        self.annotate = annotate
        self.require_match = require_match
        self.sort = sort

    def split(self, text):
        """``(base, pattern)``: the part of the input that is kept as is, and
        the part matched against candidates."""
        return '', text

    def all(self, base):
        found = self.candidates
        return list(found() if callable(found) else found)

    def accepts(self, text):
        """Whether ``text`` is a valid answer as typed."""
        return not self.require_match or text in self.all('')

    def join(self, base, candidate):
        return base + candidate


def expand_file_name(name, directory=None):
    """Absolute, normalized form of a file name given relative to ``directory``."""
    name = os.path.expanduser(name)
    if not os.path.isabs(name):
        name = os.path.join(directory or os.getcwd(), name)
    trailing = name.endswith('/') and name != '/'
    name = os.path.normpath(name)
    return name + '/' if trailing and not name.endswith('/') else name


def abbreviate_file_name(name):
    home = os.path.expanduser('~')
    if name == home:
        return '~'
    if name.startswith(home + '/'):
        return '~' + name[len(home):]
    return name


def substitute_in_file_name(text):
    """Apply the rule that typing ``//`` or ``/~`` starts the name afresh."""
    i = text.rfind('//')
    if i >= 0:
        text = text[i + 1:]
    j = text.rfind('/~')
    if j >= 0:
        text = text[j + 1:]
    return text


class FileTable(Table):
    category = 'file'

    def __init__(self, directory, *, must_match=False, directories_only=False):
        super().__init__(category='file', require_match=must_match)
        self.directory = directory
        self.directories_only = directories_only
        self._listing = (None, [])

    def split(self, text):
        text = substitute_in_file_name(text)
        i = text.rfind('/')
        return text[:i + 1], text[i + 1:]

    def all(self, base):
        directory = expand_file_name(substitute_in_file_name(base) or '.', self.directory)
        if self._listing[0] == directory:
            return self._listing[1]
        names = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        is_dir = False
                    if is_dir:
                        names.append(entry.name + '/')
                    elif not self.directories_only:
                        names.append(entry.name)
        except OSError:
            names = []
        names.sort(key=str.lower)
        self._listing = (directory, names)
        return names

    def accepts(self, text):
        if not self.require_match:
            return True
        return os.path.exists(expand_file_name(substitute_in_file_name(text), self.directory))


def _terms(pattern):
    return [t for t in pattern.split(' ') if t]


def _matcher(term):
    flags = 0 if any(c.isupper() for c in term) else re.IGNORECASE
    return re.compile(re.escape(term), flags)


def filter_candidates(pattern, candidates, *, history=(), is_file=False, ignored=()):
    """Candidates matching ``pattern``, best first."""
    terms = [pattern] if is_file else _terms(pattern)
    if is_file and not pattern.startswith('.'):
        candidates = [c for c in candidates if not c.startswith('.')]
    if not terms or terms == ['']:
        matched = [(0, 0, c) for c in candidates]
        first = None
    else:
        matchers = [_matcher(t) for t in terms]
        first = terms[0].lower()
        matched = []
        for c in candidates:
            position = 0
            for i, m in enumerate(matchers):
                found = m.search(c)
                if found is None:
                    break
                if i == 0:
                    position = found.start()
            else:
                matched.append((position, len(c), c))
    recent = {entry: i for i, entry in enumerate(reversed(list(history)))}

    def rank(item):
        position, length, c = item
        exact = 0 if first is not None and c.lower().rstrip('/') == first else 1
        prefix = 0 if position == 0 else 1
        bad = 1 if ignored and c.endswith(tuple(ignored)) else 0
        return (bad, exact, recent.get(c, len(recent) + 1) if not is_file else 0,
                prefix, position, length if first is not None else 0)

    if first is None and not recent and not ignored:
        return [c for _, _, c in matched]
    matched.sort(key=rank)
    return [c for _, _, c in matched]


class Session:
    """One use of the minibuffer."""

    def __init__(self, prompt, buffer, history, default, table):
        self.prompt = prompt
        self.buffer = buffer
        self.history = history
        self.history_index = None       # index into the history while browsing it
        self.stashed = ''
        self.default = default
        self.table = table
        self.result = None
        self.selected = 0
        self.offset = 0
        self._key = None
        self._candidates = []
        self._base = ''
        self._pattern = ''

    def refresh(self):
        """Bring the candidate list up to date with the input."""
        text = self.buffer.string()
        if self.table is None:
            return []
        key = (text, len(histories[self.history]) if self.history else 0)
        if key == self._key:
            return self._candidates
        ed = _ed()
        table = self.table
        base, pattern = table.split(text)
        candidates = table.all(base)
        is_file = table.category == 'file'
        found = filter_candidates(
            pattern, candidates,
            history=() if is_file or not self.history else histories[self.history],
            is_file=is_file,
            ignored=ed.var('completion_ignored_extensions') if is_file else ())
        if not pattern and self.default and self.default in found:
            found.remove(self.default)
            found.insert(0, self.default)
        self._key = key
        self._candidates = found
        self._base, self._pattern = base, pattern
        self.selected = 0 if found else -1
        self.offset = 0
        return found

    def chosen(self):
        """The answer ``RET`` would give now."""
        found = self.refresh()
        if self.table is not None and 0 <= self.selected < len(found):
            return self.table.join(self._base, found[self.selected])
        return self.buffer.string()

    def rows(self, width):
        """Rows listing the candidates, for the display."""
        ed = _ed()
        found = self.refresh()
        window = ed.frame.minibuffer_window
        total = len(found)
        if self.table is None:
            return []
        if total:
            where = f'{self.selected + 1}/{total}' if self.selected >= 0 else f'*/{total}'
        else:
            where = '0/0'
        window.prompt = f'{where} {self.prompt}'
        if not total:
            return []
        height = max(1, int(ed.var('completions_max_height')))
        if self.selected >= 0:
            if self.selected < self.offset:
                self.offset = self.selected
            elif self.selected >= self.offset + height:
                self.offset = self.selected - height + 1
        self.offset = max(0, min(self.offset, max(0, total - height)))
        normal = faces.style()
        chosen = faces.style('completions-selected')
        matchers = [_matcher(t) for t in
                    ([self._pattern] if self.table.category == 'file' else _terms(self._pattern))
                    if t]
        rows = []
        for index in range(self.offset, min(total, self.offset + height)):
            candidate = found[index]
            base = chosen if index == self.selected else normal
            runs = [(faces.merge(base, 'completions-count'), ' ▸ ' if index == self.selected
                     else '   ')]
            marks = sorted(m.span() for m in (matcher.search(candidate) for matcher in matchers)
                           if m)
            at = 0
            for start, end in marks:
                if start < at:
                    continue
                if start > at:
                    runs.append((base, candidate[at:start]))
                runs.append((faces.merge(base, 'completions-common-part'), candidate[start:end]))
                at = end
            runs.append((base, candidate[at:]))
            if self.table.annotate is not None:
                try:
                    note = self.table.annotate(candidate)
                except Exception:
                    note = None
                if note:
                    pad = max(2, 34 - len(candidate))
                    pieces = note if isinstance(note, list) else [(note, 'completions-annotations')]
                    runs.append((base, ' ' * pad))
                    for text, face in pieces:
                        runs.append((faces.merge(base, face) if face else base, text))
            from .display import fit
            rows.append(fit(runs, width, base))
        return rows


def session():
    """The innermost active minibuffer session, or None."""
    return _stack[-1] if _stack else None


def _require_session():
    current = session()
    if current is None:
        raise EditorError('Not in a minibuffer')
    return current


# -- reading ---------------------------------------------------------------------------

def read_from_minibuffer(prompt, initial='', *, history='minibuffer', default=None,
                         keymap=None, table=None):
    """Read a line of text from the user and return it."""
    ed = _ed()
    if ed.minibuffer_depth > 0 and not ed.var('enable_recursive_minibuffers'):
        raise EditorError('Command attempted to use minibuffer while in minibuffer')
    frame = ed.frame
    window = frame.minibuffer_window
    buffer = Buffer(f' *Minibuf-{ed.minibuffer_depth + 1}*')
    buffer.minibuffer = True
    buffer.directory = ed.current_buffer.directory
    buffer.locals['display_line_numbers'] = False
    if keymap is None:
        keymap = minibuffer_local_map if table is None else (
            minibuffer_local_filename_map if table.category == 'file'
            else minibuffer_local_completion_map)
    buffer.keymap = keymap
    state = Session(prompt, buffer, history, default, table)
    outer_window = ed.selected_window
    saved = (window.buffer, window.prompt, ed.current_buffer, ed.completion_rows,
             ed.minibuffer_selected_window, window.buffer.pt)
    if not outer_window.is_minibuffer:
        ed.minibuffer_selected_window = outer_window
    outer_window.pointm.set(outer_window.buffer.pt)
    window.set_buffer(buffer)
    window.prompt = prompt
    ed.select_window(window, record=False)
    ed.current_buffer = buffer
    if initial:
        buffer.insert(initial)
        buffer.undo_boundary()
        buffer.clear_undo()
    ed.minibuffer_depth += 1
    _stack.append(state)
    ed.completion_rows = state.rows if table is not None else None
    ed.echo = None
    try:
        ed.recursive_edit()
        result = state.result if state.result is not None else buffer.string()
    finally:
        ed.minibuffer_depth -= 1
        _stack.pop()
        ed.completion_rows = saved[3]
        ed.minibuffer_selected_window = saved[4]
        window.set_buffer(saved[0], saved[5])
        window.prompt = saved[1]
        buffer.live = False
        if outer_window is window or outer_window in frame.windows():
            target = outer_window
        else:
            target = frame.windows()[0]         # the window we came from was deleted
        ed.selected_window = window
        if target is not window:
            ed.select_window(target, record=False)
        ed.current_buffer = saved[2] if saved[2].live else target.buffer
        ed.minibuffer_message = None
    if result == '' and default is not None and table is None:
        result = default
    add_to_history(history, result)
    return result


def add_to_history(history, entry):
    if not history or not entry:
        return
    entries = histories[history]
    if _ed().var('history_delete_duplicates') and entry in entries:
        entries.remove(entry)
    if not entries or entries[-1] != entry:
        entries.append(entry)


def read_string(prompt, initial='', history='minibuffer', default=None):
    return read_from_minibuffer(prompt, initial, history=history, default=default)


def completing_read(prompt, collection, *, require_match=False, initial='', history=None,
                    default=None, category=None, annotate=None):
    """Read one of ``collection``, with completion.

    ``collection`` is a list, a function returning one, or a ``Table``.
    Unless ``require_match``, the answer may be something not in it.
    """
    table = collection if isinstance(collection, Table) else Table(
        collection, category=category, annotate=annotate, require_match=require_match)
    result = read_from_minibuffer(prompt, initial, history=history or category or 'minibuffer',
                                  default=default, table=table)
    if result == '' and default is not None:
        return default
    return result


def read_file_name(prompt, directory=None, default=None, must_match=False, initial=None,
                   directories_only=False):
    """Read a file name and return it absolute."""
    ed = _ed()
    directory = directory or ed.current_buffer.directory or os.getcwd()
    shown = abbreviate_file_name(directory)
    if not shown.endswith('/'):
        shown += '/'
    table = FileTable(directory, must_match=must_match, directories_only=directories_only)
    text = read_from_minibuffer(prompt, (initial if initial is not None else shown),
                                history='file-name', table=table)
    text = substitute_in_file_name(text)
    if (text == shown or text == '') and default is not None:
        return expand_file_name(default, directory)
    return expand_file_name(text, directory)


def read_buffer(prompt, default=None, require_match=False):
    ed = _ed()
    if default is not None and not isinstance(default, str):
        default = default.name

    def names():
        return [b.name for b in ed.buffers if not b.temporary]

    def annotate(name):
        buffer = ed.get_buffer(name)
        if buffer is None:
            return None
        mode = buffer.major_mode.title if buffer.major_mode else ''
        where = abbreviate_file_name(buffer.filename) if buffer.filename else ''
        return f'{"*" if buffer.modified and buffer.filename else " "} {mode:<14} {where}'

    if default:
        prompt = re.sub(r':\s*$', f' (default {default}): ', prompt)
    return completing_read(prompt, names, require_match=require_match, default=default,
                           category='buffer', annotate=annotate)


def _command_note(name):
    ed = _ed()
    record = commands.get(name)
    if record is None:
        return None
    pieces = []
    keys = where_is(ed.active_maps(ed.active_window().buffer), name)
    if keys:
        pieces.append((key_description(keys[0]) + '  ', 'completions-key'))
    doc = record.doc.strip().split('\n')[0]
    if doc:
        pieces.append((doc, 'completions-annotations'))
    return pieces


def read_command(prompt, default=None):
    return completing_read(prompt, lambda: sorted(commands), require_match=True,
                           category='command', annotate=_command_note, default=default)


def read_variable(prompt, default=None):
    def note(name):
        var = variables.get(name.replace('-', '_'))
        return var.doc.strip().split('\n')[0] if var else None

    name = completing_read(prompt, lambda: sorted(n.replace('_', '-') for n in variables),
                           require_match=True, category='variable', annotate=note,
                           default=default)
    return name.replace('-', '_')


def read_number(prompt, default=None):
    while True:
        if default is not None and '(default' not in prompt:
            shown = re.sub(r':\s*$', f' (default {default}): ', prompt)
        else:
            shown = prompt
        text = read_from_minibuffer(shown, history='number').strip()
        if not text and default is not None:
            return default
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                _ed().message('Please enter a number.', log=False)


def read_char(prompt=None):
    """Read one character; return it as a string."""
    ed = _ed()
    while True:
        event = ed.read_key(prompt)
        key = event.key
        if key == 'C-g':
            raise Quit()
        text = {'SPC': ' ', 'RET': '\n', 'TAB': '\t', 'C-j': '\n'}.get(key, key)
        if len(text) == 1:
            return text
        if event.is_mouse:
            continue
        ed.ding()


def y_or_n_p(prompt):
    """Ask a question answered with a single ``y`` or ``n``."""
    ed = _ed()
    shown = f'{prompt.rstrip()} (y or n) '
    while True:
        event = ed.read_key(shown)
        key = event.key
        if key in ('y', 'Y', 'SPC'):
            return True
        if key in ('n', 'N', 'DEL'):
            return False
        if key in ('C-g', 'ESC', 'C-]'):
            raise Quit()
        if not event.is_mouse:
            shown = f'Please answer y or n.  {prompt.rstrip()} (y or n) '


def yes_or_no_p(prompt):
    """Ask a question that must be answered by typing ``yes`` or ``no``."""
    shown = f'{prompt.rstrip()} (yes or no) '
    while True:
        answer = read_from_minibuffer(shown, history='yes-or-no').strip().lower()
        if answer in ('yes', 'no'):
            return answer == 'yes'
        shown = f'Please answer yes or no.  {prompt.rstrip()} (yes or no) '


def read_key_sequence(prompt):
    ed = _ed()
    ed.echo = prompt
    ed.cursor_in_echo = True
    try:
        keys, _ = ed.read_key_sequence()
    finally:
        ed.cursor_in_echo = False
        ed.echo = None
    return keys


def minibuffer_message(text):
    """Show ``text`` after the minibuffer's contents until the next key."""
    ed = _ed()
    if ed.minibuffer_depth > 0:
        ed.minibuffer_message = text
    else:
        ed.message(text, log=False)


def read_interactive_args(ed, spec):
    """Read the arguments an interactive specification asks for."""
    args = []
    buffer = ed.current_buffer
    raw = ed.current_prefix_arg
    for item in spec.split('\n'):
        if not item:
            continue
        code, prompt = item[0], item[1:]
        if code == 'p':
            args.append(ed.prefix_numeric_value(raw))
        elif code == 'P':
            args.append(raw)
        elif code == 'r':
            args.extend(buffer.region())
        elif code == 'd':
            args.append(buffer.pt)
        elif code == 'm':
            if buffer.mark is None:
                raise EditorError('The mark is not set now')
            args.append(buffer.mark.pos)
        elif code in 'sM':
            args.append(read_string(prompt))
        elif code == 'S':
            args.append(read_string(prompt).strip())
        elif code == 'n':
            args.append(read_number(prompt))
        elif code == 'N':
            args.append(ed.prefix_numeric_value(raw) if raw is not None else read_number(prompt))
        elif code == 'f':
            args.append(read_file_name(prompt, must_match=True))
        elif code in 'FG':
            args.append(read_file_name(prompt))
        elif code == 'D':
            args.append(read_file_name(prompt, directories_only=True))
        elif code == 'b':
            args.append(read_buffer(prompt, default=ed.other_buffer(buffer), require_match=True))
        elif code == 'B':
            args.append(read_buffer(prompt, default=ed.other_buffer(buffer)))
        elif code in 'Ca':
            args.append(read_command(prompt))
        elif code == 'v':
            args.append(read_variable(prompt))
        elif code == 'c':
            args.append(read_char(prompt))
        elif code in 'kK':
            args.append(read_key_sequence(prompt))
        elif code in 'xX':
            from . import live, user
            text = read_from_minibuffer(prompt, history='eval')
            report = live.eval_source(user, text, '<minibuffer>')
            if report.error is not None:
                raise EditorError(f'{type(report.error).__name__}: {report.error}')
            args.append(report.value)
        elif code == 'i':
            args.append(None)
        elif code == 'e':
            args.append(ed.last_event)
        else:
            raise EditorError(f'Unknown interactive code {code!r}')
    return args


# -- commands of the minibuffer ----------------------------------------------------------

@command
def exit_minibuffer():
    """Finish with the minibuffer, giving its contents as the answer."""
    state = _require_session()
    text = state.buffer.string()
    if state.table is not None and not state.table.accepts(text):
        minibuffer_message('No match')
        return
    state.result = text
    raise ExitRecursiveEdit()


@command
def minibuffer_complete_and_exit():
    """Finish with the minibuffer, answering with the highlighted candidate."""
    state = _require_session()
    if state.table is None:
        raise ExitRecursiveEdit()
    answer = state.chosen()
    if not state.refresh() and not state.table.accepts(answer):
        minibuffer_message('No match')
        return
    state.result = answer
    raise ExitRecursiveEdit()


@command
def minibuffer_complete():
    """Put the highlighted candidate into the minibuffer."""
    state = _require_session()
    found = state.refresh()
    if state.table is None or not found:
        minibuffer_message('No match')
        return
    answer = state.chosen() if state.selected >= 0 else state.table.join(state._base, found[0])
    buffer = state.buffer
    if buffer.string() == answer:
        if len(found) == 1:
            minibuffer_message('Sole completion')
        return
    buffer.replace_region(buffer.point_min, buffer.point_max, answer)
    buffer.goto_char(buffer.point_max)


@command
def abort_minibuffers():
    """Leave the minibuffer without answering."""
    ed = _ed()
    if ed.current_buffer.region_active() and ed.var('transient_mark_mode'):
        ed.current_buffer.mark_active = False
        return
    raise AbortRecursiveEdit()


def _move_selection(delta, wrap=True):
    state = _require_session()
    found = state.refresh()
    if not found:
        return
    index = state.selected + delta
    if wrap:
        # One step above the first candidate is the input itself.
        if index < -1:
            index = len(found) - 1
        elif index >= len(found):
            index = -1 if not state.table.require_match else 0
        if index == -1 and state.table.require_match:
            index = len(found) - 1 if delta < 0 else 0
    else:
        index = max(0, min(index, len(found) - 1))
    state.selected = index


@command('p')
def minibuffer_next_candidate(n=1):
    """Highlight the next candidate."""
    _move_selection(n)


@command('p')
def minibuffer_previous_candidate(n=1):
    """Highlight the previous candidate."""
    _move_selection(-n)


@command
def minibuffer_next_page():
    """Move the highlight down by a screenful of candidates."""
    _move_selection(max(1, int(_ed().var('completions_max_height')) - 1), wrap=False)


@command
def minibuffer_previous_page():
    """Move the highlight up by a screenful of candidates."""
    _move_selection(-max(1, int(_ed().var('completions_max_height')) - 1), wrap=False)


@command
def minibuffer_first_candidate():
    """Highlight the first candidate."""
    state = _require_session()
    state.selected = 0 if state.refresh() else -1


@command
def minibuffer_last_candidate():
    """Highlight the last candidate."""
    state = _require_session()
    state.selected = len(state.refresh()) - 1


def _history_move(delta):
    state = _require_session()
    entries = histories[state.history] if state.history else []
    if not entries:
        raise EditorError('No history')
    buffer = state.buffer
    index = state.history_index
    if index is None:
        state.stashed = buffer.string()
        index = len(entries)
    index += delta
    if index < 0:
        raise EditorError('Beginning of history; no preceding item')
    if index > len(entries):
        raise EditorError('End of history; no next item')
    state.history_index = index if index < len(entries) else None
    text = entries[index] if index < len(entries) else state.stashed
    buffer.replace_region(buffer.point_min, buffer.point_max, text)
    buffer.goto_char(buffer.point_max)


@command('p')
def previous_history_element(n=1):
    """Replace the minibuffer's contents with an earlier answer."""
    _history_move(-n)


@command('p')
def next_history_element(n=1):
    """Replace the minibuffer's contents with a later answer."""
    _history_move(n)


@command('p')
def minibuffer_delete_backward(n=1):
    """Delete the character before point; after a slash, the directory before it."""
    state = _require_session()
    buffer = state.buffer
    if n == 1 and buffer.pt == buffer.point_max and buffer.char_before() == '/' \
            and buffer.pt - buffer.point_min > 1:
        text = buffer.string()
        if text not in ('~/', '/') and not text.endswith('//'):
            cut = text.rfind('/', 0, len(text) - 1)
            buffer.delete_region(buffer.point_min + cut + 1, buffer.point_max)
            return
    if buffer.pt - n < buffer.point_min:
        raise EditorError('Beginning of buffer')
    buffer.delete_region(buffer.pt - n, buffer.pt)


for _keys, _command in (
        ('RET', 'exit-minibuffer'), ('C-j', 'exit-minibuffer'), ('M-RET', 'exit-minibuffer'),
        ('C-g', 'abort-minibuffers'), ('C-]', 'abort-minibuffers'),
        ('M-p', 'previous-history-element'), ('M-n', 'next-history-element'),
        ('<up>', 'previous-history-element'), ('<down>', 'next-history-element')):
    minibuffer_local_map.bind(_keys, _command)

for _keys, _command in (
        ('RET', 'minibuffer-complete-and-exit'), ('TAB', 'minibuffer-complete'),
        ('C-n', 'minibuffer-next-candidate'), ('C-p', 'minibuffer-previous-candidate'),
        ('<down>', 'minibuffer-next-candidate'), ('<up>', 'minibuffer-previous-candidate'),
        ('C-v', 'minibuffer-next-page'), ('M-v', 'minibuffer-previous-page'),
        ('<next>', 'minibuffer-next-page'), ('<prior>', 'minibuffer-previous-page'),
        ('M-<', 'minibuffer-first-candidate'), ('M->', 'minibuffer-last-candidate')):
    minibuffer_local_completion_map.bind(_keys, _command)

minibuffer_local_filename_map.bind('DEL', 'minibuffer-delete-backward')
