# runrist -- a live terminal text editor in the Emacs tradition.
# Copyright (C) 2026 Adam DePrince
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Runrist: a live, Python-extensible terminal text editor.

The running editor is the program.  Its source is loaded statement by
statement and patched in place, so redefining a function, a class or a key
binding takes effect at once, in every place that already refers to it.
"""

__version__ = '0.1.0'
