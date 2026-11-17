# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Running commands by name, and setting variables."""

from .. import variables
from ..command import command, find_command
from ..editor import ed, global_map
from ..errors import EditorError
from ..minibuffer import read_command, read_from_minibuffer, read_variable


def buf():
    return ed.current_buffer


@command('P')
def execute_extended_command(arg=None):
    """Read a command name and run that command."""
    prompt = 'M-x '
    if arg is not None:
        ed.prefix_arg = arg
        prompt = ed._describe_prefix((), False) + ' M-x '
        ed.prefix_arg = None
    name = read_command(prompt)
    record = find_command(name)
    if record is None:
        raise EditorError(f'{name} is not a command')
    ed.this_command = record.name
    ed.current_prefix_arg = arg
    ed.call_interactively(record)


def _read_setting():
    name = read_variable('Set variable: ')
    current = variables.get(name)
    text = read_from_minibuffer(f'Set {name.replace("_", "-")} to value: ', repr(current),
                                history='eval')
    from .. import live, user
    report = live.eval_source(user, text, '<minibuffer>')
    if report.error is not None:
        raise EditorError(f'{type(report.error).__name__}: {report.error}')
    return [name, report.value]


@command(interactive=_read_setting)
def set_variable(name, value):
    """Set a variable to the value of a Python expression.

    A variable that is local to buffers is set in the current buffer only."""
    variables.setq(name, value)
    ed.display.invalidate()
    ed.message(f'{name.replace("_", "-")} is now {value!r}', log=False)


for _keys, _name in (('M-x', 'execute-extended-command'), ('<menu>', 'execute-extended-command')):
    global_map.bind(_keys, _name)
