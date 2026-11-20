# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""AI commands. Processes supply text. The editor owns buffers, previews and undo."""

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from weakref import ref

from . import ai_process, ai_profiles, files, minibuffer as mini
from .ai_profiles import configured, starter_init
from .ai_auth import ai_auth_status, ai_login, auth_profiles
from .buffer import inhibit_read_only
from .command import command
from .editor import ed, global_map
from .errors import EditorError
from .mode import major_mode, major_modes, minor_mode
from .variables import add_hook, defvar, set_default

# Older init files imported plain command lists. New init files contain full profiles.
def __getattr__(name):
    if name == 'DEFAULT_BACKENDS':
        return {key: spec['command'] for key, spec in ai_profiles.legacy_profiles().items()}
    raise AttributeError(name)


defvar('ai_backends', {},
       'AI picker entries. Map each display name to a command argument list or an HTTP '
       'profile with url and model. M-x ai-help shows examples.')
defvar('ai_backend', None, 'Name selected from ai_backends. M-x ai-select-backend changes '
       'it for this run. Set it in your init file to choose a startup default.')
defvar('ai_completion_delay', 0.7, 'Seconds to wait after typing before requesting completion.')


def backend():
    name = ed.var('ai_backend')
    if name not in ed.var('ai_backends'):
        name = ai_select_backend()
    return configured(name)


class ProfileTable(mini.Table):
    def revision(self):
        return tuple(self.all(''))


class BackendTable(ProfileTable):
    def __init__(self, source):
        super().__init__(lambda: source.get('ai_backends'), require_match=True)


@command()
def ai_configure():
    """Edit AI profiles in your personal init.py. Save with C-x C-s to apply them."""
    from .startup import init_file
    path = Path(init_file()).absolute()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    new = not path.exists() and files.buffer_visiting(str(path)) is None
    buffer = files.find_file_noselect(str(path))
    ed.switch_to_buffer(buffer)
    buffer.locals['user_init_file'] = buffer.filename
    if new:
        buffer.insert(starter_init())
        buffer.goto_char(0)


@command()
def ai_help():
    """Show AI setup, the configured profiles, and how to use them."""
    from .help import Page, view_echo_area_messages
    from .startup import init_file
    page = Page().add('AI configuration\n\n', 'help-heading')
    profiles = ed.var('ai_backends')
    selected = ed.var('ai_backend')
    page.add('Selected for new requests: ' +
             (selected if selected in profiles else '(choose on first use)') + '\n')
    if selected in profiles:
        try:
            spec = ai_profiles.profile(selected)
        except EditorError as error:
            page.add(f'{error}\n')
        else:
            values = ai_profiles.overrides(selected)
            for field in ('model', 'effort'):
                value = values.get(field, spec.get(field)) or '(provider default)'
                page.add(f'{field.capitalize()}: {value}\n')
    page.add('Profiles: ' + (', '.join(profiles) or '(none)') + '\n\n')
    page.link('Edit ' + mini.abbreviate_file_name(init_file()), ai_configure)
    page.add('  (M-x ai-configure)\n')
    page.link('View the starter init.py',
              lambda: Page().add(starter_init()).show('*AI Starter*', select=True)).add('\n')

    def choose():
        ai_select_backend()
        ai_help()

    page.link('Choose a provider', choose).add('  (M-x ai-select-provider)\n')
    if profiles:
        def set_option(field):
            _select_setting(field)
            ai_help()

        page.link('Choose a model', lambda: set_option('model')).add('  (M-x ai-select-model)\n')
        page.link('Choose effort', lambda: set_option('effort')).add('  (M-x ai-select-effort)\n')
    auth = auth_profiles()
    if any(spec.get('login') for spec in auth.values()):
        page.link('Sign in to Claude or Codex', ai_login).add('  (M-x ai-login)\n')
    if any(spec.get('auth_status') for spec in auth.values()):
        page.link('Check sign-in status', ai_auth_status).add('  (M-x ai-auth-status)\n')
    if auth:
        page.add('Follow the CLI in the terminal, then press Return to resume editing.\n')
    page.add('\n')
    page.add('Edit your profiles\n', 'help-heading')
    page.add('Edit the provider entries in your init.py, then save with C-x C-s.\n'
             'Set the startup provider by its menu name, for example:\n\n')
    page.add("    from runrist.api import setq\n    setq(ai_backend='codex')\n")
    page.add('\nFor one session, set a provider, model and effort together.\n'
             'From the shell: runrist --provider codex --model "$MODEL_ID" --effort high\n'
             "From M-: select_ai('codex', model='your-model-id', effort='high')\n")
    page.add('\nA command reads a prompt on stdin and writes its answer on stdout.\n'
             'For HTTP, set the full streaming Chat Completions URL and model ID.\n'
             'If authentication is required, set token_file to a local file holding\n'
             'the bearer token. Set system for a profile-specific instruction.\n'
             'options supplies inference parameters. mode_options overrides them\n'
             'for completion, rewrite, chat, or delegate. Enable gzip or\n'
             'structured_outputs only if the server supports those features.\n\n')
    page.add('Try a profile\n', 'help-heading')
    page.add('Run M-x ai-chat from a scratch buffer and ask a short question.\n'
             'Use ai-complete at point, ai-rewrite on a region, or ai-delegate\n'
             'for a project task. ai-completion-mode enables suggestions as you type.\n'
             'If a request fails, open ')
    page.link('*Messages*', view_echo_area_messages).add(' for the error.\n')
    page.show('*AI Help*', select=True)


