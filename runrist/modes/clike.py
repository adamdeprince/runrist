# This file is part of runrist.  GPL-3.0-or-later; see COPYING.
"""Languages with braces: C, C++, Objective-C, Java, C#, Go, Rust, Swift,
Kotlin, Zig, TypeScript and PHP.

Emacs highlights the C family with Lisp that cannot be carried over, and has
several of the others only as tree-sitter modes, so these are written here
from the languages themselves.  One description serves them all: a set of
keywords, of type names and of constants, the comment and string syntax, and
a pattern for the line a definition starts on.
"""

import re

from .. import fontlock
from ..editor import ed
from ..fontlock import FontLock, keyword
from ..mode import major_mode, major_modes
from ..syntax import standard
from ..variables import defvar

defvar('c_basic_offset', 4, 'Columns of indentation for each level in brace languages.',
       local=True)

_NUMBER = (r'(?<![\w.])(?:0[xX][0-9a-fA-F_\']+|0[bB][01_\']+|0[oO]?[0-7_\']+'
           r'|(?:\d[\d_\']*\.?[\d_\']*|\.\d[\d_\']*)(?:[eEpP][-+]?\d+)?)[uUlLfFiI0-9a-z]*')


def words(text):
    """A pattern matching any of the space-separated words of ``text``, whole."""
    choices = '|'.join(sorted(map(re.escape, text.split()), key=len, reverse=True))
    return rf'(?<![\w$@])(?:{choices})(?![\w$])'


def brace_block_end(buffer, line):
    """Last line to fold for the block opening on ``line``: the line before its closing brace."""
    from ..commands.prog import scan_brackets
    from .. import fold
    code = fontlock.code_line(buffer, line)
    depth = 0
    opener = None
    for column, char in enumerate(code):
        if char in '([{':
            if depth == 0:
                opener = column
            depth += 1
        elif char in ')]}':
            depth = max(0, depth - 1)
            if depth == 0:
                opener = None
    if opener is None:
        return fold.indent_block(buffer, line)
    close = scan_brackets(buffer, buffer.text.line_start(line) + opener, True)
    if close is None:
        return None
    last = buffer.text.pos_to_line(close - 1)
    # Keep the closing bracket's own line in view when it holds nothing else.
    if buffer.text.line(last).strip() in ('}', '};', '},', ')', ');', ']', '];', '})', '});'):
        last -= 1
    return last if last > line else None


def brace_indent_line():
    """Indent the current line by how many brackets are open before it."""
    from ..modes.python import open_bracket
    buffer = ed.current_buffer
    line = buffer.line_at()
    offset = int(buffer.get('c_basic_offset'))
    fontlock.fontify(buffer, max(0, line - 1), line)
    info = buffer.text.meta(line)
    current = buffer.text.line(line)
    if info is not None and info.state_in is not None:
        if info.state_in[0] == 'c':                 # inside a block comment: line up the stars
            above = buffer.current_indentation(buffer.text.line_start(max(0, line - 1)))
            first = buffer.text.line(max(0, line - 1)).lstrip()
            wanted = above + (1 if first.startswith('/*') else 0)
        else:
            return
    else:
        bracket = open_bracket(buffer, line)
        stripped = current.lstrip()
        if bracket is None:
            wanted = 0
        else:
            opener_line, column = bracket
            code = fontlock.code_line(buffer, opener_line)
            base = buffer.current_indentation(buffer.text.line_start(opener_line))
            if stripped[:1] in ')]}' and stripped[:1]:
                wanted = base
            elif code[column] != '{' and code[column + 1:].strip():
                wanted = column + 1             # arguments line up under the first one
            else:
                wanted = base + offset
                if re.match(r'(?:case\b.*|default\s*):|(?:public|private|protected)\s*:',
                            stripped):
                    wanted -= offset
        if stripped.startswith('#') and buffer.major_mode.name in ('c-mode', 'c++-mode',
                                                                    'objc-mode'):
            wanted = 0
    within = buffer.pt - buffer.bol() <= len(current) - len(current.lstrip(' \t'))
    if wanted != buffer.current_indentation():
        buffer.indent_line_to(max(0, wanted), line)
    if within:
        buffer.back_to_indentation()


def _electric_brace():
    buffer = ed.current_buffer
    if buffer.get('indent_line_function') is not brace_indent_line or ed.last_event is None:
        return
    if ed.last_event.key in ('}', ')', ']') and \
            buffer.text.line(buffer.line_at()).strip() == ed.last_event.key:
        brace_indent_line()                 # a closing bracket alone on its line finds its level
        buffer.goto_char(buffer.eol())


