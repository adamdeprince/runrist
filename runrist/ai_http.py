# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Streaming chat completions over a reusable HTTP connection."""

import asyncio
import gzip
import json
from pathlib import Path

import httpx

_clients = {}


async def close(loop):
    client = _clients.pop(loop, None)
    if client is not None:
        await client.aclose()


async def complete(config, prompt, stream):
    loop = asyncio.get_running_loop()
    if loop not in _clients:
        # Keep idle connections available while the user is editing between turns.
        limits = httpx.Limits(max_connections=None, max_keepalive_connections=None,
                              keepalive_expiry=None)
        _clients[loop] = httpx.AsyncClient(trust_env=False, timeout=None, limits=limits)
    messages = []
    if config.get('system'):
        messages.append({'role': 'system', 'content': config['system']})
    messages.append({'role': 'user', 'content': prompt})
    body = dict(config.get('options', {}), model=config['model'], messages=messages, stream=True)
    data = json.dumps(body, separators=(',', ':')).encode()
    headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
    if config.get('gzip'):
        data = await asyncio.to_thread(gzip.compress, data, compresslevel=1)
        headers['Content-Encoding'] = 'gzip'
    if config.get('token_file'):
        token = Path(config['token_file']).expanduser().read_text().strip()
        headers['Authorization'] = f'Bearer {token}'
    parts = []
    fields = []
    finished = False
    async with _clients[loop].stream('POST', config['url'], headers=headers, content=data) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith('data:'):
                fields.append(line[5:].lstrip(' '))
            elif not line and fields:
                event = '\n'.join(fields)
                fields.clear()
                if event == '[DONE]':
                    if not finished:
                        raise ValueError('Server ended the answer without a finish reason')
                    # Consume the HTTP body through EOF so the socket returns
                    # to the pool even if the final chunk follows this event.
                    continue
                chunk = json.loads(event)
                if 'error' in chunk:
                    raise ValueError(str(chunk['error']))
                for choice in chunk.get('choices', []):
                    text = choice.get('delta', {}).get('content')
                    if text:
                        parts.append(text)
                        if stream:
                            stream(text)
                    reason = choice.get('finish_reason')
                    if reason and reason != 'stop':
                        raise ValueError(f'Incomplete model response ({reason})')
                    if reason == 'stop':
                        finished = True
    if not finished or fields:
        raise ValueError('Connection closed before the answer was complete')
    return ''.join(parts)
