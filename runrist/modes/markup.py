# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Markdown, YAML and Dockerfiles, and a few more file names for the modes taken
from Emacs.

Emacs has these three only as tree-sitter modes or not at all, so they are
written here.
"""

import re

from .. import fontlock
from ..fontlock import FontLock, keyword
from ..mode import add_auto_mode, major_mode
from ..syntax import Syntax, standard

# -- Markdown ----------------------------------------------------------------------------

_HEADING = re.compile(r'(#{1,6})[ \t]')


def _markdown_syntax():
    syntax = Syntax()
    syntax.add_string('```', escape=None, multiline=True)
    syntax.add_string('~~~', escape=None, multiline=True)
    syntax.add_comment('<!--', '-->')
    syntax.string_face = lambda context, text, start, delimiter: 'markdown-code-face'
    return syntax


_markdown = FontLock(_markdown_syntax(), [
    keyword(r'^#{1,6}[ \t].*$', 'markdown-header-face'),
    keyword(r'^(?:=+|-+)[ \t]*$', 'markdown-header-face'),
    keyword(r'^[ \t]*(?:[-*+]|\d+[.)])[ \t]', 'markdown-list-face'),
    keyword(r'^[ \t]*>.*$', 'markdown-blockquote-face'),
    keyword(r'`[^`\n]+`', 'markdown-code-face'),
    keyword(r'(\[[^\]\n]*\])(\([^)\n]*\))', (1, 'markdown-link-face'), (2, 'shadow')),
    keyword(r'<https?://[^>\s]+>|\bhttps?://[^\s)>\]]+', 'markdown-link-face'),
    keyword(r'(\*\*|__)(?=\S)(.+?)(?<=\S)\1', (0, 'markdown-bold-face')),
    keyword(r'(?<![\w*_])([*_])(?=[^\s*_])(.+?)(?<=[^\s*_])\1(?![\w*_])', (0, 'markdown-italic-face')),
    keyword(r'^(?:[-*_][ \t]*){3,}$', 'shadow'),
    keyword(r'^\|.*\|[ \t]*$', 'font-lock-doc-markup-face'),
])


def markdown_section_end(buffer, line):
    """Last line of the section a heading on ``line`` starts."""
    found = _HEADING.match(buffer.text.line(line))
    if found is None or fontlock.in_string_or_comment(buffer, buffer.text.line_start(line) + 1):
        return None
    level = len(found.group(1))
    last = line
    count = buffer.text.line_count
    for i in range(line + 1, count):
        text = buffer.text.line(i)
        other = _HEADING.match(text)
        if other and len(other.group(1)) <= level and not fontlock.in_string_or_comment(
                buffer, buffer.text.line_start(i) + 1):
            break
        if text.strip():
            last = i
    return last if last > line else None


@major_mode('markdown-mode', 'Markdown', parent='text-mode',
            auto=[r'\.(?:md|markdown|mkd|mdown|mdx)$'])
def markdown_mode(buffer):
    """For Markdown.  Headings fold: ``<backtab>`` shows the document as an outline."""
    fontlock.enable(buffer, _markdown)
    buffer.set('comment_start', '<!-- ')
    buffer.set('comment_end', ' -->')
    buffer.set('fold_block_function', markdown_section_end)
    buffer.set('defun_start_regexp', r'^#{1,6}[ \t]')


# -- YAML --------------------------------------------------------------------------------

def _yaml_syntax():
    syntax = Syntax()
    syntax.classes.update({'"': '"', "'": '"', '\\': '\\', '_': '_', '-': '_'})
    syntax._derive()
    syntax.strings = []
    syntax.add_string('"', multiline=True)
    syntax.add_string("'", escape=None, multiline=True)
    return syntax


_yaml = FontLock(_yaml_syntax(), [
    keyword(r'(?:^|[ \t])(#.*)$', (1, 'font-lock-comment-face')),
    keyword(r'^(?:---|\.\.\.)[ \t]*$', 'font-lock-preprocessor-face'),
    keyword(r'^[ \t]*(?:-[ \t]+)*([^\s#:\[\]{},&*!|>\'"%@`][^#:\n]*?)[ \t]*:(?=[ \t]|$)',
            (1, 'font-lock-variable-name-face')),
    keyword(r'[&*][\w-]+', 'font-lock-function-name-face'),
    keyword(r'!!?[\w/.-]*', 'font-lock-type-face'),
    keyword(r'(?<=[:\-\[,] )(?:true|false|yes|no|on|off|null|~)(?=[ \t]*(?:#|$|[,\]}]))',
            'font-lock-constant-face', flags=re.IGNORECASE),
    keyword(r'(?<=[:\-\[,] )[-+]?(?:\d[\d_]*\.?\d*(?:[eE][-+]?\d+)?|0x[0-9a-fA-F]+)'
            r'(?=[ \t]*(?:#|$|[,\]}]))', 'font-lock-number-face'),
    keyword(r'^[ \t]*-(?=[ \t]|$)', 'font-lock-punctuation-face'),
    keyword(r'[|>][-+]?[ \t]*$', 'font-lock-keyword-face'),
])


@major_mode('yaml-mode', 'YAML', parent='prog-mode',
            auto=[r'\.ya?ml$', r'(?:^|/)\.clang-(?:format|tidy)$'])
def yaml_mode(buffer):
    """For YAML."""
    fontlock.enable(buffer, _yaml)
    buffer.set('comment_start', '# ')
    buffer.set('tab_width', 2)
    buffer.set('indent_tabs_mode', False)


# -- Dockerfiles -------------------------------------------------------------------------

def _docker_syntax():
    syntax = standard()
    syntax.remove_string('"')
    syntax.add_string('"', multiline=False)
    syntax.add_string("'", escape=None, multiline=False)
    syntax.add_comment('#')
    return syntax


_docker = FontLock(_docker_syntax(), [
    keyword(r'^[ \t]*(FROM|RUN|CMD|LABEL|MAINTAINER|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER'
            r'|WORKDIR|ARG|ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b', (1, 'font-lock-keyword-face'),
            flags=re.IGNORECASE),
    keyword(r'\b(AS)\b', (1, 'font-lock-keyword-face'), flags=re.IGNORECASE),
    keyword(r'\$\{?[\w]+\}?', 'font-lock-variable-name-face'),
    keyword(r'--[\w-]+(?==)', 'font-lock-builtin-face'),
])


@major_mode('dockerfile-mode', 'Dockerfile', parent='prog-mode',
            auto=[r'(?:^|/)(?:Dockerfile|Containerfile)(?:\.[\w.-]+)?$', r'\.dockerfile$'])
def dockerfile_mode(buffer):
    """For Dockerfiles."""
    fontlock.enable(buffer, _docker)
    buffer.set('comment_start', '# ')


# -- more names for the modes from Emacs ---------------------------------------------------
# Emacs reaches these through aliases and helper functions that the extraction
# does not follow.

for _pattern, _mode in (
        (r'\.[cm]?jsx?$', 'js-mode'),
        (r'\.x?html?$', 'html-mode'),
        (r'\.(?:xml|xsl|xslt|xsd|svg|plist|rss|atom)$', 'sgml-mode'),
        (r'\.(?:tex|ltx|sty|cls|bbl)$', 'latex-mode'),
        (r'\.(?:conf|cfg|cnf|ini|properties)$', 'conf-unix-mode'),
        (r'(?:^|/)\.(?:gitconfig|gitmodules|editorconfig|npmrc)$', 'conf-unix-mode'),
        (r'(?:^|/)\.(?:gitignore|gitattributes|dockerignore|ignore)$', 'conf-unix-mode'),
        (r'(?:^|/)(?:GNU)?[Mm]akefile(?:\.[\w.-]+)?$|\.mk$', 'makefile-gmake-mode'),
        (r'(?:^|/)CMakeLists\.txt$|\.cmake$', 'conf-unix-mode'),
        (r'\.(?:el|eld)$', 'emacs-lisp-mode'),
        (r'(?:^|/)\.(?:bash|zsh|profile|zprofile|zshenv|bash_profile)(?:rc)?$', 'sh-mode'),
        (r'\.lock$', 'conf-toml-mode'),
        (r'\.(?:proto|vim|org)$', 'text-mode')):
    add_auto_mode(_pattern, _mode)

