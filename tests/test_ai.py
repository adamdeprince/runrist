# This file is part of runrist. GPL-3.0-or-later; see COPYING.
import asyncio
import gzip
import json
import sys

import httpx
import pytest

from runrist import ai, ai_http, ai_process, files, live, minibuffer, startup, variables
from runrist.buffer import Buffer
from runrist.testing import run, screen, start


@pytest.fixture
def editor(monkeypatch, tmp_path):
    ed = start('left right')
    ed.current_buffer.directory = str(tmp_path)
    backend = variables.variables['ai_backends']
    monkeypatch.setattr(backend, 'default', {'test': [sys.executable]})
    monkeypatch.setattr(variables.variables['ai_backend'], 'default', 'test')
    yield ed
    ai_process.shutdown(ed.loop)


def drain(ed):
    while ai_process._tasks or ed._posted:
        ed._run_posted()
        tasks = list(ai_process._tasks)
        if tasks:
            ed.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))


def program(ed, code):
    ed.var('ai_backends')['test'] = [sys.executable, '-c', code]


def test_completion_preview_accept_undo_and_cancel(editor):
    ed = editor
    b = ed.current_buffer
    b.goto_char(4)
    program(ed, "import json; print(json.dumps(' hand\\nmore'))")
    ed.execute('ai-complete')
    drain(ed)
    assert b.string() == 'left right'
    assert 'left hand↵ right' in '\n'.join(screen(ed))
    run(ed, 'C-c RET')
    assert b.string() == 'left hand\nmore right'
    run(ed, 'C-/')
    assert b.string() == 'left right'
    ed.execute('ai-complete')
    ed.execute('ai-cancel')
    drain(ed)
    assert not ai.ghost(b)


def test_automatic_completion_uses_unsaved_edits(editor, monkeypatch):
    ed = editor
    program(ed, """import json,sys
data=json.loads(sys.stdin.read().split('\\n',1)[1])
print(json.dumps(data['prefix'].upper()))""")
    monkeypatch.setattr(variables.variables['ai_completion_delay'], 'default', 0)
    ed.execute('ai-completion-mode')
    run(ed, 'x')
    ed.loop.run_until_complete(asyncio.sleep(0))
    drain(ed)
    assert ai.ghost(ed.current_buffer).text == 'X'


def test_rewrite_and_stale_proposal(editor, monkeypatch):
    ed = editor
    b = ed.current_buffer
    monkeypatch.setattr(minibuffer, 'read_string', lambda prompt: 'uppercase')
    program(ed, """import json,sys
print(json.dumps(json.loads(sys.stdin.read().split('\\n',1)[1])['region']['text'].upper()))""")
    for stale in (True, False):
        ed.switch_to_buffer(b)
        b.goto_char(4)
        b.push_mark(0, activate=True)
        ed.execute('ai-rewrite')
        review = ed.current_buffer
        drain(ed)
        assert '+LEFT' in review.string()
        if stale:
            b.insert_at(b.size, '!')
            run(ed, 'a')
            assert 'Source changed' in ed.echo and b.string().startswith('left')
            run(ed, 'r')
        else:
            run(ed, 'a')
            assert b.string() == 'LEFT right!'
            ed.switch_to_buffer(b)
            run(ed, 'C-/')
            assert b.string() == 'left right!'


def test_chat_large_stream_followup_and_switch(editor, monkeypatch):
    ed = editor
    program(ed, """import sys
prompt=sys.stdin.read()
print('second answer' if 'Previous answer' in prompt else 'Previous answer '+('界'*70000))""")
    monkeypatch.setattr(minibuffer, 'read_string', lambda prompt: 'question')
    ed.execute('ai-chat')
    chat = ed.current_buffer
    drain(ed)
    assert '界' * 70000 in chat.string()
    ed.execute('ai-chat')
    drain(ed)
    assert chat.string().rstrip().endswith('second answer')
    ed.var('ai_backends')['other'] = ['unused']
    monkeypatch.setattr(minibuffer, 'completing_read', lambda *a, **k: 'other')
    ed.execute('ai-select-backend')
    assert ed.current_buffer.ai_backend == 'other'
    assert 'Previous answer' not in ed.current_buffer.string()


def test_live_init_updates_open_backend_menu(editor, tmp_path):
    path = tmp_path / 'init.py'
    def config(name):
        path.write_text(f"from runrist.api import setq\nsetq(ai_backends={{{name!r}: ['cli']}})\n")
    config('old')
    assert startup.load_init_file(str(path)).ok
    table = ai.BackendTable(editor.current_buffer)
    session = minibuffer.Session('', Buffer('menu'), None, None, table)
    assert session.refresh() == ['old']
    config('new')
    assert live.reload_path(str(path)).ok
    assert session.refresh() == ['new']


