# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The ``runrist`` command."""

import argparse
import asyncio
import signal
import sys
import traceback

from . import __version__


def parse(argv):
    parser = argparse.ArgumentParser(
        prog='runrist',
        description='A live, Python-extensible terminal text editor in the Emacs tradition.')
    parser.add_argument('files', nargs='*', metavar='FILE',
                        help='files to visit; +N before a file goes to line N')
    parser.add_argument('-Q', '--quick', action='store_true', help='do not load init.py')
    parser.add_argument('-e', '--eval', action='append', default=[], metavar='CODE',
                        help='evaluate Python CODE after start-up; may be repeated')
    parser.add_argument('--version', action='version', version=f'runrist {__version__}')
    return parser.parse_args(argv)


def visit(arguments):
    """Visit the files named on the command line; return how many."""
    from . import files
    from .editor import ed
    line = None
    visited = 0
    first = None
    for argument in arguments:
        if argument.startswith('+') and argument[1:].isdigit():
            line = int(argument[1:])
            continue
        try:
            buffer = files.find_file_noselect(argument)
        except Exception as error:
            ed.report_error(error, argument)
            continue
        visited += 1
        first = first or buffer
        if line is not None:
            buffer.goto_line(line)
            line = None
    if first is not None:
        ed.switch_to_buffer(first)
    return visited


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    from . import startup
    from .term import Tty, stdio_is_terminal
    if not stdio_is_terminal():
        print('runrist: standard input and output must be a terminal', file=sys.stderr)
        return 2
    startup.define_everything()
    from .editor import ed
    from .errors import ExitEditor
    loop = asyncio.new_event_loop()
    tty = Tty(loop)
    ed.setup(tty)

    def leave(signum, frame):
        from . import files
        try:
            files.do_auto_save()
        finally:
            raise ExitEditor(128 + signum)

    for name in ('SIGTERM', 'SIGHUP'):
        signal.signal(getattr(signal, name), leave)

    failure = None
    code = 0
    tty.start()
    try:
        if not args.quick:
            startup.load_init_file()
        startup.after_init(args)
        if not visit(args.files):
            startup.splash()
        code = ed.run()
    except ExitEditor as leaving:
        code = leaving.code
    except BaseException:
        failure = traceback.format_exc()
        try:
            from . import files
            files.do_auto_save()
        except Exception:
            pass
        code = 1
    finally:
        tty.stop()
        loop.close()
    if failure:
        print(failure, file=sys.stderr)
        print('runrist stopped on an internal error.  Unsaved file buffers were auto-saved; '
              'M-x recover-this-file brings them back.', file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