@command()
def ai_select_backend():
    """Choose an AI from the live configuration. Switching agents starts a fresh chat."""
    source = ed.current_buffer
    table = BackendTable(source)
    if not table.all(''):
        raise EditorError('No AI profiles. Use M-x ai-configure to add one')
    name = mini.completing_read('AI backend: ', table, history='ai-backend',
                                default=source.get('ai_backend'))
    return select_ai(name)


_UNSET = object()


def select_ai(provider, *, model=_UNSET, effort=_UNSET):
    """Select a provider for this run. Pass None to restore a profile's model or effort."""
    changes = {key: value for key, value in [('model', model), ('effort', effort)]
               if value is not _UNSET}
    ai_profiles.select(provider, changes)
    source = ed.current_buffer
    if (changes or provider != ed.var('ai_backend')) and not getattr(source, 'ai_view', False):
        cancel(source)
    set_default('ai_backend', provider)
    if getattr(source, 'ai_session', None) is source and provider != source.ai_backend:
        new_session(provider, Path(source.directory))
    return provider


@command()
def ai_select_provider():
    """Choose the AI provider (a named profile in init.py)."""
    return ai_select_backend()


def _selection_provider():
    return getattr(ed.current_buffer, 'ai_backend', None) or ed.var('ai_backend') or ai_select_provider()


def _select_setting(field):
    name = _selection_provider()
    source = ed.current_buffer
    spec = ai_profiles.profile(name)
    if 'command' in spec and not spec.get(field + '_args'):
        raise EditorError(f'Add {field}_args to this command profile in M-x ai-configure')
    use_default = '(profile default)'
    current = ai_profiles.overrides(name).get(field, use_default)

    def candidates():
        profiles = source.get('ai_backends')
        if name not in profiles:
            return []
        latest = ai_profiles.profile(name, profiles)
        choices = ai_profiles.choices(latest, field)
        if field == 'model':
            choices = [value for value in (latest.get('model'), current) if value] + choices
        return list(dict.fromkeys([use_default, *choices]))

    value = mini.completing_read(f'{name} {field}: ', ProfileTable(candidates),
                                 default=current, history=f'ai-{name}-{field}')
    ai_profiles.select(name, {field: None if value == use_default else value})
    if not getattr(ed.current_buffer, 'ai_view', False):
        cancel(ed.current_buffer)
    ed.message(f'{name} {field}: {value}', log=False)


