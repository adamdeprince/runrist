# This file is part of runrist. GPL-3.0-or-later; see COPYING.
"""Use the configured provider executable for interactive account management."""

import os
import subprocess
import sys

from . import minibuffer as mini
from .ai_profiles import check_argv, profile
from .command import command
from .editor import ed
from .errors import EditorError


def auth_profiles(action=None):
    """Find the command profiles that configure a login or status action."""
    found = {}
    for name in ed.var('ai_backends'):
        try:
            spec = profile(name)
        except EditorError:
            continue
        if 'command' in spec and (spec.get(action) if action else spec.get('login') or spec.get('auth_status')):
            found[name] = spec
    return found


def _profile(action):
    profiles = auth_profiles(action)
    if not profiles:
        raise EditorError('No Claude or Codex command profile. Use M-x ai-configure to add one')
    name = next(iter(profiles))
    if len(profiles) > 1:
        current = getattr(ed.current_buffer, 'ai_backend', ed.var('ai_backend'))
        name = mini.completing_read('Account for profile: ', list(profiles), require_match=True,
                                    default=current if current in profiles else name,
                                    history='ai-auth-profile')
    return name, profiles[name]


def _run(executable, args, *, stdin=None):
    """Give the CLI the real terminal, then restore and redraw the editor."""
    from .ai import project
    cwd = project(ed.current_buffer)
    try:
        with ed.tty.suspended():
            result = subprocess.run([executable, *args], input=stdin, cwd=cwd)
            print('\nPress Return to go back to Runrist.', flush=True)
            sys.stdin.readline()
    except FileNotFoundError as error:
        raise EditorError(f'Could not find {error.filename}. Check the installed CLI and '
                          'its executable path in M-x ai-configure') from None
    except OSError as error:
        raise EditorError(f'Could not run {executable}: {error.strerror}') from None
    finally:
        ed.display.invalidate()
    return result.returncode


@command()
def ai_login():
    """Sign in to a Claude or Codex profile using the provider's own login flow."""
    name, spec = _profile('login')
    executable = check_argv(spec['command'], 'command')[0]
    methods = spec['login']
    method = mini.completing_read(f'Sign in to {name} with: ', list(methods),
                                  require_match=True, default=next(iter(methods)))
    args = methods[method]
    stdin = None
    if isinstance(args, dict):
        if env_name := args.get('stdin_env'):
            key = os.environ.get(env_name)
            if not key:
                raise EditorError(f'Set {env_name} in the environment that starts Runrist')
            stdin = key.encode() + b'\n'
        args = args.get('args')
    code = _run(executable, check_argv(args, 'login arguments'), stdin=stdin)
    if code:
        raise EditorError(f'{name} sign-in exited with status {code}. '
                          'M-x ai-auth-status checks the account')
    ed.message(f'{name} sign-in completed', log=False)


@command()
def ai_auth_status():
    """Show the provider's authentication status for a Claude or Codex profile."""
    name, spec = _profile('auth_status')
    executable = check_argv(spec['command'], 'command')[0]
    code = _run(executable, check_argv(spec['auth_status'], 'auth_status'))
    if code:
        raise EditorError(f'{name} auth status exited with status {code}. '
                          'Use M-x ai-login to sign in')
    ed.message(f'{name} account status checked', log=False)
