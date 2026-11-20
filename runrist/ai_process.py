# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Pipe one UTF-8 prompt to an argv and deliver its answer on the editor loop."""

import asyncio
import codecs
import io
import os
import signal

from .editor import ed


class Request:
    def __init__(self, owner, argv, prompt, directory, finished, failed, stream, capture_status=False):
        self.owner = owner
        self.active = True
        self.task = ed.loop.create_task(
            self.run(argv, prompt, directory, finished, failed, stream, capture_status))
        _tasks.add(self.task)
        self.task.add_done_callback(_tasks.discard)

    def deliver(self, callback, *args):
        def posted():
            if self.active and self.owner.live:
                callback(*args)
        ed.post(posted)

    def cancel(self):
        self.active = False
        self.task.cancel()

    async def run(self, argv, prompt, directory, finished, failed, stream, capture_status=False):
        proc = None
        drained = False
        try:
            if isinstance(argv, dict):
                from .ai_http import complete
                answer = await complete(argv, prompt,
                    (lambda piece: self.deliver(stream, piece)) if stream else None)
                self.deliver(finished, answer)
                return
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=directory, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)

            async def feed():
                try:
                    proc.stdin.write(prompt.encode('utf-8'))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

            async def output():
                decoder = codecs.getincrementaldecoder('utf-8')()
                pieces = []
                while True:
                    # A pipe read size controls scheduling, never total output.
                    data = await proc.stdout.read(io.DEFAULT_BUFFER_SIZE)
                    piece = decoder.decode(data, final=not data)
                    if piece:
                        pieces.append(piece)
                        if stream:
                            self.deliver(stream, piece)
                    if not data:
                        return ''.join(pieces)

            async with asyncio.TaskGroup() as group:
                group.create_task(feed())
                answer = group.create_task(output())
                errors = group.create_task(proc.stderr.read())
            status = await proc.wait()
            drained = True
            if capture_status:
                detail = errors.result().decode('utf-8', 'replace')
                self.deliver(finished, f'Exit {status}\n{answer.result()}\n{detail}')
                return
            if status:
                detail = errors.result().decode('utf-8', 'replace').strip()
                raise ValueError(f'{argv[0]} exited with status {status}\n{detail}'.rstrip())
            self.deliver(finished, answer.result())
        except Exception as error:
            while isinstance(error, ExceptionGroup):
                error = error.exceptions[0]
            self.deliver(failed, str(error) or type(error).__name__)
        finally:
            if proc and not drained:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                # Drain pipes even if cancellation interrupted a large response.
                await proc.communicate()


_tasks = set()


def shutdown(loop):
    """Reap editor-owned processes before closing their event loop."""
    tasks = [task for task in _tasks if task.get_loop() is loop]
    for task in tasks:
        task.cancel()
    if tasks:
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    from . import ai_http
    loop.run_until_complete(ai_http.close(loop))
