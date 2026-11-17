# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Language modes taken from GNU Emacs.

``runrist/modes/data`` holds, for each of some fifty Emacs major modes, what
``tools/emacs-extract.el`` wrote down from a running Emacs: the mode's syntax
table, its font-lock keywords, its comment syntax and the file names that
select it.  This module turns those descriptions into runrist major modes.
A description is read the first time its mode is used.

Emacs does some of a mode's work in Lisp, which a description cannot carry.
Where that matters a *port* finishes the job in Python::

    @port('python-mode')
    def python(buffer, definition):
        ...                         # runs after the generic set-up

    @matcher('python--font-lock-f-strings')
    def f_strings(line, position, context):
        ...                         # stands in for the Emacs function of that name

Keywords whose matcher or face is Lisp with no port are left out, so an
unported mode highlights a little less than Emacs does, never wrongly.

The descriptions are derived from GNU Emacs, copyright the Free Software
Foundation, and like the rest of runrist are under the GNU GPL version 3 or
later.
"""

import json
import pathlib
import re

from .. import eregex, faces, fontlock
from ..fontlock import Anchored, FontLock, Highlight, Keyword
from ..mode import major_mode, major_modes
from ..syntax import Syntax

DATA = pathlib.Path(__file__).with_name('data')

ports = {}
refinements = {}
matchers = {}
_definitions = {}

# The forms a keyword uses to say where its anchored search starts and stops
# that can be understood without running Lisp.
_PRE_FORMS = (
    re.compile(r'nil|\(point-at-eol\)|\(line-end-position\)|\(pos-eol\)'),
    re.compile(r'\(goto-char \(match-(?P<side>beginning|end) (?P<group>\d+)\)\)'),
    re.compile(r'\(progn \(goto-char \(match-(?P<side>beginning|end) (?P<group>\d+)\)\) '
               r'\(match-end (?P<limit>\d+)\)\)'),
    re.compile(r'\(match-end (?P<limit>\d+)\)'),
)


def port(mode):
    """Register a function of ``(buffer, definition)`` that completes a mode's set-up."""
    def register(function):
        ports[mode] = function
        return function
    return register


def refine(mode):
    """Register a function of the ``Definition`` that adjusts a mode once, when
    its description is first read: its syntax, its keywords."""
    def register(function):
        refinements[mode] = function
        definition = _definitions.get(mode)
        if definition is not None:              # redefined while in use: start afresh
            del _definitions[mode]
        return function
    return register


def matcher(name):
    """Register a Python matcher under the name of the Emacs function it replaces."""
    def register(function):
        matchers[name] = function
        return function
    return register


