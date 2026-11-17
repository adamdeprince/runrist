# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Key names in Emacs notation, and input events.

A key is a string: ``a``, ``C-x``, ``M-%``, ``C-M-<left>``, ``RET``,
``<f3>``, ``<mouse-1>``.  Modifiers are written in the order ``C- H- M- S- s-``
(control, hyper, meta, shift, super).  A shifted letter is the capital letter;
shift is spelled out only where it does not change the character.  A key
sequence is a tuple of keys, and ``kbd('C-x C-f')`` makes one.

``TAB``, ``RET`` and ``ESC`` are the Tab, Enter and Escape keys.  A terminal
that speaks the kitty keyboard protocol reports ``C-i``, ``C-m`` and ``C-[``
as themselves; a key sequence that has no binding under one of those names is
looked up again under the other (see ``fallbacks``), so bindings written the
Emacs way work on every terminal.
"""

import re

_MODIFIERS = 'CHMSs'
_SPLIT = re.compile(r'([ACHMSs])-(?=.)')

_NAMED = {
    'ret': 'RET', 'return': 'RET', 'enter': 'RET', 'kp-enter': 'RET',
    'tab': 'TAB', 'esc': 'ESC', 'escape': 'ESC',
    'spc': 'SPC', 'space': 'SPC',
    'del': 'DEL', 'backspace': 'DEL',
    'delete': '<delete>', 'deletechar': '<delete>',
    'pageup': '<prior>', 'pgup': '<prior>', 'prior': '<prior>',
    'pagedown': '<next>', 'pgdn': '<next>', 'pgdown': '<next>', 'next': '<next>',
    'ins': '<insert>', 'insert': '<insert>', 'insertchar': '<insert>',
    'backtab': '<backtab>', 'btab': '<backtab>',
}


_SPECIAL = frozenset(('RET', 'SPC', 'TAB', 'ESC', 'DEL', 'LFD', 'NUL'))


def normalize_key(desc):
    """Canonical name of one key."""
    mods = set()
    while True:
        match = _SPLIT.match(desc)
        if match is None:
            break
        mods.add('M' if match.group(1) == 'A' else match.group(1))
        desc = desc[match.end():]
    base = desc
    if len(base) > 1:
        if base.startswith('<') and base.endswith('>') and len(base) > 2:
            name = base[1:-1].lower()
        elif base in _SPECIAL:
            name = base.lower()
        else:
            raise ValueError(f'Not a key: {desc!r}')
        if name == 'lfd':
            base = 'j'
            mods.add('C')
        elif name == 'nul':
            base = 'SPC'
            mods.add('C')
        else:
            base = _NAMED.get(name, f'<{name}>')
    if len(base) == 1:
        if 'C' in mods:
            if base == '@':
                base = 'SPC'
            elif base.isalpha() and base.isupper():
                base = base.lower()         # C-X is C-x; ctrl-shift-x is written C-S-x
        elif 'S' in mods and base.isalpha() and base.upper() != base:
            base = base.upper()
            mods.discard('S')
        elif 'S' in mods and base.isalpha() and base.isupper():
            mods.discard('S')
    if base == 'TAB' and 'S' in mods:
        base = '<backtab>'
        mods.discard('S')
    return ''.join(m + '-' for m in _MODIFIERS if m in mods) + base


def kbd(keys):
    """Key sequence named by ``keys``, as a tuple of canonical keys.

    As in Emacs, words are separated by spaces, a function key is written in
    angle brackets, and a word of ordinary characters stands for those
    characters typed one after another: ``kbd('C-x b foo RET')``.
    """
    if not isinstance(keys, str):
        return tuple(normalize_key(k) for k in keys)
    out = []
    for word in keys.split():
        try:
            out.append(normalize_key(word))
        except ValueError:
            if _SPLIT.match(word):
                raise
            out.extend(normalize_key(char) for char in word)
    return tuple(out)


def key_description(keys):
    if isinstance(keys, str):
        return keys
    return ' '.join(keys)


def split_key(key):
    """``(set of modifier letters, base)`` of a canonical key."""
    mods = set()
    while True:
        match = _SPLIT.match(key)
        if match is None:
            return mods, key
        mods.add(match.group(1))
        key = key[match.end():]


def join_key(mods, base):
    return ''.join(m + '-' for m in _MODIFIERS if m in mods) + base


def add_modifier(key, modifier):
    mods, base = split_key(key)
    mods.add(modifier)
    return normalize_key(join_key(mods, base))


def fallbacks(key):
    """Other names to try, in order, when ``key`` has no binding."""
    mods, base = split_key(key)
    out = []
    if 'C' in mods and base in ('i', 'm', '['):
        rest = mods - {'C'}
        out.append(join_key(rest, {'i': 'TAB', 'm': 'RET', '[': 'ESC'}[base]))
    if 'S' in mods:
        # An unbound shifted key runs the binding of the plain key.
        out.append(normalize_key(join_key(mods - {'S'}, base)))
    elif len(base) == 1 and base.isupper() and mods:
        out.append(join_key(mods, base.lower()))
    return out


def self_insert_text(key):
    """The text a key inserts when it is not bound to anything else."""
    if key == 'SPC':
        return ' '
    if len(key) == 1:
        return key
    return None


class Event:
    """One thing read from the terminal.

    ``key`` names it.  A paste carries its ``text``.  A mouse event carries
    the cell it happened in, and ``px``/``py`` when pixels were reported.
    ``data`` is whatever else the source attached, such as a protocol report.
    """

    __slots__ = ('key', 'text', 'x', 'y', 'px', 'py', 'data')

    def __init__(self, key, text=None, x=-1, y=-1, px=None, py=None, data=None):
        self.key = key
        self.text = text
        self.x = x
        self.y = y
        self.px = px
        self.py = py
        self.data = data

    @property
    def is_mouse(self):
        return self.x >= 0

    @property
    def recordable(self):
        """Whether a keyboard macro should keep this event."""
        return self.x < 0 and self.data is None and not self.key.startswith('<focus')

    def __eq__(self, other):
        return (isinstance(other, Event) and self.key == other.key and self.text == other.text
                and self.x == other.x and self.y == other.y)

    def __hash__(self):
        return hash((self.key, self.text))

    def __repr__(self):
        extra = f' {self.text!r}' if self.text is not None else ''
        where = f' @{self.x},{self.y}' if self.x >= 0 else ''
        return f'<Event {self.key}{extra}{where}>'


# -- decoding hamingjur keystrokes -------------------------------------------

_KITTY = re.compile(r'\x1b\[(\d+)(?::(\d*))?(?::(\d*))?(?:;(\d*)(?::(\d*))?)?(?:;([\d:]*))?u')
_MOUSE = re.compile(r'\x1b\[<(\d+);(\d+);(\d+)([mM])')

_KITTY_NAMED = {9: 'TAB', 13: 'RET', 27: 'ESC', 32: 'SPC', 127: 'DEL', 8: 'DEL',
                57414: 'RET',                                   # keypad enter
                57417: '<left>', 57418: '<right>', 57419: '<up>', 57420: '<down>',
                57421: '<prior>', 57422: '<next>', 57423: '<home>', 57424: '<end>',
                57425: '<insert>', 57426: '<delete>', 57427: '<begin>',
                57409: '.', 57410: '/', 57411: '*', 57412: '-', 57413: '+', 57415: '=',
                57416: ','}
_KITTY_NAMED.update({57399 + d: str(d) for d in range(10)})    # keypad digits
_KITTY_NAMED.update({57376 + n: f'<f{13 + n}>' for n in range(23)})

_CODE_KEYS = {
    'KEY_LEFT': '<left>', 'KEY_RIGHT': '<right>', 'KEY_UP': '<up>', 'KEY_DOWN': '<down>',
    'KEY_HOME': '<home>', 'KEY_END': '<end>', 'KEY_PGUP': '<prior>', 'KEY_PGDOWN': '<next>',
    'KEY_PPAGE': '<prior>', 'KEY_NPAGE': '<next>', 'KEY_FIND': '<home>', 'KEY_SELECT': '<end>',
    'KEY_INSERT': '<insert>', 'KEY_IC': '<insert>', 'KEY_DELETE': '<delete>', 'KEY_DC': '<delete>',
    'KEY_BACKSPACE': 'DEL', 'KEY_ENTER': 'RET', 'KEY_TAB': 'TAB', 'KEY_BTAB': '<backtab>',
    'KEY_ESCAPE': 'ESC', 'KEY_EXIT': 'ESC', 'KEY_BEGIN': '<begin>', 'KEY_B2': '<begin>',
    'KEY_SLEFT': 'S-<left>', 'KEY_SRIGHT': 'S-<right>', 'KEY_SR': 'S-<up>', 'KEY_SF': 'S-<down>',
    'KEY_SHOME': 'S-<home>', 'KEY_SEND': 'S-<end>', 'KEY_SDC': 'S-<delete>',
    'KEY_MENU': '<menu>',
}

_code_names = None


def _code_key(code):
    global _code_names
    if _code_names is None:
        from hamingjur.keyboard import get_keyboard_codes
        _code_names = get_keyboard_codes()
    name = _code_names.get(code)
    if name is None:
        return _KITTY_NAMED.get(code)
    if name in _CODE_KEYS:
        return _CODE_KEYS[name]
    match = re.fullmatch(r'KEY_F(\d+)', name)
    if match:
        return f'<f{match.group(1)}>'
    match = re.fullmatch(r'KEY_KP_(\d)', name)
    if match:
        return match.group(1)
    if name.startswith('KEY_'):
        return '<' + name[4:].lower().replace('_', '-') + '>'
    return None


def _mods_from_bits(bits):
    mods = set()
    if bits & 1:
        mods.add('S')
    if bits & (2 | 32):
        mods.add('M')
    if bits & 4:
        mods.add('C')
    if bits & 8:
        mods.add('s')
    if bits & 16:
        mods.add('H')
    return mods


_US_SHIFT = dict(zip("`1234567890-=[]\\;',./", '~!@#$%^&*()_+{}|:"<>?'))


def _text_key(char, mods, shifted=None):
    """Key for a character-producing key with modifiers held."""
    mods = set(mods)
    if 'S' in mods:
        if not shifted and char in _US_SHIFT:
            # The terminal did not say what the shifted key produces; assume a US layout
            # rather than leave M-% and M-< unreachable.
            shifted = _US_SHIFT[char]
        if shifted and shifted != char:
            if not shifted.isalpha() or 'C' not in mods:
                char = shifted
                mods.discard('S')
        elif char.isalpha() and char.upper() != char and 'C' not in mods:
            char = char.upper()
            mods.discard('S')
    if char == ' ':
        char = 'SPC'
    return normalize_key(join_key(mods, char))


def _control_char_key(o):
    """Key for a C0 control character or DEL received as a plain byte."""
    if o == 0:
        return 'C-SPC'
    if o == 9:
        return 'TAB'
    if o == 13:
        return 'RET'
    if o == 27:
        return 'ESC'
    if o == 127:
        return 'DEL'
    if o < 27:
        return 'C-' + chr(o + 96)
    return 'C-' + chr(o + 64)               # C-\\ C-] C-^ C-_


class Decoder:
    """Turns hamingjur keystrokes into events, pairing mouse presses and
    releases into clicks and drags."""

    def __init__(self, cell_size=lambda: None, pixels=False, double_click_time=0.4):
        self.cell_size = cell_size
        self.pixels = pixels
        self.double_click_time = double_click_time
        self._down = None           # (button, x, y) of the press in progress
        self._moved = False
        self._last_click = (None, -1, -1, 0.0)
        self._clicks = 1

    def decode(self, ks, now=0.0):
        """The event for keystroke ``ks``, or None if it is not one."""
        if not ks:
            name = ks.name
            if name == 'RESIZE_EVENT':
                return Event('<resize>')
            return None
        event = ks.protocol_event
        if event is not None:
            return Event('<protocol>', data=event)
        raw = str(ks)
        name = ks.name or ''
        if name == 'BRACKETED_PASTE':
            return Event('<paste>', text=ks.text or '')
        if name == 'FOCUS_IN':
            return Event('<focus-in>')
        if name == 'FOCUS_OUT':
            return Event('<focus-out>')
        if name == 'RESIZE_EVENT':
            return Event('<resize>')
        if name.endswith('_RESPONSE') or name in ('CSI', 'PROTOCOL_UNKNOWN'):
            return None
        match = _MOUSE.fullmatch(raw)
        if match:
            return self._mouse(match, now)
        match = _KITTY.fullmatch(raw)
        if match:
            return self._kitty(match)
        # A lone byte, or ESC and a byte, is named from the byte itself:
        # hamingjur reports Tab and Enter as control characters with a modifier.
        if len(raw) == 1:
            return Event(self._plain(raw))
        if len(raw) == 2 and raw[0] == '\x1b':
            return Event(add_modifier(self._plain(raw[1]), 'M'))
        mods = _mods_from_bits(ks.modifiers_bits)
        if ks.code is not None:
            key = _code_key(ks.code)
            if key is None:
                return None
            more, base = split_key(key)
            return Event(normalize_key(join_key(mods | more, base)))
        value = ks.value
        if value and len(value) == 1:
            return Event(_text_key(value, mods))    # xterm's modifyOtherKeys
        return None

    @staticmethod
    def _plain(char):
        o = ord(char)
        if o < 32 or o == 127:
            return _control_char_key(o)
        return 'SPC' if char == ' ' else char

    @staticmethod
    def _kitty(match):
        code = int(match.group(1))
        shifted = match.group(2)
        bits = (int(match.group(4)) - 1) if match.group(4) else 0
        if match.group(5) and int(match.group(5)) == 3:
            return None                                     # key release
        mods = _mods_from_bits(bits)
        named = _KITTY_NAMED.get(code)
        if named is not None:
            if len(named) == 1:
                return Event(_text_key(named, mods - {'S'}))
            more, base = split_key(named)
            return Event(normalize_key(join_key(mods | more, base)))
        if 57344 <= code <= 63743:
            key = _code_key(code)                           # other functional keys
            if key is None or 57441 <= code <= 57454:
                return None                                 # a modifier key by itself
            return Event(normalize_key(join_key(mods, key)))
        try:
            char = chr(code)
        except (ValueError, OverflowError):
            return None
        return Event(_text_key(char, mods, chr(int(shifted)) if shifted else None))

    def _mouse(self, match, now):
        b = int(match.group(1))
        x = int(match.group(2)) - 1
        y = int(match.group(3)) - 1
        released = match.group(4) == 'm'
        px = py = None
        if self.pixels:
            cell = self.cell_size()
            if cell:
                px, py = x, y
                x, y = px // max(1, cell[0]), py // max(1, cell[1])
        mods = set()
        if b & 4:
            mods.add('S')
        if b & 8:
            mods.add('M')
        if b & 16:
            mods.add('C')
        button = b & ~(4 | 8 | 16 | 32)
        if button & 256:
            return None                                     # the pointer left the window
        if button in (64, 65, 66, 67):
            name = ('wheel-up', 'wheel-down', 'wheel-left', 'wheel-right')[button - 64]
            return Event(join_key(mods, f'<{name}>'), x=x, y=y, px=px, py=py)
        if b & 32:
            if self._down is None:
                return None                                 # motion with no button held
            if (x, y) != self._down[1:]:
                self._moved = True
            return Event('<mouse-movement>', x=x, y=y, px=px, py=py)
        if button > 2:
            return None
        number = (1, 2, 3)[button]
        if not released:
            last_button, lx, ly, at = self._last_click
            if last_button == number and (lx, ly) == (x, y) and now - at <= self.double_click_time:
                self._clicks = self._clicks % 3 + 1
            else:
                self._clicks = 1
            self._last_click = (number, x, y, now)
            self._down = (number, x, y)
            self._moved = False
            prefix = ('', 'double-', 'triple-')[self._clicks - 1]
            return Event(join_key(mods, f'<{prefix}down-mouse-{number}>'), x=x, y=y, px=px, py=py)
        dragged = self._moved and self._down is not None and self._down[0] == number
        self._down = None
        if dragged:
            return Event(join_key(mods, f'<drag-mouse-{number}>'), x=x, y=y, px=px, py=py)
        prefix = ('', 'double-', 'triple-')[self._clicks - 1]
        return Event(join_key(mods, f'<{prefix}mouse-{number}>'), x=x, y=y, px=px, py=py)
