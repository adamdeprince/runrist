# This file is part of runrist. GPL-3.0-or-later; see COPYING.
"""Read provider settings and apply console choices to an individual request."""

import ast
import json
import re
from importlib import resources
from pathlib import Path

from .editor import ed
from .errors import EditorError


def starter_init():
    return resources.files('runrist').joinpath('templates', 'init.py').read_text(encoding='utf-8')


def legacy_profiles():
    """Read the literal starter entries for old init files that imported DEFAULT_BACKENDS."""
    tree = ast.parse(starter_init())
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == 'ai_backends':
            return ast.literal_eval(node.value)
    raise ValueError('Missing ai_backends in the packaged init.py')


def _legacy_profile(executable):
    if executable in ('claude', 'codex'):
        return legacy_profiles()[executable]
    return {}


def profile(name, profiles=None):
    if profiles is None:
        profiles = ed.var('ai_backends')
    if name not in profiles:
        raise EditorError(f'Unknown AI provider {name!r}. See M-x ai-configure')
    spec = profiles[name]
    if isinstance(spec, dict):
        return spec
    # Accept the original plain argument lists, including renamed CLI profiles.
    check_argv(spec, 'command')
    return {**_legacy_profile(Path(spec[0]).name), 'command': spec}


def check_argv(value, label):
    if (not isinstance(value, (tuple, list)) or not value or
            not all(isinstance(arg, str) for arg in value)):
        raise EditorError(f'Configure {label} as a nonempty list of arguments in init.py')
    return value


def overrides(name):
    return getattr(ed, 'ai_overrides', {}).get(name, {})


def choices(spec, field):
    values = spec.get('models' if field == 'model' else 'efforts', ())
    if not isinstance(values, (list, tuple, dict)) or not all(isinstance(v, str) for v in values):
        raise EditorError(f'Invalid {field} choices in init.py')
    return list(values)


def validate(spec, field, value):
    if 'command' in spec and not spec.get(field + '_args'):
        raise EditorError(f'Add {field}_args to this command profile in M-x ai-configure')
    if not isinstance(value, str) or not value.strip():
        raise EditorError(f'AI {field} must be a nonempty string')
    if field == 'effort':
        allowed = choices(spec, field)
        if allowed and value not in allowed:
            raise EditorError('Choose an effort from ' + ', '.join(allowed))


def select(name, changes):
    spec = profile(name)
    for field, value in changes.items():
        if value is not None:
            validate(spec, field, value)
    if not changes:
        return
    if not hasattr(ed, 'ai_overrides'):  # An editor already running during live reload.
        ed.ai_overrides = {}
    current = ed.ai_overrides.setdefault(name, {})
    for field, value in changes.items():
        if value is None:
            current.pop(field, None)
        else:
            current[field] = value


def configured(name):
    spec = profile(name)
    selected = overrides(name)
    if 'command' in spec:
        argv = list(check_argv(spec['command'], 'command'))
        for field in ('model', 'effort'):
            value = selected.get(field, spec.get(field))
            if value is not None:
                validate(spec, field, value)
                label = field + '_args'
                replacements = {'value': value, 'quoted_value': json.dumps(value)}
                for arg in check_argv(spec[label], label):
                    argv.append(re.sub(r'\{(value|quoted_value)\}', lambda m: replacements[m[1]], arg))
        return name, tuple(argv)

    model = selected.get('model', spec.get('model'))
    if not model or not spec.get('url'):
        raise EditorError(f'Configure a URL and model for AI provider {name!r}')
    result = dict(spec, model=model)
    if 'effort' in selected:
        result['effort_override'] = selected['effort']
    return name, result


def http_options(spec, mode):
    options = {**spec.get('options', {}), **spec.get('mode_options', {}).get(mode, {})}
    effort = spec.get('effort_override', spec.get('mode_efforts', {}).get(mode, spec.get('effort')))
    if effort is not None:
        validate(spec, 'effort', effort)
        presets = spec.get('efforts')
        params = presets.get(effort) if isinstance(presets, dict) else {'reasoning_effort': effort}
        if not isinstance(params, dict):
            raise EditorError('Each HTTP effort preset must be a dictionary of request parameters')
        options.update(params)
    return options


def apply_launch_options(args):
    changes = {field: getattr(args, field) for field in ('model', 'effort')
               if getattr(args, field) is not None}
    if args.provider is None and not changes:
        return
    name = args.provider or ed.var('ai_backend')
    if name is None:
        profiles = ed.var('ai_backends')
        if len(profiles) != 1:
            raise EditorError('Specify --provider or set ai_backend in init.py')
        name = next(iter(profiles))
    from .ai import select_ai
    select_ai(name, **changes)