@command()
def ai_select_model():
    """Choose a model for this provider or enter its model ID."""
    _select_setting('model')


@command()
def ai_select_effort():
    """Choose the reasoning effort for this provider."""
    _select_setting('effort')


def project(buffer):
    directory = Path(buffer.directory).resolve()
    return next((p for p in (directory, *directory.parents) if (p / '.git').exists()), directory)


def context(buffer, start, end):
    return dict(file=buffer.filename or buffer.name, start_line=buffer.line_number(start),
                text=buffer.substring(start, end))


def parse_json(answer):
    """Accept a JSON value, optionally wrapped in a Markdown code fence."""
    answer = answer.strip()
    if answer.startswith('```') and answer.endswith('```'):
        answer = answer.partition('\n')[2][:-3]
    return json.loads(answer)


def append(buffer, text):
    at_end = buffer.pt == buffer.size
    following = [w for w in ed.frame.windows() if w.buffer is buffer
                 and ed.window_point(w) == buffer.size]
    with inhibit_read_only():
        buffer.insert_at(buffer.size, text)
    if at_end:
        buffer.pt = buffer.size
    for window in following:
        ed.set_window_point(window, buffer.size)
    buffer.set_modified(False)


def cancel(buffer):
    request = getattr(buffer, 'ai_request', None)
    if request:
        request.cancel()
        buffer.ai_request = None
    timer = getattr(buffer, 'ai_timer', None)
    if timer:
        ed.cancel_timer(timer)
        buffer.ai_timer = None
    buffer.ai_ghost = None


def request(owner, argv, prompt, cwd, done, stream=None, *, mode=None, capture_status=False):
    if getattr(owner, 'ai_request', None):
        raise EditorError('An AI request is already running here')

    def failure(detail):
        owner.ai_request = None
        if getattr(owner, 'ai_view', False):
            append(owner, f'\n[Error] {detail}\n')
        ed._log(f'AI: {detail}')
        ed.message('AI request failed. See *Messages* for details', log=False)

    def finish(text):
        owner.ai_request = None
        try:
            done(text)
        except (ValueError, KeyError, TypeError, OSError, EditorError) as error:
            failure(str(error))

    if isinstance(argv, dict) and mode:
        options = ai_profiles.http_options(argv, mode)
        if argv.get('structured_outputs') and mode in ('completion', 'rewrite'):
            options['response_format'] = {
                'type': 'json_schema', 'json_schema': {
                    'name': 'editor_text', 'strict': True,
                    'schema': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                               'required': ['text'], 'additionalProperties': False},
                },
            }
        if argv.get('structured_outputs') and mode == 'delegate':
            options['response_format'] = {'type': 'json_object'}
        argv = dict(argv, options=options)
    owner.ai_request = ai_process.Request(
        owner, argv, prompt, cwd, finish, failure, stream, capture_status=capture_status)


@command()
def ai_cancel():
    """Stop the current buffer's request or pending AI action."""
    buf = ed.current_buffer
    cancel(buf)
    buf.ai_pending = None
    if getattr(buf, 'ai_view', False):
        append(buf, '\n[Cancelled]\n')
    ed.message('AI cancelled', log=False)


@major_mode('ai-mode', 'AI', parent='special-mode')
def ai_mode(buffer):
    """AI session or review. i prompts, a accepts, r rejects, k cancels, ? shows AI help."""
    buffer.ai_view = True
    buffer.undo_enabled = False


def view(title, directory):
    buf = ed.new_buffer(f'*{title}*', 'ai-mode')
    buf.directory = str(directory)
    ed.display_buffer(buf, select=True)
    return buf


