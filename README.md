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

## AI in the editor

Press **`C-h A`** (`M-x ai-help`) to see your current AI selection and
configured profiles. From an AI transcript or review buffer, press `?`.
Use `M-x ai-select-provider` to switch profiles for this run, or
[`M-x ai-configure` to change your settings](#configure-ai).

| Command | What it does |
| --- | --- |
| `ai-complete` | Suggest an insertion at point. `C-c RET` accepts the full suggestion. `ai-preview-completion` opens the full diff. |
| `ai-completion-mode` | Request suggestions after a pause in typing in this buffer. |
| `ai-rewrite` | Send the selected region and an instruction. Review the proposed replacement with `a` or `r`. |
| `ai-chat` | Attach the active region, or the accessible buffer if no region is active, and ask a question. Type `i` in the transcript for a follow-up without reattaching the source. |
| `ai-delegate` | Give the AI a project task. It can list directories, read file buffers, propose replacements or new files, and ask to run commands. |
| `ai-start` | Open a fresh conversation with the selected AI. |
| `ai-cancel` | Cancel a request, pending action, or suggestion in its buffer. In a transcript or review buffer, press `k`. |

Delegated work starts in the enclosing Git repository or the buffer's
directory. The transcript shows the directory, tool requests, and results.
Review each edit or command before accepting it. Accepted edits enter the
target buffer with normal undo. Save the buffer to write the changes and
trigger the usual live reload. Commands run locally with your account's
permissions and see files as saved on disk. A proposal expires if its source
changes. You can keep editing while a backend is working.

### Configure AI

1. Run `M-x ai-configure` to visit your personal `~/.config/runrist/init.py`
   (or `$XDG_CONFIG_HOME/runrist/init.py`). The first normal launch creates
   this file from the [starter init.py](runrist/templates/init.py) if it is missing.
2. Set `ai_backend` to the **menu name** you want at startup. For example,
   with `from runrist.api import setq` in the file, use `setq(ai_backend='codex')`.
   The starter `claude` and `codex` entries use their respective command-line
   clients. Install the client you want, make its executable available on
   Runrist's `PATH`, and use `M-x ai-login` to sign in.
3. Save with `C-x C-s`. Run `M-x ai-select-backend` to see the updated menu.
   To try a profile, open a scratch buffer and run `M-x ai-chat`. If a request
   fails, `C-h e` opens `*Messages*` with the error.

**Sign-in and account status.** Open `C-h A` and follow **Sign in to Claude
or Codex** (`M-x ai-login`) or **Check sign-in status** (`M-x ai-auth-status`).
Choose the profile whose account you want to manage. Your selected AI for
editing stays the same. Runrist gives the terminal to that profile's CLI.
Follow its instructions, then press Return to resume editing.

| Client | Sign-in choices |
| --- | --- |
| Claude Code | Claude subscription, Anthropic Console, or single sign-on. |
| Codex | Browser sign-in, a device code for use in another browser, or an API key from the `OPENAI_API_KEY` environment variable. |

The CLI manages the credentials used for subsequent requests. For Codex
API key login, set the variable in the environment that launches Runrist.
The helper passes its value to Codex on standard input. The same account
helpers work with renamed profiles or absolute paths to `claude` and `codex`.
HTTP profiles use the `token_file` setting described below.

**Select provider, model, and effort.** In the editor, use
`M-x ai-select-provider`, `M-x ai-select-model`, and `M-x ai-select-effort`,
or follow their links in `C-h A`. The model picker accepts an exact model ID
as well as names from the profile's `models` list. Choose `(profile default)`
to clear an override. Selections apply to subsequent requests and each
provider remembers its own settings for the current run.

From the shell, for example:

```sh
runrist --provider codex --model "$MODEL_ID" --effort high project.py
```

From the editor's Python console (`M-:`), use
`select_ai('codex', model='your-model-id', effort='high')`.
To make a choice permanent, edit the profile's `model` and `effort` in
`init.py` and set `ai_backend` to its name. With neither option set, a
command profile uses the client's own defaults.

Each entry in `setq(ai_backends={...})` defines one provider in the picker.
Edit the Claude and Codex entries directly, or add entries to that dictionary:

```python
    'writing': {
        'url': 'https://inference.example/v1/chat/completions',
        'model': 'your-server-model',
        'efforts': ['low', 'high'],
        'mode_efforts': {'completion': 'low'},
    },
    'local-command': {
        'command': ['my-assistant', '--print'],
        'model_args': ['--model', '{value}'],
        'effort_args': ['--effort', '{value}'],
    },
```

A **command** profile runs its argument list directly. The optional
`model_args` and `effort_args` append arguments when a value is set.
`{value}` inserts the literal value and `{quoted_value}` a JSON-quoted
string for clients that parse a configuration expression. The starter file
also defines each CLI’s login choices and status command. A plain argument
list still works for commands that only need stdin and stdout.

An **HTTP** entry requires the full streaming
Chat Completions `url` and the server's `model` ID. Two menu entries can use
the same URL with different models or instructions. HTTP profiles also accept
the following settings.

| Setting | When to use it |
| --- | --- |
| `token_file` | For bearer authentication, give the path to a private file containing the token, e.g. `'~/.config/runrist/inference.token'`. |
| `system` | Set instructions for this profile, e.g. `'Preserve the author’s voice.'`. |
| `options` | Supply model request parameters such as `{'temperature': 0.5}`. |
| `models` | Suggested model IDs for the console picker. |
| `efforts` | Allowed effort names, sent as `reasoning_effort`, or a dictionary mapping names to server-specific request options. |
| `mode_efforts` | Choose a configured effort for `completion`, `rewrite`, `chat`, or `delegate`. An explicit console effort takes precedence. |
| `mode_options` | Override parameters by mode, e.g. `{'completion': {'temperature': 0}}`. The keys are `completion`, `rewrite`, `chat`, and `delegate`. |
| `structured_outputs` | Set to `True` if the endpoint supports JSON Schema for edits and JSON objects for delegation. |
| `gzip` | Set to `True` if the endpoint accepts gzip-compressed requests. Runrist reuses HTTP connections between prompts. |

For automatic suggestions, enable `M-x ai-completion-mode` in the buffer.
`setq(ai_completion_delay=0.7)` sets the typing pause in seconds. Changing
profiles from a transcript with `b` starts a fresh conversation with the new
profile. The previous transcript remains available through `C-x b`.

For a loopback inference server on a private VPN, [the optional Go
gateway](tools/ai-gateway/main.go) forwards the model list and streaming
chat endpoints. Build it with `go build tools/ai-gateway/main.go` and run
`./main -help` for its arguments. Bind to the VPN interface and specify
the client VPN address, upstream origin, and a private bearer-token file.
The VPN encrypts the connection. Only the gateway handles compressed request
bodies, so the inference server can keep its existing configuration.

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