def test_delegation_reviews_buffers_and_command_results(editor, monkeypatch, tmp_path):
    ed = editor
    path = tmp_path / 'source.txt'
    path.write_text('first')
    buf = files.find_file_noselect(str(path))
    buf.insert_at(buf.size, ' unsaved')
    command = [sys.executable, '-c', 'print("check completed")']
    program(ed, f"""import json,sys
s=sys.stdin.read()
if 'Exit 0' in s:
    op=dict(action='finish',message='verified')
elif 'Edit applied to buffer.' in s:
    op=dict(action='run',argv={command!r})
elif 'Tool: "first unsaved"' in s:
    op=dict(action='edit',path='source.txt',old='first unsaved',new='second')
else:
    op=dict(action='read',path='source.txt')
print(json.dumps(op))""")
    monkeypatch.setattr(minibuffer, 'read_string', lambda prompt: 'do the task')
    ed.execute('ai-delegate')
    chat = ed.current_buffer
    drain(ed)
    assert buf.string() == 'first unsaved' and '+second' in chat.string()
    run(ed, 'a')
    drain(ed)
    assert buf.string() == 'second' and path.read_text() == 'first'
    assert 'Run in' in chat.string() and 'Exit 0' not in chat.string()
    run(ed, 'a')
    drain(ed)
    assert 'Exit 0\ncheck completed' in chat.string() and chat.string().endswith('verified\n')


def test_http_compressed_requests_and_incomplete_stream(editor, tmp_path):
    ed = editor
    bodies = []
    token = tmp_path / 'key'
    token.write_text('sample-credential')
    def handler(req):
        assert req.headers['authorization'] == 'Bearer sample-credential'
        assert req.headers['content-encoding'] == 'gzip'
        bodies.append(json.loads(gzip.decompress(req.content)))
        chunks = [{'choices': [{'delta': {'content': 'hé'}}]},
                  {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}]
        if len(bodies) == 2:
            chunks.pop()
        return httpx.Response(200, content=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks))
    ai_http._clients[ed.loop] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = dict(url='https://example.test/v1/chat/completions', model='selected',
                  token_file=token, gzip=True, system='a system prompt')
    stream = []
    assert ed.loop.run_until_complete(ai_http.complete(config, 'q', stream.append)) == 'hé'
    assert stream == ['hé'] and bodies[0]['messages'][0]['content'] == 'a system prompt'
    with pytest.raises(ValueError, match='closed'):
        ed.loop.run_until_complete(ai_http.complete(config, 'q', None))


def test_cancel_started_process_discards_queued_output(editor, tmp_path):
    ed = editor
    b = ed.current_buffer
    result = []
    marker = tmp_path / 'started'
    code = f"""from pathlib import Path
import os, time
Path({str(marker)!r}).write_text(str(os.getpid()))
print('late output', flush=True)
time.sleep(60)
"""
    ai.request(b, [sys.executable, '-c', code], '', tmp_path, result.append, result.append)
    async def started():
        while not marker.exists() or not ed._posted:
            await asyncio.sleep(0.001)
    ed.loop.run_until_complete(asyncio.wait_for(started(), 10))
    ai.cancel(b)
    drain(ed)
    assert not result
    import os
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


def test_saved_ai_edit_and_undo_reload_live_code(editor, tmp_path):
    from runrist import pyeval
    ed = editor
    path = tmp_path / 'live_example.py'
    path.write_text('def answer():\n    return 1\n')
    module, report = live.load_file(str(path), 'ai_live_example')
    assert report.ok
    function = module.answer
    b = files.find_file_noselect(str(path))
    ed.switch_to_buffer(b)
    variables.add_hook('after_save_hook', pyeval._after_save, local=True)
    i = b.string().index('1')
    ai.Edit(b, b.text.version, i, i + 1, '2').apply()
    files.write_buffer(b)
    assert function() == 2
    run(ed, 'C-/')
    files.write_buffer(b)
    assert function() == 1