@dataclass
class Edit:
    source: object
    version: int
    start: int
    end: int
    text: str

    def apply(self):
        b = self.source
        if not b.live or b.text.version != self.version:
            raise EditorError('Source changed since this proposal. Request a fresh edit')
        if b.filename and files.changed_on_disk(b):
            raise EditorError('File changed on disk. Reconcile it and request a fresh edit')
        if not b.point_min <= self.start <= self.end <= b.point_max:
            raise EditorError('Edit is outside the accessible region')
        b.undo_boundary()
        b.replace_region(self.start, self.end, self.text)
        b.undo_boundary()
        b.goto_char(self.start + len(self.text))

    def diff(self):
        before = self.source.substring(self.start, self.end)

        def lines(text):
            result = text.splitlines(keepends=True)
            if result and not result[-1].endswith('\n'):
                result[-1] += '\n\\ No newline at end\n'
            return result
        return ''.join(difflib.unified_diff(lines(before), lines(self.text),
                                            fromfile=self.source.name, tofile='proposed'))


def offer(buffer, description, accept, reply=None):
    buffer.ai_pending = accept, reply
    append(buffer, f'\n{description}\n[a] Accept   [r] Reject\n')


def decide(accept):
    b = ed.current_buffer
    pending = getattr(b, 'ai_pending', None)
    if not pending:
        raise EditorError('Nothing to review in this buffer')
    action, reply = pending
    if accept:
        action()
    b.ai_pending = None
    append(b, '[Accepted]\n' if accept else '[Rejected]\n')
    if reply:
        reply(accept)


@command()
def ai_accept():
    """Accept the pending edit or command in the review buffer."""
    decide(True)


@command()
def ai_reject():
    """Reject the pending edit or command in the review buffer."""
    decide(False)


def suggestion(answer):
    text = parse_json(answer)
    if isinstance(text, dict):
        text = text.get('text')
    if not isinstance(text, str):
        raise ValueError('Expected a JSON object with a text string')
    return text


@command()
def ai_rewrite():
    """Preview a replacement for the active region, including unsaved text."""
    b = ed.current_buffer
    if not b.region_active() or b.read_only:
        raise EditorError('Select an editable region first')
    start, end = b.region()
    version = b.text.version
    name, argv = backend()
    instruction = mini.read_string('Rewrite region: ')
    cwd = project(b)
    review = view(f'AI rewrite {b.name}', cwd)
    append(review, f'{name} · {b.name}\n{instruction}\n')
    prompt = ('Return only a JSON object {"text":"..."} containing the exact replacement, including whitespace.\n'
              + json.dumps(dict(instruction=instruction, region=context(b, start, end))))

    def done(answer):
        if not b.live or b.text.version != version:
            raise EditorError('Source changed while rewriting. Request a fresh edit')
        edit = Edit(b, version, start, end, suggestion(answer))
        offer(review, edit.diff(), edit.apply)

    request(review, argv, prompt, cwd, done, mode='rewrite')


def complete(b, selected):
    if b.read_only or b.minibuffer:
        raise EditorError('Choose an editable buffer')
    cancel(b)
    _, argv = selected
    point, version = b.pt, b.text.version
    prompt = ('Complete the code at point. Return only a JSON object {"text":"..."} containing the exact text '
              'to insert between prefix and suffix.\n' + json.dumps(dict(
                  file=b.filename or b.name, prefix=b.substring(b.point_min, point),
                  suffix=b.substring(point, b.point_max))))

    def done(answer):
        if (b.pt, b.text.version) == (point, version) and b is ed.current_buffer:
            text = suggestion(answer)
            if text:
                b.ai_ghost = Edit(b, version, point, point, text)
                ed.message('AI completion · C-c RET accepts · M-x ai-preview-completion shows all',
                           log=False)
    request(b, argv, prompt, project(b), done, mode='completion')


@command()
def ai_complete():
    """Request an inline suggestion at point."""
    complete(ed.current_buffer, backend())


def ghost(buffer):
    edit = getattr(buffer, 'ai_ghost', None)
    if edit and edit.start == buffer.pt and edit.version == buffer.text.version:
        return edit
    return None


@command()
def ai_accept_completion():
    """Insert the current suggestion as a single undoable edit."""
    buf = ed.current_buffer
    edit = ghost(buf)
    if not edit:
        raise EditorError('No completion at point')
    edit.apply()
    buf.ai_ghost = None


