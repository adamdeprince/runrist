# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Project tools for backends that can only exchange text."""

import json
import shlex
from pathlib import Path

from . import files
from .ai import Edit, append, offer, parse_json, request
from .errors import EditorError

_INSTRUCTIONS = """Work on the user's delegated task. Respond with one JSON object per turn.
Use the following actions, with project-relative paths. Wait for each tool result.
{"action":"list","path":"."} lists one directory.
{"action":"read","path":"..."} reads the current buffer, including unsaved edits.
{"action":"edit","path":"...","old":"exact unique text","new":"replacement"} proposes
one edit. Read the file first. Use old="" only for an empty or new file.
Edits enter editor buffers. The user saves them to disk.
{"action":"run","argv":["executable","argument"]} asks the user to run a command on disk.
{"action":"finish","message":"..."} reports results. Do not claim unperformed checks.
Use these actions for all work. The transcript follows.
"""


class Task:
    def __init__(self, buffer, backend, cwd):
        self.buffer = buffer
        self.backend = backend
        self.cwd = cwd
        self.snapshots = {}

    def step(self):
        request(self.buffer, self.backend, _INSTRUCTIONS + self.buffer.string(),
                self.cwd, self.receive, mode='delegate')

    def result(self, text):
        append(self.buffer, '\nTool: ' + text + '\n')
        self.step()

    def path(self, value):
        path = (self.cwd / value).resolve()
        if not path.is_relative_to(self.cwd):
            raise EditorError('Requested path is outside the project')
        return path

    def receive(self, answer):
        op = parse_json(answer)
        action = op['action']
        if action == 'finish':
            append(self.buffer, '\nAgent: ' + op['message'] + '\n')
            return
        if action == 'run':
            self.run(op['argv'])
            return
        path = self.path(op['path'])
        append(self.buffer, f'\nAgent: {action} {path.relative_to(self.cwd)}\n')
        if action == 'list':
            self.result(json.dumps(sorted(p.name for p in path.iterdir())))
            return
        if path.exists() and not path.is_file():
            raise EditorError('Choose a regular file')
        if action == 'read':
            b = files.find_file_noselect(str(path))
            self.snapshots[path] = b, b.text.version
            self.result(json.dumps(b.string()))
        elif action == 'edit':
            if path not in self.snapshots:
                self.result('Read the file before editing it. Read new files as empty.')
                return
            b, version = self.snapshots[path]
            if not b.live or version != b.text.version or files.changed_on_disk(b):
                raise EditorError('Source changed since the agent read it. Request a fresh edit')
            old, new = op['old'], op['new']
            source = b.string()
            if not isinstance(old, str) or not isinstance(new, str):
                raise EditorError('Both edit operands must be text')
            if (old and source.count(old) != 1) or (not old and b.size):
                self.result('Replacement needs a unique match in the current buffer.')
                return
            start = b.point_min + source.index(old)
            edit = Edit(b, version, start, start + len(old), new)
            offer(self.buffer, edit.diff(), edit.apply,
                  lambda yes: self.result('Edit applied to buffer.' if yes else 'Edit rejected.'))
        else:
            raise EditorError(f'Unknown AI action {action!r}')

    def run(self, argv):
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise EditorError('Expected a nonempty list of command arguments')
        description = f'Run in {self.cwd} with your account permissions?\n{shlex.join(argv)}'
        def accepted():
            request(self.buffer, argv, '', self.cwd, self.result, capture_status=True)
        offer(self.buffer, description, accepted,
              lambda yes: None if yes else self.result('Command rejected.'))