def test_http_completion_uses_profile_settings_and_schema(editor):
    ed = editor
    variables.set_default('ai_backends', {'test': {
        'model': 'chosen', 'url': 'https://example.test/chat', 'structured_outputs': True,
        'options': {'temperature': 1}, 'mode_options': {'completion': {'temperature': 0}},
    }})
    def handler(req):
        payload = json.loads(req.content)
        assert payload['temperature'] == 0
        assert payload['response_format']['json_schema']['schema']['required'] == ['text']
        answer = {'choices': [{'delta': {'content': json.dumps({'text': ' more'})},
                               'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: '+json.dumps(answer)+'\n\ndata: [DONE]\n\n')
    ai_http._clients[ed.loop] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ed.execute('ai-complete')
    drain(ed)
    assert ai.ghost(ed.current_buffer).text == ' more'


def test_ai_help_first_config_save_and_reopen(editor, monkeypatch, tmp_path):
    from runrist import pyeval
    ed = editor
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    pyeval.start_watching()
    run(ed, 'C-h A')
    assert ed.current_buffer.name == '*AI Help*'
    assert 'Selected for new requests: test' in ed.current_buffer.string()
    run(ed, 'TAB RET')
    config = ed.current_buffer
    path = tmp_path / 'config' / 'runrist' / 'init.py'
    assert config.filename == str(path) and not path.exists()
    assert "'claude': {" in config.string() and "'codex': {" in config.string()
    assert 'DEFAULT_BACKENDS' not in config.string()
    config.goto_char(config.size)
    config.insert("\nsetq(ai_backend='codex')\n")
    draft = config.string()
    ed.execute('ai-configure')
    assert ed.current_buffer is config and config.string() == draft
    run(ed, 'C-x C-s')
    assert ed.var('ai_backend') == 'codex'
    assert path.read_text() == draft
    run(ed, 'C-h A')
    assert 'Selected for new requests: codex' in ed.current_buffer.string()
    run(ed, 'TAB RET')
    assert ed.current_buffer.string() == draft and not ed.current_buffer.modified


def test_login_through_help_keeps_provider_io_out_of_editor(editor, tmp_path, monkeypatch, capfd):
    import io

    ed = editor
    executable = tmp_path / 'codex'
    executable.write_text(f'#!{sys.executable}\n' + """
import json, os, sys
from pathlib import Path
assert sys.stdin.readline().rstrip() == os.environ['OPENAI_API_KEY']
Path('invocation.json').write_text(json.dumps(sys.argv[1:]))
print("provider-only diagnostic", file=sys.stderr)
""")
    executable.chmod(0o700)
    ed.var('ai_backends')['work'] = [str(executable), 'exec', '-']
    ed.var('ai_backends')['claude'] = ['claude', '-p']
    key = 'test-credential-only'
    monkeypatch.setenv('OPENAI_API_KEY', key)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'))
    def choose(prompt, options, **kwargs):
        if prompt.startswith('Account for profile'):
            assert set(options) == {'claude', 'work'}
            return 'work'
        return 'API key from OPENAI_API_KEY'
    monkeypatch.setattr(minibuffer, 'completing_read', choose)
    run(ed, 'C-h A')
    page = ed.current_buffer
    page.goto_char(page.string().index('Sign in to Claude or Codex'))
    run(ed, 'RET')
    assert ed.echo == 'work sign-in completed'
    assert ed.var('ai_backend') == 'test'
    assert json.loads((tmp_path / 'invocation.json').read_text()) == ['login', '--with-api-key']
    assert 'provider-only diagnostic' in capfd.readouterr().err
    for buffer in ed.buffers:
        assert key not in buffer.string() and 'provider-only diagnostic' not in buffer.string()


@pytest.mark.parametrize('cli, args', [
    ('claude', ['auth', 'status', '--text']), ('codex', ['login', 'status']),
])
def test_auth_status_restores_editor_on_failure(editor, tmp_path, monkeypatch, cli, args):
    import io
    from contextlib import contextmanager
    from runrist import ai_auth

    ed = editor
    executable = str(tmp_path / cli)
    ed.var('ai_backends')['work'] = [executable]
    suspended = False
    @contextmanager
    def terminal():
        nonlocal suspended
        suspended = True
        try:
            yield
        finally:
            suspended = False

    monkeypatch.setattr(ed.tty, 'suspended', terminal)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'))
    def failed(argv, *, input, cwd):
        assert suspended and argv == [executable, *args] and input is None
        return ai_auth.subprocess.CompletedProcess(argv, 1)
    monkeypatch.setattr(ai_auth.subprocess, 'run', failed)
    ed.display.full = False
    ed.execute('ai-auth-status')
    assert not suspended and ed.display.full
    assert 'status 1' in ed.echo and 'ai-login' in ed.echo


def test_first_launch_creates_editable_profiles_and_preserves_existing_init(editor, tmp_path, monkeypatch):
    from pathlib import Path
    from runrist import ai_auth
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    assert startup.load_init_file().ok
    path = Path(startup.init_file())
    assert 'DEFAULT_BACKENDS' not in path.read_text()
    assert set(ai_auth.auth_profiles()) == {'codex', 'claude'}
    original = path.read_text()
    path.write_text(original.replace("'--model'", "'--custom-model'"))
    assert startup.load_init_file().ok
    from runrist.api import select_ai
    select_ai('codex', model='custom')
    assert ai.configured('codex')[1][-2:] == ('--custom-model', 'custom')
    assert "'--custom-model'" in path.read_text()


def test_console_selects_model_effort_and_provider_per_session(editor, monkeypatch):
    from runrist import ai_profiles, main
    from runrist.errors import EditorError
    ed = editor
    providers = ai_profiles.legacy_profiles()
    providers['remote'] = {
        'url': 'https://example.test/chat', 'model': 'usual', 'models': ['alternate'],
        'efforts': {'fast': {'thinking': False}, 'deep': {'thinking': True}},
        'mode_efforts': {'completion': 'fast'},
    }
    variables.set_default('ai_backends', providers)
    args = main.parse(['--provider', 'codex', '--model', 'custom id', '--effort', 'high'])
    ai_profiles.apply_launch_options(args)
    assert ed.var('ai_backend') == 'codex'
    assert ai.configured('codex')[1][-4:] == ('--model', 'custom id', '-c', 'model_reasoning_effort="high"')

    # Drive the real minibuffer pickers, including an ID absent from the list.
    for command, value in [('ai-select-provider', 'remote'), ('ai-select-model', 'unlisted'),
                           ('ai-select-effort', 'deep')]:
        ed.tty.type(value)
        ed.tty.feed('RET')
        ed.execute(command)
    bodies = []
    def server(req):
        bodies.append(json.loads(req.content))
        chunk = {'choices': [{'delta': {'content': '{"text":"!"}'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
    ai_http._clients[ed.loop] = httpx.AsyncClient(transport=httpx.MockTransport(server))
    ed.execute('ai-complete')
    drain(ed)
    assert bodies[0]['model'] == 'unlisted' and bodies[0]['thinking'] is True
    assert ai.ghost(ed.current_buffer).text == '!'

    from runrist.api import select_ai
    select_ai('remote', model=None, effort=None)
    ed.execute('ai-complete')
    drain(ed)
    assert bodies[1]['model'] == 'usual' and bodies[1]['thinking'] is False
    with pytest.raises(EditorError, match='effort'):
        select_ai('codex', model='different', effort='typo')
    assert ed.var('ai_backend') == 'remote'
    select_ai('codex')
    assert ai.configured('codex')[1][-4:] == ('--model', 'custom id', '-c', 'model_reasoning_effort="high"')


@pytest.mark.parametrize('field, key', [('model', 'models'), ('effort', 'efforts')])
def test_open_setting_picker_reflects_reloaded_init(editor, tmp_path, monkeypatch, field, key):
    path = tmp_path / 'init.py'
    def write(choice):
        config = {'remote': {'url': 'https://example.test/chat', 'model': 'main', key: [choice]}}
        path.write_text(f"from runrist.api import setq\nsetq(ai_backend='remote', ai_backends={config!r})\n")
    write('old')
    assert startup.load_init_file(str(path)).ok

    def pick(prompt, table, **kwargs):
        session = minibuffer.Session('', Buffer('menu'), None, None, table)
        assert 'old' in session.refresh()
        write('new')
        assert live.reload_path(str(path)).ok
        assert 'new' in session.refresh() and 'old' not in session.refresh()
        return 'new'

    monkeypatch.setattr(minibuffer, 'completing_read', pick)
    editor.execute(f'ai-select-{field}')
    assert ai.ai_profiles.overrides('remote')[field] == 'new'


def test_legacy_import_and_argv_profiles(editor, tmp_path):
    path = tmp_path / 'init.py'
    path.write_text("from runrist.ai import DEFAULT_BACKENDS\nfrom runrist.api import setq\n"
                    "setq(ai_backends={**DEFAULT_BACKENDS, 'extra': ['cat']})\n")
    assert startup.load_init_file(str(path)).ok
    assert ai.configured('claude')[1][0] == 'claude'
    assert ai.configured('extra')[1] == ('cat',)
