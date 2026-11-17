# runrist

A terminal text editor in the Emacs tradition, written in Python (3.12 or
later) for the [Silkie](https://github.com/adamdeprince/silkie/tree/silkie)
terminal through [hamingjur](https://github.com/adamdeprince/hamingjur).

The running editor is the program.  Redefine a command, a class or a key
binding and the change is in effect at once, everywhere that already refers
to it, with no restart.  The keys are Emacs's.

Version 0.1: young, usable, and able to edit its own source while it runs.

## Running it

It needs Python 3.12 or later.

```sh
pip install runrist
runrist [+LINE] [FILE...]
```

To work on it from a checkout:

```sh
uv venv --python 3.12 .venv     # or any later Python
uv pip install --python .venv/bin/python -e .
.venv/bin/runrist [+LINE] [FILE...]
```

`-Q` skips your `init.py`; `-e CODE` evaluates Python after start-up.

It runs in any terminal with 24-bit color.  In Silkie and kitty it also gets
unambiguous keys (so `C-i` is not `TAB`, and `M-%` and `C-_` arrive as typed),
the minimap and the rounded badges in the mode line.  In
Silkie it additionally uses pixel scroll reports and font weights.

## What is here

**Emacs's keys and Emacs's model.**  Buffers, windows that split, a real
minibuffer, the kill ring, the mark and the region, prefix arguments, undo
that can itself be undone, narrowing, registers and rectangles, dired.
`C-h b` lists every key; `C-h k`, `C-h f`, `C-h v`, `C-h m` and `C-h a`
describe keys, commands, variables, modes, and find commands by name.  Pause
after a prefix such as `C-x` and the keys that may follow are listed.

**Keyboard macros.**  `F3` records, `F4` stops and then replays; `C-x (`,
`C-x )` and `C-x e` do the same.  A numeric argument repeats, and `C-u 0 F4`
repeats until something fails.  `F3` while recording inserts a counter.
Macros replay through the same input path they were recorded from, so they
drive prompts, searches and queries.  `C-x C-k n` names one, `C-x C-k b`
binds it to a key, `C-x C-k r` applies it to each line of the region, and
`M-x insert-kbd-macro` writes it out as Python.

**Live code.**  In a Python buffer `C-M-x` evaluates the definition around
point, `C-x C-e` the expression before it, and `M-:` anything you type.  Code
from a file that is a loaded module is evaluated *in that module*, and
functions and classes are patched in place: the same function object gets the
new code, existing instances get the new methods.  Saving such a file loads
what changed, and the editor also watches its own source on disk, so an edit
made from outside lands in the running editor too.  State survives: on a
reload only the top-level statements that changed are run again.  See
`runrist/live.py`.

**Highlighting learned from Emacs.**  `tools/emacs-extract.el` runs in a batch
Emacs and writes out, for each major mode, its syntax table and its font-lock
keywords after Emacs has compiled them.  `runrist/modes/data` holds fifty
modes extracted this way from Emacs 31.1; `runrist/eregex.py` translates
their regular expressions and `runrist/fontlock.py` applies them with
font-lock's rules.  What Emacs does in Lisp -- Python's triple-quoted
strings, docstrings, indentation -- is ported by hand in `runrist/modes`.
The C family, Go, Rust, Swift, Kotlin, Zig, TypeScript, PHP, Markdown, YAML
and Dockerfiles, which Emacs has only as Lisp-heavy or tree-sitter modes, are
written from scratch in `clike.py` and `markup.py`.

To refresh or extend the extracted modes:

```sh
.venv/bin/python tools/extract_modes.py             # all of them
.venv/bin/python tools/extract_modes.py ruby-mode   # some
```

**The minimap.**  A strip down the right of each window shows the buffer
zoomed out until every character is one dot in the color of its syntax
highlighting, with a band over the part on screen and a line at point.  Click
or drag in it to go there.  `M-x minimap-mode` toggles it for a buffer and
`M-x global-minimap-mode` everywhere.  It is drawn with the kitty graphics
protocol, using Silkie's palette images where the terminal has them.

**Folding.**  `<backtab>` folds every definition so the buffer reads as an
outline, and again opens it.  `C-<tab>` or `C-c @ C-c` toggles the block at
point; the rest of hideshow's keys are under `C-c @`.  A folded block shows
its first line and a badge counting what is hidden.  Searching into a fold
opens it; moving by line steps over it.

**Looks.**  Two themes, `runestone` (dark, the default) and `birch` (light);
`M-x load-theme`.  Faces carry weights from 100 to 900 and font roles, which
Silkie draws; elsewhere a heavy weight is bold.

## Making it yours

`~/.config/runrist/init.py` is loaded at start-up, and reloaded as you save
it:

```python
from runrist.api import *

setq(fill_column=88, scroll_margin=3, cursor_type='box')
load_theme('birch')

@command('p')
def duplicate_line(n=1):
    "Insert N copies of the current line below it."
    line = buffer_substring(line_beginning_position(), line_end_position())
    with save_excursion():
        goto_char(line_end_position())
        insert(('\n' + line) * n)

global_set_key('C-c d', duplicate_line)

# Comments in the terminal's serif face, where it has one.
set_face('font-lock-comment-face', font='serif', italic=False)
```

`runrist/api.py` is the surface meant for this: the decorators that define
commands, modes, variables and faces, and functions on the current buffer
under the names Emacs gives them.  `M-x describe-variable` documents every
option.

Where it differs from Emacs on purpose: positions count from zero and ranges
exclude their end, as in Python; regular expressions in searches and in
`query-replace-regexp` are Python's; and the extension language is Python.

## The source

```
runrist/
  text.py buffer.py       lines in blocks; markers, undo, overlays, narrowing
  keys.py keymap.py       Emacs key notation, the kitty keyboard protocol, keymaps
  command.py editor.py    commands; the command loop, timers, macro playback
  minibuffer.py           prompts, completion and history
  window.py display.py    the window tree; redisplay
  faces.py themes/        faces, styles and themes
  term.py                 the terminal, through hamingjur
  live.py pyeval.py       loading code into the running editor
  syntax.py eregex.py fontlock.py     highlighting
  fold.py minimap.py search.py kmacro.py help.py files.py
  commands/               the standard commands and their keys
  modes/                  major modes; data/ is what was extracted from Emacs
tools/                    the Emacs extractor
tests/                    pytest; the editor runs on a fake terminal
```

```sh
.venv/bin/python -m pytest
```

`runrist.testing` drives the whole editor without a terminal: feed it keys in
`kbd` notation and read back the buffer or the screen.

## Not here yet

A language server client, tree-sitter, a shell or terminal buffer, version
control, spell checking, undo restricted to the region, bidirectional text,
and most of the long tail of Emacs commands.  `C-g` interrupts prompts,
searches and macros, but not a command that is busy computing.

## License

GNU General Public License, version 3 or later; see `COPYING`.  The files in
`runrist/modes/data` are derived from GNU Emacs, copyright the Free Software
Foundation, which is why the license is what it is.
