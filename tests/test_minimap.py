# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
import base64
import re
import zlib

from runrist import minimap
from runrist.testing import run, start

SOURCE = 'import os\n\n\ndef f(x):\n    # a comment\n    return "s" + x\n' + 'y = 1\n' * 40


def decode(output):
    """Width, height and pixels of the last image in terminal output."""
    packets = re.findall(r'\x1b_G([^;\x1b]*);([^\x1b]*)\x1b\\', output)
    head = dict(item.split('=') for item in packets[0][0].split(','))
    data = zlib.decompress(base64.standard_b64decode(''.join(p[1] for p in packets)))
    return head, data


def enable_graphics(ed, palette):
    ed.tty.caps.graphics = True
    ed.tty.caps.cell = (8, 16)
    ed.tty.caps.palette_images = palette
    ed.tty.output.clear()


def test_minimap_is_one_dot_per_character():
    ed = start(SOURCE, mode='python-mode', width=80, height=12)
    enable_graphics(ed, palette=True)
    ed.redisplay(force=True)
    head, data = decode(''.join(ed.tty.output))
    w = ed.selected_window
    assert w.minimap_w == 14 and head['f'] == '408'
    width, height = int(head['s']), int(head['v'])
    assert (width, height) == (14 * 8, w.body_h * 16)
    assert (head['c'], head['r']) == ('14', str(w.body_h))
    table, pixels = data[:1024], data[1024:]
    assert len(pixels) == width * height

    def row(line):                      # one pixel across and two down per character
        return pixels[line * 2 * width:line * 2 * width + width]

    blank = 128                         # an empty pixel inside the band
    # Line 4, "def f(x):": keyword, gap, name, then punctuation and the argument.
    line = row(3)
    assert line[0] == line[1] == line[2] != blank and line[3] == blank
    assert line[4] not in (blank, line[0])              # the function name has its own color
    # Line 5 is a comment: indentation blank, words in one color, spaces between them blank.
    line = row(4)
    assert set(line[:4]) == {blank} and line[4] == line[6] == line[8] != blank
    assert line[5] == line[7] == blank
    # Point is on line 1, which is marked across its whole width.
    assert row(0)[6] == 127 and row(0)[100] == 127
    # Below what the window shows, pixels use the plain shade and blank is transparent.
    line = row(39)
    assert line[0] < 127 and line[1] == 0 and table[3] == 0 and table[4 * line[0] + 3] == 255


def test_minimap_falls_back_to_rgba_and_updates_on_edit():
    ed = start(SOURCE, mode='python-mode', width=80, height=12)
    enable_graphics(ed, palette=False)
    ed.redisplay(force=True)
    head, data = decode(''.join(ed.tty.output))
    assert head['f'] == '32' and len(data) == int(head['s']) * int(head['v']) * 4
    ed.tty.output.clear()
    ed.redisplay()
    assert '\x1b_G' not in ''.join(ed.tty.output)       # nothing changed: nothing sent
    run(ed, 'x')
    assert '\x1b_G' in ''.join(ed.tty.output)
    ed.tty.output.clear()
    run(ed, 'M-x minimap-mode RET')
    assert ed.selected_window.minimap_w == 0
    assert 'a=d,d=I' in ''.join(ed.tty.output)          # the image is freed


def test_click_in_the_minimap_moves_the_window():
    from runrist.keys import Event
    ed = start(''.join(f'line {i}\n' for i in range(400)), width=80, height=12)
    enable_graphics(ed, palette=True)
    ed.redisplay(force=True)
    w = ed.selected_window
    x = w.x + w.w - 2
    ed.tty.feed(Event('<down-mouse-1>', x=x, y=6), Event('<mouse-1>', x=x, y=6))
    run(ed)
    line = ed.current_buffer.line_at()
    assert 40 <= line <= 60                              # row 6 of 11, eight lines to a row
    assert line in [row[0] for row in w.rows]
