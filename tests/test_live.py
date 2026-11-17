# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
import os
import textwrap
import time

from runrist import live


def write(path, source):
    path.write_text(textwrap.dedent(source))
    # Make sure the stamp changes even on a coarse clock.
    stamp = time.time_ns() + write.tick
    write.tick += 1_000_000_000
    os.utime(path, ns=(stamp, stamp))


write.tick = 0

V1 = '''
    import functools
    registry = {}
    counter = [0]
    LIMIT = 10

    def deco(f):
        @functools.wraps(f)
        def wrapper(*a):
            return ('wrapped', f(*a))
        return wrapper

    def greet(name):
        return 'hello ' + name

    @deco
    def decorated(x):
        return x + 1

    class Base:
        def who(self):
            return 'base'

    class Thing(Base):
        kind = 'thing'
        instances = []
        def __init__(self, n):
            self.n = n
            Thing.instances.append(self)
        def describe(self):
            return f'{self.who()} {self.n}'
        @property
        def double(self):
            return self.n * 2
        @staticmethod
        def make():
            return Thing(0)

    registry['greet'] = greet
'''

V2 = '''
    import functools
    registry = {}
    counter = [0]
    LIMIT = 20

    def deco(f):
        @functools.wraps(f)
        def wrapper(*a):
            return ('wrapped', f(*a))
        return wrapper

    def brand_new():
        return LIMIT

    def greet(name, punct='!'):
        return 'HELLO ' + name + punct

    @deco
    def decorated(x):
        return x + 100

    class Base:
        def who(self):
            return 'BASE'

    class Thing(Base):
        kind = 'thing'
        instances = []
        def __init__(self, n):
            self.n = n
            Thing.instances.append(self)
        def who(self):
            return 'thing/' + super().who()
        def describe(self):
            return f'{self.who()} #{self.n}'
        @property
        def double(self):
            return self.n * 3
        @staticmethod
        def make():
            return Thing(99)

    registry['greet'] = greet
'''


def test_reload_patches_in_place(tmp_path):
    path = tmp_path / 'livemod.py'
    write(path, V1)
    mod, report = live.load_file(str(path), 'livemod_a')
    assert report.ok, report.traceback
    greet, decorated, Thing = mod.greet, mod.decorated, mod.Thing
    thing = Thing(7)
    bound = thing.describe
    mod.counter[0] = 41
    mod.registry['extra'] = 1
    assert greet('x') == 'hello x' and decorated(1) == ('wrapped', 2)
    assert thing.describe() == 'base 7' and thing.double == 14

    write(path, V2)
    (report,) = live.poll()
    assert report.ok, report.traceback
    # Same objects, new behaviour, everywhere they were already referenced.
    assert mod.greet is greet and greet('x') == 'HELLO x!'
    assert mod.registry['greet'] is greet
    assert mod.decorated is decorated and decorated(1) == ('wrapped', 101)
    assert mod.Thing is Thing and isinstance(thing, mod.Thing)
    assert thing.describe() == 'thing/BASE #7' and bound() == 'thing/BASE #7'
    assert thing.double == 21 and Thing.make().n == 99
    # State whose definition did not change survives; a changed definition is re-evaluated.
    assert mod.counter == [41] and mod.registry.get('extra') == 1
    assert mod.LIMIT == 20 and mod.brand_new() == 20
    assert thing in Thing.instances
    # Only what changed ran.
    assert 'counter' not in report.names and 'greet' in report.names
    assert live.poll() == []


def test_moved_definition_gets_new_line(tmp_path):
    path = tmp_path / 'moved.py'
    write(path, 'def f():\n    return 1\n')
    mod, _ = live.load_file(str(path), 'livemod_b')
    f = mod.f
    assert f.__code__.co_firstlineno == 1
    write(path, '# a comment\n\n\ndef f():\n    return 1\n')
    (report,) = live.poll()
    assert mod.f is f and f.__code__.co_firstlineno == 4


def test_errors_are_contained(tmp_path):
    path = tmp_path / 'broken.py'
    write(path, 'x = 1\ndef f():\n    return x\n')
    mod, _ = live.load_file(str(path), 'livemod_c')
    f = mod.f
    write(path, 'x = 1\ndef f(:\n')
    (report,) = live.poll()
    assert not report.ok and isinstance(report.error, SyntaxError)
    assert mod.f is f and f() == 1                 # nothing was disturbed
    write(path, 'x = 1\ndef f():\n    return x + 1\ny = 1 / 0\nz = 3\n')
    (report,) = live.poll()
    assert isinstance(report.error, ZeroDivisionError) and report.error.lineno == 4
    assert f() == 2 and not hasattr(mod, 'z')     # what came before the error did load
    write(path, 'x = 5\ndef f():\n    return x + 1\ny = 2\nz = 3\n')
    (report,) = live.poll()
    assert report.ok and f() == 6 and mod.z == 3


def test_eval_source_returns_value(tmp_path):
    path = tmp_path / 'ev.py'
    write(path, 'def f():\n    return 1\n')
    mod, _ = live.load_file(str(path), 'livemod_d')
    f = mod.f
    report = live.eval_source(mod, 'def f():\n    return 2\nf() + 40\n', str(path), first_line=10)
    assert report.value == 42 and mod.f is f and f.__code__.co_firstlineno == 10
    report = live.eval_source(mod, '1 +', str(path))
    assert isinstance(report.error, SyntaxError)


def test_toplevel_block():
    lines = textwrap.dedent('''\
        import os

        @deco
        @other
        def f(a,
              b):
            if a:
                return 1

            return 2

        x = [
            1,
        ]
        try:
            pass
        except E:
            pass
        ''').split('\n')
    assert live.toplevel_block(lines, 7) == (2, 9)
    assert live.toplevel_block(lines, 2) == (2, 9)
    assert live.toplevel_block(lines, 12) == (11, 13)
    assert live.toplevel_block(lines, 17) == (14, 17)
    assert live.toplevel_block(lines, 0) == (0, 0)