class Definition:
    """One mode as Emacs describes it."""

    def __init__(self, name, data):
        self.name = name
        self.data = data
        self.syntax = Syntax.from_emacs(data['syntax'])
        # Keywords are matched under the table font-lock uses, which may differ.
        self.keyword_syntax = (Syntax.from_emacs(data['font_lock_syntax'])
                               if data.get('font_lock_syntax') else self.syntax)
        self.case_fold = bool(data.get('case_fold'))
        self.skipped = []
        for face, look in (data.get('faces') or {}).items():
            if face not in faces._specs:
                attrs = {}
                if look.get('inherit'):
                    attrs['inherit'] = look['inherit']
                if look.get('bold'):
                    attrs['weight'] = 700
                if look.get('italic'):
                    attrs['italic'] = True
                if look.get('underline'):
                    attrs['underline'] = 'line'
                faces.defface(face, f'From Emacs, for {name}.', **attrs)
        self.fontlock = FontLock(self.syntax, self._keywords(),
                                 keywords_only=bool(data.get('keywords_only')))

    def regexp(self, pattern):
        """Compile one of this mode's Emacs regexps: ``(compiled, groups)``."""
        return eregex.compile(pattern, self.keyword_syntax, self.case_fold)

    def _face(self, face):
        if isinstance(face, str):
            return face
        if isinstance(face, list):
            return tuple(face)
        return None                     # computed by Lisp

    def _highlight(self, item, groups):
        if 'anchor' in item:
            anchor = item['anchor']
            if not anchor.get('re'):
                return None
            pre = item.get('pre') or 'nil'
            form = next((m for m in (f.fullmatch(pre) for f in _PRE_FORMS) if m), None)
            if form is None:
                return None             # where the search starts is decided by Lisp
            where = form.groupdict()
            start = limit = None
            if where.get('group'):
                python = groups.get(int(where['group'])) if int(where['group']) else (0,)
                if not python:
                    return None
                start = ('b' if where['side'] == 'beginning' else 'e', python[0])
            if where.get('limit'):
                python = groups.get(int(where['limit'])) if int(where['limit']) else (0,)
                if not python:
                    return None
                limit = python[0]
            compiled, inner_groups = self.regexp(anchor['re'])
            inner = [h for h in (self._highlight(sub, inner_groups) for sub in item['highlights'])
                     if h is not None]
            return Anchored(compiled, inner, start, limit) if inner else None
        face = self._face(item.get('face'))
        if face is None:
            return None
        number = item['subexp']
        indexes = (0,) if number == 0 else groups.get(number)
        if not indexes:
            return None
        return Highlight(indexes, face, item.get('override'), bool(item.get('lax')))

    def _keywords(self):
        out = []
        for entry in self.data['keywords']:
            source = entry['matcher']
            if source.get('re') is not None:
                compiled, groups = self.regexp(source['re'])
            else:
                function = matchers.get(source.get('fn') or '')
                if function is None:
                    self.skipped.append(source.get('fn') or 'an anonymous function')
                    continue
                compiled, groups = function, _Identity()
            highlights = [h for h in (self._highlight(item, groups)
                                      for item in entry['highlights']) if h is not None]
            if highlights:
                out.append(Keyword(compiled, highlights))
        return out


class _Identity(dict):
    """Group numbers of a Python matcher are already Python's."""

    def get(self, number, default=None):
        return (number,)


def load(name):
    """The definition of mode ``name``, read from its file the first time."""
    definition = _definitions.get(name)
    if definition is None:
        with open(DATA / f'{name}.json', encoding='utf-8') as source:
            definition = _definitions[name] = Definition(name, json.load(source))
        adjust = refinements.get(name)
        if adjust is not None:
            adjust(definition)
            definition.fontlock.changed()
    return definition


def index():
    path = DATA / 'index.json'
    if not path.exists():
        return {}
    with open(path, encoding='utf-8') as source:
        return json.load(source)


def _setup(name):
    def setup(buffer):
        definition = load(name)
        data = definition.data
        buffer.set('comment_start', data.get('comment_start'))
        buffer.set('comment_end', data.get('comment_end') or '')
        if data.get('indent_tabs'):
            buffer.set('indent_tabs_mode', True)
        fontlock.enable(buffer, definition.fontlock)
        finish = ports.get(name)
        if finish is not None:
            finish(buffer, definition)

    setup.__name__ = name.replace('-', '_')
    setup.__qualname__ = setup.__name__
    setup.__doc__ = f'Major mode for editing {name.removesuffix("-mode")} files, after Emacs.'
    return setup


def define_all():
    """Define a major mode for every description in the data directory."""
    listed = index()
    pending = dict(listed)
    # Parents first, so that a derived mode finds its parent's keymap.
    while pending:
        progressed = False
        for name, info in list(pending.items()):
            parent = info.get('parent') or 'fundamental-mode'
            if parent in pending and parent != name:
                continue
            if parent not in major_modes:
                parent = 'fundamental-mode'
            major_mode(name, info.get('title'), parent=parent, auto=info.get('auto', ()),
                       interpreters=info.get('interpreters', ()))(_setup(name))
            del pending[name]
            progressed = True
        if not progressed:
            break
    return listed


define_all()