def define(name, title, auto, *, keywords, types='', constants='', line_comment='//',
           block_comment=('/*', '*/'), nested=False, strings=('"',), chars=("'",),
           raw=(), preprocessor=False, defun=None, extra=(), interpreters=(),
           capital_types=True, doc=None):
    """Define a major mode for a brace language."""
    syntax = standard()
    syntax.remove_string('"')
    for quote in raw:
        if isinstance(quote, tuple):
            syntax.add_string(*quote)
        else:
            syntax.add_string(quote, escape=None)
    for quote in strings:
        syntax.add_string(quote, multiline=False)
    for quote in chars:
        syntax.add_string(quote, multiline=False)
    if line_comment:
        syntax.add_comment(line_comment)
    if block_comment:
        syntax.add_comment(block_comment[0], block_comment[1], nested=nested)
    rules = list(extra)
    if preprocessor:
        rules.append(keyword(r'^[ \t]*(#[ \t]*include)[ \t]*(<[^>\n]*>)',
                             (1, 'font-lock-preprocessor-face'), (2, 'font-lock-string-face')))
        rules.append(keyword(r'^[ \t]*(#[ \t]*define)[ \t]+(\w+)',
                             (1, 'font-lock-preprocessor-face'),
                             (2, 'font-lock-variable-name-face')))
        rules.append(keyword(r'^[ \t]*#[ \t]*\w+', 'font-lock-preprocessor-face'))
    rules.append(keyword(words(keywords), 'font-lock-keyword-face'))
    if constants:
        rules.append(keyword(words(constants), 'font-lock-constant-face'))
    if types:
        rules.append(keyword(words(types), 'font-lock-type-face'))
    rules.append(keyword(_NUMBER, 'font-lock-number-face'))
    rules.append(keyword(r'\b[A-Z][A-Z0-9_]{2,}\b', 'font-lock-constant-face'))
    if capital_types:
        rules.append(keyword(r'\b[A-Z]\w*[a-z]\w*\b(?!\s*\()', 'font-lock-type-face'))
    if defun:
        rules.append(keyword(defun, (1, 'font-lock-function-name-face')))
    rules.append(keyword(r'\b([A-Za-z_]\w*)\s*(?=\()', (1, 'font-lock-function-call-face')))
    definition = FontLock(syntax, rules)

    def setup(buffer):
        buffer.set('comment_start', (line_comment or block_comment[0]) + ' ')
        buffer.set('comment_end', '' if line_comment else block_comment[1])
        buffer.set('indent_line_function', brace_indent_line)
        buffer.set('fold_block_function', brace_block_end)
        if defun:
            buffer.set('defun_start_regexp', defun)
        fontlock.enable(buffer, definition)

    setup.__name__ = name.replace('-', '_').replace('+', 'p')
    setup.__qualname__ = setup.__name__
    setup.__doc__ = doc or f'Major mode for editing {title} code.'
    major_mode(name, title, parent='prog-mode', auto=auto, interpreters=interpreters)(setup)
    return major_modes[name]


_C_KEYWORDS = '''auto break case const continue default do else enum extern for goto if inline
register restrict return sizeof static struct switch typedef union volatile while _Alignas
_Alignof _Atomic _Generic _Noreturn _Static_assert _Thread_local alignas alignof constexpr
static_assert thread_local typeof typeof_unqual nullptr'''
_C_TYPES = '''void char short int long float double signed unsigned bool _Bool _Complex size_t
ssize_t ptrdiff_t intptr_t uintptr_t int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t
uint64_t wchar_t char8_t char16_t char32_t FILE va_list off_t pid_t time_t'''
_C_DEFUN = r'^(?:[A-Za-z_][\w \t\*&:<>,\[\]]*?[ \t\*&]+)??\**([A-Za-z_]\w*)[ \t]*\([^;]*$'

define('c-mode', 'C', [r'\.[ch]$', r'\.[ly]$'], keywords=_C_KEYWORDS, types=_C_TYPES,
       constants='NULL true false EOF', preprocessor=True, defun=_C_DEFUN, capital_types=False)