@command()
def ai_preview_completion():
    """Show the entire suggested insertion in a review buffer."""
    buf = ed.current_buffer
    edit = ghost(buf)
    if not edit:
        raise EditorError('No completion at point')
    review = view(f'AI completion {buf.name}', project(buf))
    buf.ai_ghost = None
    offer(review, edit.diff(), edit.apply)


@minor_mode('ai-completion-mode', ' AI')
def ai_completion_mode(buffer, on):
    """Request inline completions after a pause in typing."""
    if on:
        backend()
    else:
        cancel(buffer)


def after_command():
    b = ed.current_buffer
    if b.minibuffer:
        return
    if getattr(b, 'ai_ghost', None) and not ghost(b):
        b.ai_ghost = None
    if not b.get('ai_completion_mode'):
        return
    if ed.this_command == 'self-insert-command':
        cancel(b)
        point, version = b.pt, b.text.version

        def idle():
            if b.live and b is ed.current_buffer and b.pt == point and b.text.version == version:
                complete(b, backend())
        b.ai_timer = ed.run_at_time(b.get('ai_completion_delay'), idle)


def new_session(name, cwd):
    chat = view(f'AI {name} {cwd.name}', cwd)
    chat.ai_session = chat
    chat.ai_backend = name
    append(chat, f'{name} · {cwd}\n')
    return chat


@command()
def ai_start():
    """Open a fresh conversation with the selected backend."""
    source = ed.current_buffer
    name, _ = backend()
    chat = new_session(name, project(source))
    if not getattr(source, 'ai_view', False):
        source.ai_session = chat


def session(delegate):
    source = ed.current_buffer
    chat = getattr(source, 'ai_session', None)
    if source is not chat and chat is not None and chat.ai_backend != ed.var('ai_backend'):
        chat = None
    if chat is not None and chat.live:
        if getattr(chat, 'ai_pending', None) or getattr(chat, 'ai_request', None):
            raise EditorError('Finish or cancel the pending action first')
        name, argv = configured(chat.ai_backend)
        cwd = Path(chat.directory)
    else:
        chat = None
        name, argv = backend()
        cwd = project(source)
    instruction = mini.read_string('Delegate task: ' if delegate else 'Ask: ')
    if not instruction.strip():
        return
    if chat is None:
        chat = new_session(name, cwd)
        source.ai_session = chat
    elif source is not chat:
        ed.display_buffer(chat, select=True)
    if source is not chat:
        start, end = source.region() if source.region_active() else (source.point_min, source.point_max)
        attachment = ref(source), source.filename, source.text.version, start, end
        if getattr(chat, 'ai_attachment', None) != attachment:
            attached = context(source, start, end)
            append(chat, f'\nContext: {json.dumps(attached, ensure_ascii=False)}\n')
            chat.ai_attachment = attachment
    append(chat, f'\nYou: {instruction}\n')
    if delegate:
        from .ai_delegate import Task
        Task(chat, argv, cwd).step()
    else:
        prompt = ('Answer the latest user message in this conversation.\n' + chat.string())
        append(chat, f'\n{name}: ')
        request(chat, argv, prompt, cwd, lambda _: append(chat, '\n'),
                lambda piece: append(chat, piece), mode='chat')


@command()
def ai_chat():
    """Ask about the active region or buffer, or continue from the transcript."""
    session(False)


@command()
def ai_delegate():
    """Delegate a project task with reviewed edits and commands."""
    session(True)


_review_map = major_modes['ai-mode'].keymap
for _key, _cmd in [('i', 'ai-chat'), ('d', 'ai-delegate'), ('a', 'ai-accept'),
                   ('r', 'ai-reject'), ('k', 'ai-cancel'), ('b', 'ai-select-backend'),
                   ('?', 'ai-help')]:
    _review_map.bind(_key, _cmd)

global_map.bind('C-c RET', 'ai-accept-completion')
add_hook('post_command_hook', after_command)


def killed():
    cancel(ed.current_buffer)


add_hook('kill_buffer_hook', killed)
