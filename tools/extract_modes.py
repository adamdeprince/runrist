#!/usr/bin/env python3
# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Rebuild runrist/modes/data from the Emacs installed on this machine.

    python tools/extract_modes.py            # every mode in MODES
    python tools/extract_modes.py sh-mode    # just these

Runs ``tools/emacs-extract.el`` in a batch Emacs, checks that every regular
expression it wrote can be translated, and writes one JSON file per mode plus
``index.json``, which is what runrist reads at start-up to know which files
select which mode.  The output is derived from GNU Emacs.
"""

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runrist import eregex                     # noqa: E402
from runrist.syntax import Syntax              # noqa: E402

DATA = ROOT / 'runrist' / 'modes' / 'data'

MODES = '''
python-mode emacs-lisp-mode lisp-mode lisp-data-mode scheme-mode js-mode js-json-mode sh-mode
makefile-mode makefile-gmake-mode makefile-bsdmake-mode makefile-automake-mode
conf-mode conf-unix-mode conf-toml-mode conf-space-mode conf-colon-mode conf-windows-mode
conf-javaprop-mode conf-desktop-mode css-mode html-mode sgml-mode latex-mode plain-tex-mode
ruby-mode perl-mode sql-mode diff-mode pascal-mode fortran-mode f90-mode tcl-mode awk-mode
bat-mode asm-mode prolog-mode octave-mode lua-mode rst-mode m4-mode autoconf-mode dns-mode
nroff-mode ld-script-mode texinfo-mode bibtex-mode change-log-mode verilog-mode vhdl-mode
'''.split()

KNOWN_PARENTS = ('prog-mode', 'text-mode', 'special-mode', 'fundamental-mode')


def check(mode, data):
    """Translate every regexp of a mode; return the ones that would not."""
    syntax = Syntax.from_emacs(data['font_lock_syntax'] or data['syntax'])
    bad = []

    def one(pattern):
        try:
            eregex.compile(pattern, syntax, bool(data['case_fold']))
        except Exception as error:      # noqa: BLE001
            bad.append((pattern, str(error)))

    def highlight(h):
        if 'anchor' in h:
            if h['anchor'].get('re'):
                one(h['anchor']['re'])
            for sub in h['highlights']:
                highlight(sub)

    for kw in data['keywords']:
        if kw['matcher'].get('re'):
            one(kw['matcher']['re'])
        for h in kw['highlights']:
            highlight(h)
    return bad


def title_of(mode, data):
    if data.get('title'):
        return data['title']
    return mode.removesuffix('-mode').replace('-', ' ').title()


def main(argv):
    wanted = argv or MODES
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as out:
        env = dict(os.environ, MODES=' '.join(wanted), OUT=out)
        result = subprocess.run(
            ['emacs', '--batch', '-Q', '-l', str(ROOT / 'tools' / 'emacs-extract.el')],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        for line in result.stdout.splitlines():
            if not line.endswith(': ok'):
                print('emacs:', line)
        index_path = DATA / 'index.json'
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
        for mode in wanted:
            path = pathlib.Path(out) / f'{mode}.json'
            if not path.exists():
                print(f'{mode}: Emacs wrote nothing')
                continue
            data = json.loads(path.read_text())
            bad = check(mode, data)
            for pattern, error in bad:
                print(f'{mode}: cannot translate {pattern[:60]!r}: {error}')
            parent = next((a for a in data['ancestors']
                           if a in KNOWN_PARENTS or a in wanted or a in index), 'fundamental-mode')
            auto = []
            for pattern in data['auto_mode']:
                try:
                    auto.append(eregex.translate(pattern)[0])
                    re.compile(auto[-1])
                except Exception as error:      # noqa: BLE001
                    print(f'{mode}: bad file pattern {pattern!r}: {error}')
                    auto.pop()
            interpreters = []
            for pattern in data['interpreters']:
                try:
                    interpreters.append(eregex.translate(pattern)[0])
                    re.compile(interpreters[-1])
                except Exception as error:      # noqa: BLE001
                    print(f'{mode}: bad interpreter pattern {pattern!r}: {error}')
                    interpreters.pop()
            index[mode] = {'title': title_of(mode, data), 'parent': parent, 'auto': auto,
                           'interpreters': interpreters, 'source': data.get('source'),
                           'emacs': data.get('emacs')}
            (DATA / f'{mode}.json').write_text(
                json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n')
            functions = sum(1 for kw in data['keywords'] if 'fn' in kw['matcher'])
            print(f'{mode:26} {len(data["keywords"]):3} keywords, {functions} by function, '
                  f'{len(bad)} untranslatable')
        index_path.write_text(json.dumps(dict(sorted(index.items())), indent=1,
                                         ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
