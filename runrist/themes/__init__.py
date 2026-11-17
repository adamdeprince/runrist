# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""The standard faces and the two themes that ship with the editor.

``runestone`` is the default: slate, with the warm colors of carved and
weathered stone.  ``birch`` is its light counterpart.  Both are produced from
a palette by ``faces_for``, which is also the easy way to make another::

    from runrist.themes import faces_for, RUNESTONE
    deftheme('mine', faces_for({**RUNESTONE, 'accent': '#ff8800'}))
"""

from ..faces import blend, defface, deftheme

RUNESTONE = {
    'bg': '#14171c', 'bg_line': '#1b1f26', 'bg_region': '#2a3a55', 'bg_pop': '#1e232b',
    'bg_mode': '#262c36', 'bg_mode_off': '#1a1d23', 'border': '#2b313b',
    'fg': '#d6d0c4', 'fg_dim': '#8a93a0', 'fg_faint': '#4b525d', 'comment': '#727b88',
    'accent': '#e8915a', 'blue': '#7fb4d8', 'green': '#9cc27b', 'yellow': '#e6c36b',
    'purple': '#b497e0', 'red': '#e06c6c', 'teal': '#6cc4b0', 'pink': '#d28fc4',
    'sand': '#d9b98c', 'sage': '#8fa58a',
}

BIRCH = {
    'bg': '#f7f4ec', 'bg_line': '#eeeadf', 'bg_region': '#cfe0f2', 'bg_pop': '#ece8dc',
    'bg_mode': '#dcd6c6', 'bg_mode_off': '#ebe7dc', 'border': '#cfc9b9',
    'fg': '#2e3138', 'fg_dim': '#6c7079', 'fg_faint': '#b3ad9f', 'comment': '#8a8778',
    'accent': '#b8501c', 'blue': '#2c6a9c', 'green': '#4d7a2c', 'yellow': '#94690a',
    'purple': '#6f4bb0', 'red': '#b83232', 'teal': '#1f7f6e', 'pink': '#a0408c',
    'sand': '#7a5a26', 'sage': '#5c7558',
}


def faces_for(p):
    """Face attributes for palette ``p``."""
    bg, fg = p['bg'], p['fg']
    return {
        'default': dict(fg=fg, bg=bg),
        'cursor': dict(bg=p['accent']),
        'region': dict(bg=p['bg_region']),
        'secondary-selection': dict(bg=blend(bg, p['teal'], 0.25)),
        'highlight': dict(bg=blend(bg, p['blue'], 0.22)),
        'hl-line': dict(bg=p['bg_line']),
        'shadow': dict(fg=p['fg_dim']),
        'bold': dict(weight=700),
        'italic': dict(italic=True),
        'underline': dict(underline='line'),
        'error': dict(fg=p['red'], weight=600),
        'warning': dict(fg=p['yellow'], weight=600),
        'success': dict(fg=p['green'], weight=600),
        'link': dict(fg=p['blue'], underline='line'),
        'button': dict(fg=p['blue'], underline='line'),
        'escape-glyph': dict(fg=p['teal']),
        'trailing-whitespace': dict(bg=blend(bg, p['red'], 0.35)),
        'match': dict(bg=blend(bg, p['yellow'], 0.30)),
        'isearch': dict(fg=bg, bg=p['yellow'], weight=700),
        'lazy-highlight': dict(bg=blend(bg, p['yellow'], 0.28)),
        'isearch-fail': dict(bg=blend(bg, p['red'], 0.45)),
        'query-replace': dict(fg=bg, bg=p['accent'], weight=700),
        'show-paren-match': dict(bg=blend(bg, p['blue'], 0.38), weight=700),
        'show-paren-mismatch': dict(fg=bg, bg=p['red'], weight=700),

        'fringe': dict(fg=p['fg_faint']),
        'line-number': dict(fg=p['fg_faint']),
        'line-number-current-line': dict(fg=p['sand'], bg=p['bg_line'], weight=600),
        'fold-marker': dict(fg=p['blue']),
        'fold': dict(fg=p['blue'], bg=blend(bg, p['blue'], 0.14), italic=True),
        'continuation': dict(fg=p['fg_faint']),
        'vertical-border': dict(fg=p['border']),
        'tilde': dict(fg=p['fg_faint']),

        'mode-line': dict(fg=fg, bg=p['bg_mode']),
        'mode-line-inactive': dict(fg=p['fg_dim'], bg=p['bg_mode_off']),
        'mode-line-buffer-id': dict(weight=700),
        'mode-line-emphasis': dict(fg=p['sand'], weight=600),
        'mode-line-dim': dict(fg=p['fg_dim']),
        'mode-line-state': dict(fg=bg, bg=p['blue'], weight=700),
        'mode-line-modified': dict(fg=bg, bg=p['accent'], weight=700),
        'mode-line-read-only': dict(fg=bg, bg=p['purple'], weight=700),
        'mode-line-recording': dict(fg=bg, bg=p['red'], weight=700),
        'mode-line-mode': dict(fg=p['teal'], weight=600),
        'header-line': dict(fg=fg, bg=p['bg_mode']),

        'minibuffer-prompt': dict(fg=p['blue'], weight=700),
        'echo-area': dict(),
        'completions-selected': dict(bg=p['bg_region']),
        'completions-common-part': dict(fg=p['accent'], weight=700),
        'completions-annotations': dict(fg=p['comment'], italic=True),
        'completions-key': dict(fg=p['blue']),
        'completions-count': dict(fg=p['fg_dim']),
        'help-key-binding': dict(fg=p['blue'], bg=p['bg_pop'], weight=600),
        'help-heading': dict(fg=p['accent'], weight=700),
        'help-argument-name': dict(fg=p['sand'], italic=True),
        'which-key-key': dict(fg=p['blue'], weight=700),
        'which-key-command': dict(fg=fg),
        'which-key-group': dict(fg=p['accent']),
        'which-key-separator': dict(fg=p['fg_faint']),
        'popup': dict(fg=fg, bg=p['bg_pop']),

        'font-lock-comment-face': dict(fg=p['comment'], italic=True),
        'font-lock-comment-delimiter-face': dict(inherit='font-lock-comment-face'),
        'font-lock-doc-face': dict(fg=p['sage'], italic=True),
        'font-lock-doc-markup-face': dict(fg=p['teal']),
        'font-lock-string-face': dict(fg=p['green']),
        'font-lock-keyword-face': dict(fg=p['accent'], weight=600),
        'font-lock-builtin-face': dict(fg=p['teal']),
        'font-lock-function-name-face': dict(fg=p['blue'], weight=700),
        'font-lock-function-call-face': dict(fg=p['blue']),
        'font-lock-variable-name-face': dict(fg=p['sand']),
        'font-lock-variable-use-face': dict(),
        'font-lock-type-face': dict(fg=p['yellow']),
        'font-lock-constant-face': dict(fg=p['purple']),
        'font-lock-number-face': dict(fg=p['pink']),
        'font-lock-operator-face': dict(fg=p['fg_dim']),
        'font-lock-punctuation-face': dict(fg=p['fg_dim']),
        'font-lock-bracket-face': dict(inherit='font-lock-punctuation-face'),
        'font-lock-delimiter-face': dict(inherit='font-lock-punctuation-face'),
        'font-lock-misc-punctuation-face': dict(inherit='font-lock-punctuation-face'),
        'font-lock-property-name-face': dict(fg=blend(p['blue'], fg, 0.35)),
        'font-lock-property-use-face': dict(inherit='font-lock-property-name-face'),
        'font-lock-preprocessor-face': dict(fg=p['pink']),
        'font-lock-warning-face': dict(fg=p['red'], weight=700),
        'font-lock-negation-char-face': dict(fg=p['red']),
        'font-lock-escape-face': dict(fg=p['teal']),
        'font-lock-regexp-face': dict(fg=p['teal']),
        'font-lock-regexp-grouping-backslash': dict(fg=p['teal'], weight=700),
        'font-lock-regexp-grouping-construct': dict(fg=p['teal'], weight=700),

        'minimap-viewport': dict(bg=fg),
        'minimap-cursor-line': dict(bg=p['accent']),

        'diff-added': dict(fg=p['green']),
        'diff-removed': dict(fg=p['red']),
        'diff-changed': dict(fg=p['yellow']),
        'diff-header': dict(fg=p['blue'], weight=700),
        'diff-file-header': dict(fg=p['accent'], weight=700),
        'diff-hunk-header': dict(fg=p['purple']),
        'diff-context': dict(fg=p['fg_dim']),

        'markdown-header-face': dict(fg=p['accent'], weight=700),
        'markdown-code-face': dict(fg=p['green']),
        'markdown-bold-face': dict(weight=700),
        'markdown-italic-face': dict(italic=True),
        'markdown-link-face': dict(fg=p['blue'], underline='line'),
        'markdown-list-face': dict(fg=p['teal']),
        'markdown-blockquote-face': dict(fg=p['sage'], italic=True),

        'dired-directory': dict(fg=p['blue'], weight=700),
        'dired-symlink': dict(fg=p['teal']),
        'dired-header': dict(fg=p['accent'], weight=700),

        'splash-title': dict(fg=p['accent'], weight=800),
        'splash-rune': dict(fg=p['sand']),
        'splash-text': dict(fg=p['fg_dim']),
        'splash-key': dict(fg=p['blue'], weight=600),
    }


for _name, _attrs in faces_for(RUNESTONE).items():
    defface(_name, **_attrs)

deftheme('runestone', {}, 'Slate, with the warm colors of carved stone.  The default.')
deftheme('birch', faces_for(BIRCH), 'Light: ink on birch bark.', dark=False)