define('c++-mode', 'C++', [r'\.(?:cc|cpp|cxx|c\+\+|hh|hpp|hxx|h\+\+|ipp|tpp|inl|C|H)$'],
       keywords=_C_KEYWORDS + '''
       alignas and and_eq asm bitand bitor catch class compl concept const_cast consteval
       constinit co_await co_return co_yield decltype delete dynamic_cast explicit export
       final friend import module mutable namespace new noexcept not not_eq operator or or_eq
       override private protected public reinterpret_cast requires static_cast template this
       throw try typeid typename using virtual xor xor_eq''',
       types=_C_TYPES + ' string wstring string_view vector map set unordered_map unique_ptr '
       'shared_ptr optional variant tuple pair array span',
       constants='NULL nullptr true false', preprocessor=True, defun=_C_DEFUN,
       raw=(('R"(', ')"', None, True),))

define('objc-mode', 'ObjC', [r'\.mm?$'], keywords=_C_KEYWORDS + '''
       @interface @implementation @end @property @synthesize @dynamic @protocol @class
       @selector @encode @try @catch @finally @throw @autoreleasepool @optional @required
       self super in out inout bycopy byref oneway''',
       types=_C_TYPES + ' id Class SEL IMP BOOL instancetype NSInteger NSUInteger CGFloat',
       constants='NULL nil Nil YES NO true false', preprocessor=True, defun=_C_DEFUN)

define('java-mode', 'Java', [r'\.java$', r'\.gradle$'], keywords='''abstract assert break case
       catch class continue default do else enum exports extends final finally for if
       implements import instanceof interface module native new non-sealed open opens package
       permits private protected provides public record requires return sealed static
       strictfp super switch synchronized this throw throws to transient transitive try uses
       var volatile while with yield''',
       types='boolean byte char double float int long short void String Object',
       constants='true false null',
       defun=r'^[ \t]+(?:(?:public|private|protected|static|final|abstract|synchronized|native'
             r'|default)[ \t]+)*(?:[\w<>\[\],.?]+[ \t]+)+(\w+)[ \t]*\([^;]*$')

define('csharp-mode', 'C#', [r'\.cs$'], keywords='''abstract as async await base break case
       catch checked class const continue default delegate do else enum event explicit extern
       finally fixed for foreach get goto if implicit in init interface internal is lock
       namespace new operator out override params partial private protected public readonly
       record ref required return sealed set sizeof stackalloc static struct switch this throw
       try typeof unchecked unsafe using var virtual volatile when where while yield''',
       types='bool byte char decimal double dynamic float int long nint nuint object sbyte '
             'short string uint ulong ushort void',
       constants='true false null', preprocessor=True, raw=(('@"', '"', None, True),))

define('go-mode', 'Go', [r'\.go$'], keywords='''break case chan const continue default defer
       else fallthrough for func go goto if import interface map package range return select
       struct switch type var''',
       types='any bool byte comparable complex64 complex128 error float32 float64 int int8 '
             'int16 int32 int64 rune string uint uint8 uint16 uint32 uint64 uintptr',
       constants='true false nil iota', raw=('`',), chars=("'",),
       defun=r'^func[ \t]+(?:\([^)]*\)[ \t]*)?(\w+)',
       extra=[keyword(r'^(?:type)[ \t]+(\w+)', (1, 'font-lock-type-face'))])

define('rust-mode', 'Rust', [r'\.rs$'], keywords='''as async await break const continue crate
       dyn else enum extern fn for if impl in let loop match mod move mut pub ref return self
       Self static struct super trait type union unsafe use where while macro_rules''',
       types='bool char f32 f64 i8 i16 i32 i64 i128 isize str u8 u16 u32 u64 u128 usize '
             'String Vec Option Result Box Rc Arc',
       constants='true false None Some Ok Err', nested=True, chars=(),
       raw=(('r#"', '"#', None, True), ('r"', '"', None, True)),
       defun=r'^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:(?:const|async|unsafe|extern(?:[ \t]+"[^"]*")?)'
             r'[ \t]+)*fn[ \t]+(\w+)',
       extra=[keyword(r"'(?:\\.|[^\\'])'", 'font-lock-string-face'),
              keyword(r"'[A-Za-z_]\w*\b(?!')", 'font-lock-variable-name-face'),
              keyword(r'\b(\w+)!', (1, 'font-lock-preprocessor-face')),
              keyword(r'#!?\[[^\]\n]*\]', 'font-lock-preprocessor-face')])

define('swift-mode', 'Swift', [r'\.swift$'], keywords='''actor any as associatedtype async
       await break case catch class continue convenience default defer deinit didSet do
       dynamic else enum extension fallthrough fileprivate final for func get guard if import
       in indirect infix init inout internal is lazy let mutating nonisolated nonmutating open
       operator optional override postfix precedencegroup prefix private protocol public
       repeat required rethrows return self Self set some static struct subscript super
       switch throw throws try typealias unowned var weak where while willSet''',
       types='Any AnyObject Bool Character Double Float Int Int8 Int16 Int32 Int64 String '
             'UInt UInt8 UInt16 UInt32 UInt64 Void Array Dictionary Optional Set',
       constants='true false nil', nested=True, chars=(),
       raw=(('"""', '"""', '\\', True),),
       defun=r'^[ \t]*(?:(?:public|private|internal|fileprivate|open|static|class|final'
             r'|override|mutating|@\w+)[ \t]+)*func[ \t]+(\w+)')

define('kotlin-mode', 'Kotlin', [r'\.kts?$'], keywords='''abstract actual annotation as break
       by catch class companion const constructor continue crossinline data do else enum
       expect external final finally for fun get if import in infix init inline inner
       interface internal is lateinit noinline object open operator out override package
       private protected public reified return sealed set super suspend tailrec this throw
       try typealias val value var vararg when where while''',
       types='Any Boolean Byte Char Double Float Int Long Nothing Short String Unit',
       constants='true false null', raw=(('"""', '"""', None, True),),
       defun=r'^[ \t]*(?:(?:public|private|internal|protected|override|open|suspend|inline'
             r'|operator|infix|tailrec)[ \t]+)*fun[ \t]+(?:<[^>]*>[ \t]*)?(?:[\w.]+\.)?(\w+)')

define('zig-mode', 'Zig', [r'\.zig$'], keywords='''addrspace align allowzero and anyframe
       anytype asm async await break callconv catch comptime const continue defer else enum
       errdefer error export extern fn for if inline linksection noalias noinline nosuspend
       opaque or orelse packed pub resume return struct suspend switch test threadlocal try
       union unreachable usingnamespace var volatile while''',
       types='bool void noreturn type anyerror anyopaque comptime_int comptime_float f16 f32 '
             'f64 f80 f128 i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize c_int c_uint '
             'c_long c_char',
       constants='true false null undefined', block_comment=None,
       defun=r'^[ \t]*(?:pub[ \t]+)?(?:export[ \t]+|inline[ \t]+)?fn[ \t]+(\w+)',
       extra=[keyword(r'@\w+', 'font-lock-builtin-face')])

define('typescript-mode', 'TypeScript', [r'\.[cm]?tsx?$'], keywords='''abstract any as
       asserts async await break case catch class const constructor continue debugger declare
       default delete do else enum export extends finally for from function get global if
       implements import in infer instanceof interface is keyof let module namespace new of
       out override package private protected public readonly require return satisfies set
       static super switch this throw try type typeof unique var void while with yield''',
       types='bigint boolean never number object string symbol unknown',
       constants='true false null undefined NaN Infinity',
       strings=('"', "'"), chars=(), raw=(('`', '`', '\\', True),),
       defun=r'^[ \t]*(?:export[ \t]+)?(?:default[ \t]+)?(?:async[ \t]+)?function\*?[ \t]+(\w+)')

define('php-mode', 'PHP', [r'\.php[s345]?$', r'\.phtml$'], keywords='''abstract and array as
       break callable case catch class clone const continue declare default do echo else
       elseif empty enddeclare endfor endforeach endif endswitch endwhile enum extends final
       finally fn for foreach function global goto if implements include include_once
       instanceof insteadof interface isset list match namespace new or print private
       protected public readonly require require_once return static switch throw trait try
       unset use var while xor yield''',
       types='bool float int iterable mixed never object string void',
       constants='true false null TRUE FALSE NULL', strings=('"', "'"), chars=(),
       defun=r'^[ \t]*(?:(?:public|private|protected|static|final|abstract)[ \t]+)*'
             r'function[ \t]+&?(\w+)',
       extra=[keyword(r'\$\w+', 'font-lock-variable-name-face'),
              keyword(r'<\?(?:php|=)?|\?>', 'font-lock-preprocessor-face')],
       interpreters=[r'php\d*'])

from ..variables import add_hook  # noqa: E402

add_hook('post_self_insert_hook', _electric_brace)
