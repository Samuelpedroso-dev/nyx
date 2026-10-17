#!/usr/bin/env python3
"""Nyx compiler, version 0.2.

Nyx is a statically typed systems language that compiles to C.
This single file holds the whole toolchain: lexer, parser, type checker,
C code generator and command line interface.

Usage
    nyx run   file.nyx           compile and run
    nyx build file.nyx [-o out]  compile to a native executable
    nyx emit  file.nyx           print the generated C code
    nyx check file.nyx           type check only
"""

import os
import shutil
import subprocess
import sys
import tempfile

VERSION = "0.2.0"
LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")


# ════════════════════════════════════════════════════════════════════
#  Errors
# ════════════════════════════════════════════════════════════════════

class NyxError(Exception):
    def __init__(self, msg, line=0, col=0, file=None):
        super().__init__(msg)
        self.msg, self.line, self.col, self.file = msg, line, col, file

    def render(self, path, source):
        out = f"{path}:{self.line}:{self.col}: error: {self.msg}"
        lines = source.splitlines()
        if 1 <= self.line <= len(lines):
            src = lines[self.line - 1]
            out += f"\n  {self.line:>4} | {src}\n       | {' ' * max(self.col - 1, 0)}^"
        return out


# ════════════════════════════════════════════════════════════════════
#  Lexer
# ════════════════════════════════════════════════════════════════════

KEYWORDS = {
    "fn", "let", "var", "if", "else", "while", "for", "in", "return",
    "break", "continue", "struct", "true", "false", "as", "extern",
    "impl", "import",
}

OPERATORS = sorted([
    "<<=", ">>=", "...",
    "->", "..", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "~", "&", "|", "^",
    "(", ")", "{", "}", "[", "]", ",", ":", ";", ".",
], key=len, reverse=True)


class Tok:
    __slots__ = ("kind", "value", "line", "col")

    def __init__(self, kind, value, line, col):
        self.kind, self.value, self.line, self.col = kind, value, line, col


def lex(src):
    toks, i, line, col, n = [], 0, 1, 1, len(src)

    def adv(k=1):
        nonlocal i, line, col
        for _ in range(k):
            if src[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        c = src[i]
        if c in " \t\r\n":
            adv()
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                adv()
            continue
        if src.startswith("/*", i):
            sl, sc = line, col
            adv(2)
            while i < n and not src.startswith("*/", i):
                adv()
            if i >= n:
                raise NyxError("unterminated block comment", sl, sc)
            adv(2)
            continue
        sl, sc = line, col
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            adv(j - i)
            toks.append(Tok("kw" if word in KEYWORDS else "ident", word, sl, sc))
            continue
        if c.isdigit():
            j = i
            is_float = False
            if src.startswith(("0x", "0X"), i):
                j += 2
                while j < n and src[j] in "0123456789abcdefABCDEF_":
                    j += 1
                val = int(src[i:j].replace("_", ""), 16)
            elif src.startswith(("0b", "0B"), i):
                j += 2
                while j < n and src[j] in "01_":
                    j += 1
                val = int(src[i:j].replace("_", ""), 2)
            else:
                while j < n and (src[j].isdigit() or src[j] == "_"):
                    j += 1
                if j < n and src[j] == "." and j + 1 < n and src[j + 1].isdigit():
                    is_float = True
                    j += 1
                    while j < n and (src[j].isdigit() or src[j] == "_"):
                        j += 1
                if j < n and src[j] in "eE":
                    k = j + 1
                    if k < n and src[k] in "+-":
                        k += 1
                    if k < n and src[k].isdigit():
                        is_float = True
                        j = k
                        while j < n and src[j].isdigit():
                            j += 1
                text = src[i:j].replace("_", "")
                val = float(text) if is_float else int(text)
            adv(j - i)
            toks.append(Tok("float" if is_float else "int", val, sl, sc))
            continue
        if c == '"':
            adv()
            buf = []
            while True:
                if i >= n or src[i] == "\n":
                    raise NyxError("unterminated string literal", sl, sc)
                ch = src[i]
                if ch == '"':
                    adv()
                    break
                if ch == "\\":
                    if i + 1 >= n:
                        raise NyxError("bad escape", line, col)
                    e = src[i + 1]
                    m = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"'}
                    if e not in m:
                        raise NyxError(f"unknown escape \\{e}", line, col)
                    buf.append(m[e])
                    adv(2)
                    continue
                buf.append(ch)
                adv()
            toks.append(Tok("str", "".join(buf), sl, sc))
            continue
        for op in OPERATORS:
            if src.startswith(op, i):
                adv(len(op))
                toks.append(Tok("op", op, sl, sc))
                break
        else:
            raise NyxError(f"unexpected character {c!r}", sl, sc)
    toks.append(Tok("eof", None, line, col))
    return toks


# ════════════════════════════════════════════════════════════════════
#  AST
# ════════════════════════════════════════════════════════════════════

class Node:
    def __init__(self, kind, line, col, **kw):
        self.kind, self.line, self.col = kind, line, col
        self.ty = None
        self.__dict__.update(kw)


# ════════════════════════════════════════════════════════════════════
#  Parser
# ════════════════════════════════════════════════════════════════════

BINARY_PREC = [
    ["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="],
    ["<", "<=", ">", ">="], ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
]
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}


class Parser:
    def __init__(self, toks):
        self.t, self.p = toks, 0

    def peek(self, k=0):
        return self.t[min(self.p + k, len(self.t) - 1)]

    def at(self, kind, value=None):
        tk = self.peek()
        return tk.kind == kind and (value is None or tk.value == value)

    def at_op(self, value):
        return self.at("op", value)

    def eat(self, kind, value=None):
        tk = self.peek()
        if tk.kind != kind or (value is not None and tk.value != value):
            want = value if value is not None else kind
            got = tk.value if tk.value is not None else tk.kind
            raise NyxError(f"expected '{want}' but found '{got}'", tk.line, tk.col)
        self.p += 1
        return tk

    def accept(self, kind, value=None):
        if self.at(kind, value):
            return self.eat(kind, value)
        return None

    def same_line(self):
        return self.peek().line == self.t[self.p - 1].line

    # top level
    def program(self):
        items = []
        while not self.at("eof"):
            tk = self.peek()
            if self.at("kw", "fn"):
                items.append(self.func())
            elif self.at("kw", "struct"):
                items.append(self.struct())
            elif self.at("kw", "extern"):
                items.append(self.extern())
            elif self.at("kw", "impl"):
                items.append(self.impl())
            elif self.accept("kw", "import"):
                path = self.eat("str")
                items.append(Node("import", tk.line, tk.col, path=path.value))
            else:
                raise NyxError("expected 'fn', 'struct', 'impl', 'import' or 'extern' at top level", tk.line, tk.col)
        return items

    def type_expr(self):
        tk = self.peek()
        if self.accept("op", "["):
            elem = self.type_expr()
            if self.accept("op", ";"):
                size = self.eat("int")
                self.eat("op", "]")
                if size.value <= 0:
                    raise NyxError("array size must be positive", size.line, size.col)
                return Node("tarray", tk.line, tk.col, elem=elem, size=size.value)
            self.eat("op", "]")
            return Node("tlist", tk.line, tk.col, elem=elem)
        name = self.eat("ident")
        return Node("tname", tk.line, tk.col, name=name.value)

    def params(self, allow_self=False):
        self.eat("op", "(")
        ps, variadic, self_mode = [], False, None
        if allow_self:
            if self.at("kw", "var") and self.peek(1).kind == "ident" and self.peek(1).value == "self":
                self.p += 2
                self_mode = "var"
            elif self.at("ident", "self"):
                self.p += 1
                self_mode = "value"
            if self_mode and not self.at_op(")"):
                self.eat("op", ",")
        while not self.at_op(")"):
            if self.accept("op", "..."):
                variadic = True
                break
            name = self.eat("ident")
            if name.value == "self":
                raise NyxError("'self' must be the first parameter of a method", name.line, name.col)
            self.eat("op", ":")
            ps.append((name.value, self.type_expr(), name.line, name.col))
            if not self.accept("op", ","):
                break
        self.eat("op", ")")
        ret = None
        if self.accept("op", "->"):
            ret = self.type_expr()
        return ps, ret, variadic, self_mode

    def func(self, allow_self=False):
        kw = self.eat("kw", "fn")
        name = self.eat("ident")
        ps, ret, variadic, self_mode = self.params(allow_self)
        if variadic:
            raise NyxError("only extern functions can be variadic", kw.line, kw.col)
        body = self.block()
        return Node("fn", kw.line, kw.col, name=name.value, params=ps, ret=ret, body=body, self_mode=self_mode)

    def extern(self):
        kw = self.eat("kw", "extern")
        self.eat("kw", "fn")
        name = self.eat("ident")
        ps, ret, variadic, _ = self.params()
        return Node("extern", kw.line, kw.col, name=name.value, params=ps, ret=ret, variadic=variadic)

    def struct(self):
        kw = self.eat("kw", "struct")
        name = self.eat("ident")
        self.eat("op", "{")
        fields = []
        while not self.at_op("}"):
            fname = self.eat("ident")
            self.eat("op", ":")
            fields.append((fname.value, self.type_expr(), fname.line, fname.col))
            if not (self.accept("op", ",") or self.accept("op", ";")):
                break
        self.eat("op", "}")
        return Node("struct", kw.line, kw.col, name=name.value, fields=fields)

    def impl(self):
        kw = self.eat("kw", "impl")
        name = self.eat("ident")
        self.eat("op", "{")
        methods = []
        while not self.at_op("}"):
            if not self.at("kw", "fn"):
                tk = self.peek()
                raise NyxError("only functions can go inside an impl block", tk.line, tk.col)
            methods.append(self.func(allow_self=True))
        self.eat("op", "}")
        return Node("impl", kw.line, kw.col, name=name.value, methods=methods)

    # statements
    def block(self):
        lb = self.eat("op", "{")
        stmts = []
        while not self.at_op("}"):
            if self.at("eof"):
                raise NyxError("unclosed block, missing '}'", lb.line, lb.col)
            if self.accept("op", ";"):
                continue
            stmts.append(self.stmt())
        self.eat("op", "}")
        return Node("block", lb.line, lb.col, stmts=stmts)

    def stmt(self):
        tk = self.peek()
        if tk.kind == "kw":
            v = tk.value
            if v in ("let", "var"):
                self.p += 1
                name = self.eat("ident")
                ty = self.type_expr() if self.accept("op", ":") else None
                self.eat("op", "=")
                init = self.expr()
                return Node("let", tk.line, tk.col, name=name.value, decl_ty=ty, init=init, mutable=(v == "var"))
            if v == "if":
                return self.if_stmt()
            if v == "while":
                self.p += 1
                cond = self.expr()
                return Node("while", tk.line, tk.col, cond=cond, body=self.block())
            if v == "for":
                self.p += 1
                var = self.eat("ident")
                self.eat("kw", "in")
                start = self.expr()
                if self.accept("op", ".."):
                    end = self.expr()
                    return Node("for", tk.line, tk.col, var=var.value, start=start, end=end, body=self.block())
                return Node("foreach", tk.line, tk.col, var=var.value, iterable=start, body=self.block())
            if v == "return":
                self.p += 1
                val = None
                if self.same_line() and not self.at_op("}") and not self.at_op(";"):
                    val = self.expr()
                return Node("return", tk.line, tk.col, value=val)
            if v == "break":
                self.p += 1
                return Node("break", tk.line, tk.col)
            if v == "continue":
                self.p += 1
                return Node("continue", tk.line, tk.col)
        e = self.expr()
        if self.peek().kind == "op" and self.peek().value in ASSIGN_OPS:
            op = self.eat("op").value
            val = self.expr()
            return Node("assign", tk.line, tk.col, target=e, op=op, value=val)
        return Node("exprstmt", tk.line, tk.col, expr=e)

    def if_stmt(self):
        kw = self.eat("kw", "if")
        cond = self.expr()
        then = self.block()
        els = None
        if self.accept("kw", "else"):
            els = self.if_stmt() if self.at("kw", "if") else self.block()
        return Node("if", kw.line, kw.col, cond=cond, then=then, els=els)

    # expressions
    def expr(self, level=0):
        if level == len(BINARY_PREC):
            return self.unary()
        left = self.expr(level + 1)
        while self.peek().kind == "op" and self.peek().value in BINARY_PREC[level]:
            op = self.eat("op")
            right = self.expr(level + 1)
            left = Node("binary", op.line, op.col, op=op.value, left=left, right=right)
        return left

    def unary(self):
        tk = self.peek()
        if tk.kind == "op" and tk.value in ("-", "!", "~"):
            self.p += 1
            return Node("unary", tk.line, tk.col, op=tk.value, operand=self.unary())
        return self.cast()

    def cast(self):
        e = self.postfix()
        while self.at("kw", "as"):
            tk = self.eat("kw", "as")
            e = Node("cast", tk.line, tk.col, expr=e, target=self.type_expr())
        return e

    def postfix(self):
        e = self.primary()
        while True:
            if self.at_op("(") and self.same_line():
                tk = self.eat("op", "(")
                args = []
                while not self.at_op(")"):
                    args.append(self.expr())
                    if not self.accept("op", ","):
                        break
                self.eat("op", ")")
                e = Node("call", tk.line, tk.col, callee=e, args=args)
            elif self.at_op("[") and self.same_line():
                tk = self.eat("op", "[")
                idx = self.expr()
                self.eat("op", "]")
                e = Node("index", tk.line, tk.col, base=e, index=idx)
            elif self.at_op("."):
                tk = self.eat("op", ".")
                name = self.eat("ident")
                e = Node("field", tk.line, tk.col, base=e, name=name.value)
            else:
                return e

    def primary(self):
        tk = self.peek()
        if tk.kind == "int":
            self.p += 1
            return Node("int", tk.line, tk.col, value=tk.value)
        if tk.kind == "float":
            self.p += 1
            return Node("float", tk.line, tk.col, value=tk.value)
        if tk.kind == "str":
            self.p += 1
            return Node("str", tk.line, tk.col, value=tk.value)
        if tk.kind == "kw" and tk.value in ("true", "false"):
            self.p += 1
            return Node("bool", tk.line, tk.col, value=(tk.value == "true"))
        if tk.kind == "ident":
            self.p += 1
            if self.at_op("{") and tk.value[:1].isupper():
                self.eat("op", "{")
                inits = []
                while not self.at_op("}"):
                    fname = self.eat("ident")
                    self.eat("op", ":")
                    inits.append((fname.value, self.expr(), fname.line, fname.col))
                    if not self.accept("op", ","):
                        break
                self.eat("op", "}")
                return Node("structlit", tk.line, tk.col, name=tk.value, inits=inits)
            return Node("name", tk.line, tk.col, name=tk.value)
        if self.accept("op", "("):
            e = self.expr()
            self.eat("op", ")")
            return e
        if self.accept("op", "["):
            items = []
            if not self.at_op("]"):
                first = self.expr()
                if self.accept("op", ";"):
                    size = self.eat("int")
                    self.eat("op", "]")
                    if size.value <= 0:
                        raise NyxError("array size must be positive", size.line, size.col)
                    return Node("arrayrep", tk.line, tk.col, item=first, size=size.value)
                items.append(first)
                self.accept("op", ",")
            while not self.at_op("]"):
                items.append(self.expr())
                if not self.accept("op", ","):
                    break
            self.eat("op", "]")
            return Node("arraylit", tk.line, tk.col, items=items)
        got = tk.value if tk.value is not None else tk.kind
        raise NyxError(f"unexpected '{got}' in expression", tk.line, tk.col)


# ════════════════════════════════════════════════════════════════════
#  Types
# ════════════════════════════════════════════════════════════════════

class Type:
    def __init__(self, kind, name=None, elem=None, size=None):
        self.kind, self.name, self.elem, self.size = kind, name, elem, size

    def __eq__(self, other):
        return (isinstance(other, Type) and self.kind == other.kind and self.name == other.name
                and self.elem == other.elem and self.size == other.size)

    def __hash__(self):
        return hash((self.kind, self.name, self.elem, self.size))

    def __str__(self):
        if self.kind == "array":
            return f"[{self.elem}; {self.size}]"
        if self.kind == "list":
            return f"[{self.elem}]"
        return self.name


INT_TYPES = {
    "i8": ("int8_t", True, 8), "i16": ("int16_t", True, 16),
    "i32": ("int32_t", True, 32), "i64": ("int64_t", True, 64),
    "u8": ("uint8_t", False, 8), "u16": ("uint16_t", False, 16),
    "u32": ("uint32_t", False, 32), "u64": ("uint64_t", False, 64),
}
FLOAT_TYPES = {"f32": "float", "f64": "double"}
PRIMS = {n: Type("prim", n) for n in [*INT_TYPES, *FLOAT_TYPES, "bool", "str", "void"]}
I64, F64, BOOL, STR, VOID, U8 = PRIMS["i64"], PRIMS["f64"], PRIMS["bool"], PRIMS["str"], PRIMS["void"], PRIMS["u8"]


def is_int(t):
    return t is not None and t.kind == "prim" and t.name in INT_TYPES


def is_float(t):
    return t is not None and t.kind == "prim" and t.name in FLOAT_TYPES


def is_num(t):
    return is_int(t) or is_float(t)


def int_fits(value, t):
    _, signed, bits = INT_TYPES[t.name]
    if signed:
        return -(1 << (bits - 1)) <= value < (1 << (bits - 1))
    return 0 <= value < (1 << bits)


BUILTINS = {"print", "len", "push", "pop", "remove", "clear", "str", "substr", "contains", "find",
            "chr", "read_line", "parse_int", "parse_float", "time_ms", "exit"}
LIST_METHODS = {"push", "pop", "remove", "clear", "len"}
STR_METHODS = {"len", "substr", "contains", "find"}

MATH_FUNCS = {
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "pow", "exp", "log", "log10",
    "fabs", "floor", "ceil", "round", "fmod", "fmin", "fmax", "hypot",
    "sqrtf", "sinf", "cosf", "tanf", "powf", "fabsf", "floorf", "ceilf",
}
STDLIB_C = {"abs", "rand", "srand", "putchar", "getchar", "printf", "puts"}


# ════════════════════════════════════════════════════════════════════
#  Type checker
# ════════════════════════════════════════════════════════════════════

class Sym:
    def __init__(self, ty, mutable, cname):
        self.ty, self.mutable, self.cname = ty, mutable, cname


class Method:
    def __init__(self, struct, node, pts, ret, cname):
        self.struct, self.node, self.pts, self.ret, self.cname = struct, node, pts, ret, cname
        self.self_mode = node.self_mode


class Checker:
    def __init__(self, items):
        self.items = items
        self.structs = {}
        self.struct_types = {}
        self.funcs = {}
        self.methods = {}
        self.scopes = []
        self.cur_ret = None
        self.loop_depth = 0
        self.uses_math = False

    def push(self):
        self.scopes.append({})

    def pop(self):
        self.scopes.pop()

    def declare(self, name, sym, node):
        if name in self.scopes[-1]:
            raise NyxError(f"'{name}' is already declared in this scope", node.line, node.col)
        self.scopes[-1][name] = sym

    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def resolve(self, tn):
        if tn is None:
            return VOID
        if tn.kind in ("tarray", "tlist"):
            elem = self.resolve(tn.elem)
            if elem == VOID:
                raise NyxError("collection of void is not allowed", tn.line, tn.col)
            if tn.kind == "tlist":
                return Type("list", elem=elem)
            return Type("array", elem=elem, size=tn.size)
        if tn.name in PRIMS:
            return PRIMS[tn.name]
        if tn.name in self.struct_types:
            return self.struct_types[tn.name]
        raise NyxError(f"unknown type '{tn.name}'", tn.line, tn.col)

    def per_item(self, item, fn):
        try:
            fn()
        except NyxError as ex:
            if ex.file is None:
                ex.file = getattr(item, "file", None)
            raise

    def run(self):
        for it in self.items:
            if it.kind == "struct":
                def reg(it=it):
                    if it.name in self.structs or it.name in PRIMS:
                        raise NyxError(f"type '{it.name}' is already defined", it.line, it.col)
                    self.structs[it.name] = it
                    self.struct_types[it.name] = Type("struct", name=it.name)
                    self.methods[it.name] = {}
                self.per_item(it, reg)
        for it in self.items:
            if it.kind == "struct":
                def fields(it=it):
                    seen = set()
                    it.field_types = []
                    for fname, ftn, fl, fc in it.fields:
                        if fname in seen:
                            raise NyxError(f"duplicate field '{fname}'", fl, fc)
                        seen.add(fname)
                        ft = self.resolve(ftn)
                        if ft == VOID:
                            raise NyxError("field cannot be void", fl, fc)
                        it.field_types.append((fname, ft))
                self.per_item(it, fields)
        self.check_recursive_structs()
        for it in self.items:
            if it.kind in ("fn", "extern"):
                self.per_item(it, lambda it=it: self.register_fn(it))
            elif it.kind == "impl":
                self.per_item(it, lambda it=it: self.register_impl(it))
        if "main" not in self.funcs or self.funcs["main"][4].kind != "fn":
            raise NyxError("program has no 'fn main()'", 1, 1)
        mpts, mret, _, _, mnode = self.funcs["main"]
        if mpts or mret not in (VOID, PRIMS["i32"], I64):
            raise NyxError("main must take no parameters and return nothing, i32 or i64", mnode.line, mnode.col,
                           getattr(mnode, "file", None))
        for it in self.items:
            if it.kind == "fn":
                self.per_item(it, lambda it=it: self.check_fn(it, self.funcs[it.name][0], self.funcs[it.name][1]))
            elif it.kind == "impl":
                for m in it.methods:
                    meth = self.methods[it.name][m.name]
                    self.per_item(it, lambda m=m, meth=meth: self.check_fn(m, meth.pts, meth.ret, meth))

    def register_fn(self, it):
        if it.name in self.funcs or it.name in BUILTINS:
            raise NyxError(f"function '{it.name}' is already defined", it.line, it.col)
        pts = []
        for pname, ptn, pl, pc in it.params:
            pt = self.resolve(ptn)
            if pt == VOID:
                raise NyxError("parameter cannot be void", pl, pc)
            pts.append(pt)
        ret = self.resolve(it.ret)
        if it.kind == "extern":
            for t in pts + [ret]:
                if t.kind != "prim":
                    raise NyxError("extern functions only take and return primitive types", it.line, it.col)
            cname = it.name
            if it.name in MATH_FUNCS:
                self.uses_math = True
            variadic = it.variadic
        else:
            cname = "main_nx" if it.name == "main" else "nx_" + it.name
            variadic = False
        self.funcs[it.name] = (pts, ret, variadic, cname, it)

    def register_impl(self, it):
        if it.name not in self.structs:
            raise NyxError(f"impl for unknown struct '{it.name}'", it.line, it.col)
        table = self.methods[it.name]
        fnames = {f for f, _ in self.structs[it.name].field_types}
        for m in it.methods:
            if m.name in table:
                raise NyxError(f"method '{m.name}' is already defined for {it.name}", m.line, m.col)
            if m.name in fnames and m.self_mode:
                raise NyxError(f"method '{m.name}' has the same name as a field of {it.name}", m.line, m.col)
            pts = []
            for pname, ptn, pl, pc in m.params:
                pt = self.resolve(ptn)
                if pt == VOID:
                    raise NyxError("parameter cannot be void", pl, pc)
                pts.append(pt)
            ret = self.resolve(m.ret)
            table[m.name] = Method(it.name, m, pts, ret, f"nx_{it.name}__{m.name}")

    def check_recursive_structs(self):
        state = {}

        def visit(name, node):
            if state.get(name) == 1:
                raise NyxError(f"struct '{name}' contains itself by value", node.line, node.col, getattr(node, "file", None))
            if state.get(name) == 2:
                return
            state[name] = 1
            for _, ft in self.structs[name].field_types:
                t = ft
                while t.kind == "array":
                    t = t.elem
                if t.kind == "struct":
                    visit(t.name, self.structs[name])
            state[name] = 2

        for n, node in self.structs.items():
            visit(n, node)

    def check_fn(self, fn, pts, ret, method=None):
        self.cur_ret = ret
        self.push()
        if method is not None and method.self_mode:
            st = self.struct_types[method.struct]
            cname = "(*nx_self)" if method.self_mode == "var" else "nx_self"
            self.scopes[-1]["self"] = Sym(st, method.self_mode == "var", cname)
        for (pname, _, pl, pc), pt in zip(fn.params, pts):
            self.declare(pname, Sym(pt, False, "nx_" + pname), Node("p", pl, pc))
        self.check_block(fn.body, new_scope=False)
        self.pop()
        if ret != VOID and not self.always_returns(fn.body):
            raise NyxError(f"function '{fn.name}' does not return a value on every path", fn.line, fn.col)

    def always_returns(self, node):
        if node.kind == "return":
            return True
        if node.kind == "block":
            return any(self.always_returns(s) for s in node.stmts)
        if node.kind == "if":
            return node.els is not None and self.always_returns(node.then) and self.always_returns(node.els)
        if node.kind == "while":
            return node.cond.kind == "bool" and node.cond.value and not self.has_break(node.body)
        if node.kind == "exprstmt" and node.expr.kind == "call" and node.expr.callee.kind == "name" \
                and node.expr.callee.name == "exit":
            return True
        return False

    def has_break(self, node):
        if node.kind == "break":
            return True
        if node.kind in ("while", "for", "foreach"):
            return False
        for child in (getattr(node, "stmts", None) or []):
            if self.has_break(child):
                return True
        if node.kind == "if":
            return self.has_break(node.then) or (node.els is not None and self.has_break(node.els))
        return False

    def check_block(self, blk, new_scope=True):
        if new_scope:
            self.push()
        for s in blk.stmts:
            self.check_stmt(s)
        if new_scope:
            self.pop()

    def check_stmt(self, s):
        k = s.kind
        if k == "let":
            want = self.resolve(s.decl_ty) if s.decl_ty is not None else None
            t = self.check_expr(s.init, want)
            if want is not None:
                self.expect(t, want, s.init)
                t = want
            if t == VOID:
                raise NyxError("cannot store a void value", s.init.line, s.init.col)
            s.ty = t
            s.cname = "nx_" + s.name
            self.declare(s.name, Sym(t, s.mutable, s.cname), s)
        elif k == "assign":
            tt = self.check_place(s.target)
            vt = self.check_expr(s.value, tt)
            if s.op == "=":
                self.expect(vt, tt, s.value)
            elif s.op == "+=" and tt == STR:
                self.expect(vt, STR, s.value)
            else:
                op = s.op[:-1]
                if op in ("&", "|", "^", "<<", ">>", "%"):
                    if not is_int(tt):
                        raise NyxError(f"'{s.op}' needs an integer target, got {tt}", s.line, s.col)
                elif not is_num(tt):
                    raise NyxError(f"'{s.op}' needs a numeric target, got {tt}", s.line, s.col)
                if op in ("<<", ">>"):
                    if not is_int(vt):
                        raise NyxError("shift amount must be an integer", s.value.line, s.value.col)
                else:
                    self.expect(vt, tt, s.value)
        elif k == "exprstmt":
            self.check_expr(s.expr)
        elif k == "if":
            self.expect(self.check_expr(s.cond, BOOL), BOOL, s.cond)
            self.check_block(s.then)
            if s.els is not None:
                if s.els.kind == "if":
                    self.check_stmt(s.els)
                else:
                    self.check_block(s.els)
        elif k == "while":
            self.expect(self.check_expr(s.cond, BOOL), BOOL, s.cond)
            self.loop_depth += 1
            self.check_block(s.body)
            self.loop_depth -= 1
        elif k == "for":
            st = self.check_expr(s.start)
            et = self.check_expr(s.end, st)
            if not is_int(st):
                raise NyxError("for range bounds must be integers", s.start.line, s.start.col)
            if s.start.kind == "int" and is_int(et) and et != st:
                st = self.check_expr(s.start, et)
            self.expect(et, st, s.end)
            s.ty = st
            s.cname = "nx_" + s.var
            self.loop_body(s, st)
        elif k == "foreach":
            it = self.check_expr(s.iterable)
            if it.kind in ("list", "array"):
                et = it.elem
            elif it == STR:
                et = U8
            else:
                raise NyxError(f"cannot loop over a value of type {it}", s.iterable.line, s.iterable.col)
            s.ty = et
            s.cname = "nx_" + s.var
            self.loop_body(s, et)
        elif k == "return":
            if s.value is None:
                if self.cur_ret != VOID:
                    raise NyxError(f"missing return value of type {self.cur_ret}", s.line, s.col)
            else:
                if self.cur_ret == VOID:
                    raise NyxError("this function does not return a value", s.value.line, s.value.col)
                self.expect(self.check_expr(s.value, self.cur_ret), self.cur_ret, s.value)
        elif k in ("break", "continue"):
            if self.loop_depth == 0:
                raise NyxError(f"'{k}' outside of a loop", s.line, s.col)
        else:
            raise NyxError(f"internal: unknown statement {k}", s.line, s.col)

    def loop_body(self, s, var_ty):
        self.push()
        self.declare(s.var, Sym(var_ty, False, s.cname), s)
        self.loop_depth += 1
        self.check_block(s.body, new_scope=False)
        self.loop_depth -= 1
        self.pop()

    def check_place(self, e):
        if e.kind == "name":
            sym = self.lookup(e.name)
            if sym is None:
                raise NyxError(f"unknown name '{e.name}'", e.line, e.col)
            if not sym.mutable:
                hint = "use 'var self' in the method signature" if e.name == "self" else \
                    "declare it with 'var' to make it mutable"
                raise NyxError(f"cannot modify '{e.name}', {hint}", e.line, e.col)
            e.ty, e.cname = sym.ty, sym.cname
            return sym.ty
        if e.kind == "field":
            self.check_place(e.base)
            return self.check_field(e)
        if e.kind == "index":
            bt = self.check_expr(e.base)
            if bt.kind == "list":
                return self.check_index(e)
            if bt == STR:
                raise NyxError("strings are immutable, build a new one instead", e.line, e.col)
            self.check_place(e.base)
            return self.check_index(e)
        raise NyxError("this expression cannot be assigned to", e.line, e.col)

    def expect(self, got, want, node):
        if got != want:
            raise NyxError(f"type mismatch, expected {want} but got {got}", node.line, node.col)

    def check_field(self, e):
        bt = e.base.ty
        if bt.kind != "struct":
            raise NyxError(f"type {bt} has no fields", e.line, e.col)
        for fname, ft in self.structs[bt.name].field_types:
            if fname == e.name:
                e.ty = ft
                return ft
        if e.name in self.methods.get(bt.name, {}):
            raise NyxError(f"'{e.name}' is a method, call it with parentheses", e.line, e.col)
        raise NyxError(f"struct {bt.name} has no field '{e.name}'", e.line, e.col)

    def check_index(self, e):
        bt = e.base.ty
        it = self.check_expr(e.index, I64)
        if not is_int(it):
            raise NyxError("index must be an integer", e.index.line, e.index.col)
        if bt.kind in ("array", "list"):
            e.ty = bt.elem
            return bt.elem
        if bt == STR:
            e.ty = U8
            return U8
        raise NyxError(f"type {bt} cannot be indexed", e.line, e.col)

    def check_expr(self, e, want=None):
        t = self._check_expr(e, want)
        e.ty = t
        return t

    def _check_expr(self, e, want):
        k = e.kind
        if k == "int":
            t = want if is_num(want) else I64
            if is_int(t) and not int_fits(e.value, t):
                raise NyxError(f"literal {e.value} does not fit in {t}", e.line, e.col)
            return t
        if k == "float":
            return want if is_float(want) else F64
        if k == "bool":
            return BOOL
        if k == "str":
            return STR
        if k == "name":
            sym = self.lookup(e.name)
            if sym is None:
                if e.name in self.funcs:
                    raise NyxError(f"'{e.name}' is a function, call it with parentheses", e.line, e.col)
                raise NyxError(f"unknown name '{e.name}'", e.line, e.col)
            e.cname = sym.cname
            return sym.ty
        if k == "unary":
            if e.op == "!":
                self.expect(self.check_expr(e.operand, BOOL), BOOL, e.operand)
                return BOOL
            t = self.check_expr(e.operand, want)
            if e.op == "-" and not is_num(t):
                raise NyxError(f"cannot negate {t}", e.line, e.col)
            if e.op == "~" and not is_int(t):
                raise NyxError(f"'~' needs an integer, got {t}", e.line, e.col)
            return t
        if k == "binary":
            return self.check_binary(e, want)
        if k == "cast":
            src = self.check_expr(e.expr)
            dst = self.resolve(e.target)
            ok = (is_num(src) and is_num(dst)) or (is_int(src) and dst == BOOL) or \
                 (src == BOOL and is_int(dst)) or src == dst
            if not ok:
                hint = ". Use str(x) to build a string" if dst == STR else ""
                raise NyxError(f"cannot cast {src} to {dst}{hint}", e.line, e.col)
            return dst
        if k == "field":
            self.check_expr(e.base)
            return self.check_field(e)
        if k == "index":
            self.check_expr(e.base)
            return self.check_index(e)
        if k == "call":
            return self.check_call(e)
        if k == "structlit":
            if e.name not in self.structs:
                raise NyxError(f"unknown struct '{e.name}'", e.line, e.col)
            ftypes = dict(self.structs[e.name].field_types)
            given = set()
            for fname, val, fl, fc in e.inits:
                if fname not in ftypes:
                    raise NyxError(f"struct {e.name} has no field '{fname}'", fl, fc)
                if fname in given:
                    raise NyxError(f"field '{fname}' given twice", fl, fc)
                given.add(fname)
                self.expect(self.check_expr(val, ftypes[fname]), ftypes[fname], val)
            missing = [f for f in ftypes if f not in given]
            if missing:
                raise NyxError(f"missing field(s) {', '.join(missing)} in {e.name} literal", e.line, e.col)
            return self.struct_types[e.name]
        if k == "arrayrep":
            elem_want = want.elem if (want is not None and want.kind in ("array", "list")) else None
            et = self.check_expr(e.item, elem_want)
            if et == VOID:
                raise NyxError("array of void is not allowed", e.line, e.col)
            return Type("array", elem=et, size=e.size)
        if k == "arraylit":
            is_list = want is not None and want.kind == "list"
            elem_want = want.elem if (want is not None and want.kind in ("array", "list")) else None
            if not e.items:
                if is_list:
                    e.as_list = True
                    return want
                raise NyxError("empty list needs a type, for example  var xs: [i64] = []", e.line, e.col)
            et = self.check_expr(e.items[0], elem_want)
            for it in e.items[1:]:
                self.expect(self.check_expr(it, et), et, it)
            if et == VOID:
                raise NyxError("collection of void is not allowed", e.line, e.col)
            e.as_list = is_list
            if is_list:
                return Type("list", elem=et)
            return Type("array", elem=et, size=len(e.items))
        raise NyxError(f"internal: unknown expression {k}", e.line, e.col)

    def check_binary(self, e, want):
        op = e.op
        if op in ("&&", "||"):
            self.expect(self.check_expr(e.left, BOOL), BOOL, e.left)
            self.expect(self.check_expr(e.right, BOOL), BOOL, e.right)
            return BOOL
        if op in ("<<", ">>"):
            lt = self.check_expr(e.left, want if is_int(want) else None)
            rt = self.check_expr(e.right, I64)
            if not is_int(lt) or not is_int(rt):
                raise NyxError(f"'{op}' needs integer operands", e.line, e.col)
            return lt
        arith_want = want if (op in ("+", "-", "*", "/", "%", "&", "|", "^") and is_num(want)) else None
        lt = self.check_expr(e.left, arith_want)
        rt = self.check_expr(e.right, lt)
        if lt != rt and e.left.kind in ("int", "float") and is_num(rt):
            lt = self.check_expr(e.left, rt)
        if lt != rt:
            if STR in (lt, rt) and op == "+":
                raise NyxError(f"cannot add {lt} and {rt}. Convert with str(x) first", e.line, e.col)
            raise NyxError(f"operands of '{op}' have different types, {lt} and {rt}. Use 'as' to convert", e.line, e.col)
        if lt == STR:
            if op in ("==", "!=", "<", "<=", ">", ">="):
                return BOOL
            if op == "+":
                return STR
            raise NyxError(f"'{op}' cannot be used on strings", e.line, e.col)
        if op in ("==", "!="):
            if lt.kind != "prim" or lt == VOID:
                raise NyxError(f"cannot compare values of type {lt} with '{op}'", e.line, e.col)
            return BOOL
        if op in ("<", "<=", ">", ">="):
            if not is_num(lt):
                raise NyxError(f"cannot order values of type {lt}", e.line, e.col)
            return BOOL
        if op in ("&", "|", "^", "%"):
            if not is_int(lt):
                raise NyxError(f"'{op}' needs integer operands, got {lt}", e.line, e.col)
            return lt
        if not is_num(lt):
            raise NyxError(f"'{op}' needs numeric operands, got {lt}", e.line, e.col)
        return lt

    def nargs(self, e, name, n):
        if len(e.args) != n:
            raise NyxError(f"{name} takes {n} argument(s) but got {len(e.args)}", e.line, e.col)

    def check_builtin(self, e, name, args):
        e.builtin = name
        e.bargs = args
        if name == "print":
            for a in args:
                if self.check_expr(a) == VOID:
                    raise NyxError("cannot print a void value", a.line, a.col)
            return VOID
        if name == "str":
            if len(args) != 1:
                raise NyxError("str takes exactly one argument", e.line, e.col)
            if self.check_expr(args[0]) == VOID:
                raise NyxError("cannot convert void to str", e.line, e.col)
            return STR
        if name == "len":
            if len(args) != 1:
                raise NyxError("len takes exactly one argument", e.line, e.col)
            t = self.check_expr(args[0])
            if t.kind not in ("array", "list") and t != STR:
                raise NyxError(f"len needs an array, list or str, got {t}", e.line, e.col)
            return I64
        if name in ("push", "pop", "remove", "clear"):
            want = {"push": 2, "pop": 1, "remove": 2, "clear": 1}[name]
            if len(args) != want:
                raise NyxError(f"{name} takes {want} argument(s) but got {len(args)}", e.line, e.col)
            lt = self.check_expr(args[0])
            if lt.kind != "list":
                raise NyxError(f"{name} needs a list, got {lt}", args[0].line, args[0].col)
            if name == "push":
                self.expect(self.check_expr(args[1], lt.elem), lt.elem, args[1])
                return VOID
            if name == "remove":
                if not is_int(self.check_expr(args[1], I64)):
                    raise NyxError("index must be an integer", args[1].line, args[1].col)
                return lt.elem
            if name == "clear":
                return VOID
            return lt.elem
        if name == "substr":
            if len(args) != 3:
                raise NyxError("substr takes a string, a start and an end", e.line, e.col)
            self.expect(self.check_expr(args[0]), STR, args[0])
            for a in args[1:]:
                if not is_int(self.check_expr(a, I64)):
                    raise NyxError("substr bounds must be integers", a.line, a.col)
            return STR
        if name in ("contains", "find"):
            if len(args) != 2:
                raise NyxError(f"{name} takes 2 arguments", e.line, e.col)
            self.expect(self.check_expr(args[0]), STR, args[0])
            self.expect(self.check_expr(args[1]), STR, args[1])
            return BOOL if name == "contains" else I64
        if name == "chr":
            self.nargs(e, name, 1)
            if not is_int(self.check_expr(args[0], U8)):
                raise NyxError("chr needs an integer character code", e.line, e.col)
            return STR
        if name == "read_line":
            self.nargs(e, name, 0)
            return STR
        if name in ("parse_int", "parse_float"):
            self.nargs(e, name, 1)
            self.expect(self.check_expr(args[0]), STR, args[0])
            return I64 if name == "parse_int" else F64
        if name == "time_ms":
            self.nargs(e, name, 0)
            return I64
        if name == "exit":
            self.nargs(e, name, 1)
            if not is_int(self.check_expr(args[0], PRIMS["i32"])):
                raise NyxError("exit code must be an integer", e.line, e.col)
            return VOID
        raise NyxError(f"internal: unknown builtin {name}", e.line, e.col)

    def check_call(self, e):
        callee = e.callee
        e.builtin = None
        e.recv = None
        if callee.kind == "field":
            base = callee.base
            # static method  Type.name(...)
            if base.kind == "name" and self.lookup(base.name) is None and base.name in self.structs:
                m = self.methods[base.name].get(callee.name)
                if m is None:
                    raise NyxError(f"{base.name} has no function '{callee.name}'", callee.line, callee.col)
                if m.self_mode:
                    raise NyxError(f"'{callee.name}' needs a value, call it as value.{callee.name}()", e.line, e.col)
                return self.finish_call(e, m.pts, m.ret, False, m.cname, f"{base.name}.{callee.name}")
            bt = self.check_expr(base)
            if bt.kind == "list" and callee.name in LIST_METHODS:
                return self.check_builtin(e, callee.name, [base] + e.args)
            if bt == STR and callee.name in STR_METHODS:
                return self.check_builtin(e, callee.name, [base] + e.args)
            if bt.kind != "struct":
                raise NyxError(f"type {bt} has no method '{callee.name}'", callee.line, callee.col)
            m = self.methods[bt.name].get(callee.name)
            if m is None:
                raise NyxError(f"{bt.name} has no method '{callee.name}'", callee.line, callee.col)
            if not m.self_mode:
                raise NyxError(f"'{callee.name}' is a static function, call it as {bt.name}.{callee.name}()",
                               e.line, e.col)
            if m.self_mode == "var":
                self.check_place(base)
            e.recv = base
            e.recv_mode = m.self_mode
            return self.finish_call(e, m.pts, m.ret, False, m.cname, callee.name)
        if callee.kind != "name":
            raise NyxError("only named functions can be called", e.line, e.col)
        name = callee.name
        if name in BUILTINS:
            return self.check_builtin(e, name, e.args)
        if name not in self.funcs:
            raise NyxError(f"unknown function '{name}'", callee.line, callee.col)
        pts, ret, variadic, cname, _ = self.funcs[name]
        return self.finish_call(e, pts, ret, variadic, cname, name)

    def finish_call(self, e, pts, ret, variadic, cname, label):
        if len(e.args) < len(pts) or (len(e.args) > len(pts) and not variadic):
            raise NyxError(f"'{label}' takes {len(pts)} argument(s) but got {len(e.args)}", e.line, e.col)
        for i, a in enumerate(e.args):
            if i < len(pts):
                self.expect(self.check_expr(a, pts[i]), pts[i], a)
            else:
                self.check_expr(a)
        e.cname = cname
        return ret


# ════════════════════════════════════════════════════════════════════
#  C code generation
# ════════════════════════════════════════════════════════════════════

C_RUNTIME = r"""/* Generated by the Nyx compiler {version}. Do not edit by hand. */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <inttypes.h>
{math}
static void nx_panic(const char* msg, int line) {{
    fprintf(stderr, "nyx runtime error: %s (line %d)\n", msg, line);
    exit(101);
}}
static void* nx_alloc(size_t n) {{
    void* p = malloc(n ? n : 1);
    if (!p) {{ fprintf(stderr, "nyx runtime error: out of memory\n"); exit(102); }}
    return p;
}}
static int64_t nx_check_idx(int64_t i, int64_t n, int line) {{
    if (i < 0 || i >= n) {{
        fprintf(stderr, "nyx runtime error: index %" PRId64 " out of bounds for length %" PRId64 " (line %d)\n", i, n, line);
        exit(101);
    }}
    return i;
}}
static const char* nx_str_concat(const char* a, const char* b) {{
    size_t la = strlen(a), lb = strlen(b);
    char* r = (char*)nx_alloc(la + lb + 1);
    memcpy(r, a, la); memcpy(r + la, b, lb + 1);
    return r;
}}
static int nx_str_cmp(const char* a, const char* b) {{ return strcmp(a, b); }}
static const char* nx_str_i64(int64_t v) {{ char* r = (char*)nx_alloc(24); snprintf(r, 24, "%" PRId64, v); return r; }}
static const char* nx_str_u64(uint64_t v) {{ char* r = (char*)nx_alloc(24); snprintf(r, 24, "%" PRIu64, v); return r; }}
static const char* nx_str_f64(double v) {{ char* r = (char*)nx_alloc(32); snprintf(r, 32, "%g", v); return r; }}
static const char* nx_str_bool(bool v) {{ return v ? "true" : "false"; }}
static const char* nx_str_quote(const char* s) {{ return nx_str_concat(nx_str_concat("\"", s), "\""); }}
static uint8_t nx_str_at(const char* s, int64_t i, int line) {{
    return (uint8_t)s[nx_check_idx(i, (int64_t)strlen(s), line)];
}}
static const char* nx_substr(const char* s, int64_t a, int64_t b, int line) {{
    int64_t n = (int64_t)strlen(s);
    if (a < 0 || b > n || a > b) nx_panic("substr range out of bounds", line);
    char* r = (char*)nx_alloc((size_t)(b - a + 1));
    memcpy(r, s + a, (size_t)(b - a)); r[b - a] = 0;
    return r;
}}
static int64_t nx_find(const char* s, const char* sub) {{
    const char* p = strstr(s, sub);
    return p ? (int64_t)(p - s) : -1;
}}
static const char* nx_chr(uint8_t c) {{ char* r = (char*)nx_alloc(2); r[0] = (char)c; r[1] = 0; return r; }}
static const char* nx_read_line(void) {{
    size_t cap = 64, len = 0;
    char* buf = (char*)nx_alloc(cap);
    int c;
    while ((c = getchar()) != EOF && c != '\n') {{
        if (len + 1 >= cap) {{ cap *= 2; buf = (char*)realloc(buf, cap); if (!buf) exit(102); }}
        buf[len++] = (char)c;
    }}
    if (len && buf[len - 1] == '\r') len--;
    buf[len] = 0;
    return buf;
}}
static int64_t nx_parse_int(const char* s) {{ return (int64_t)strtoll(s, NULL, 10); }}
static double nx_parse_float(const char* s) {{ return strtod(s, NULL); }}
static int64_t nx_time_ms(void) {{
    struct timespec ts;
    timespec_get(&ts, TIME_UTC);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}}
"""


class CGen:
    def __init__(self, checker):
        self.ck = checker
        self.out = []
        self.ind = 0
        self.typedefs = []
        self.helpers = []
        self.emitted = set()
        self.helper_done = set()
        self.tmp = 0

    # ── types ──
    def mangle(self, t):
        if t.kind == "array":
            return f"A{self.mangle(t.elem)}_{t.size}"
        if t.kind == "list":
            return "L" + self.mangle(t.elem)
        return t.name

    def ctype(self, t):
        if t.kind == "prim":
            if t.name in INT_TYPES:
                return INT_TYPES[t.name][0]
            if t.name in FLOAT_TYPES:
                return FLOAT_TYPES[t.name]
            return {"bool": "bool", "str": "const char*", "void": "void"}[t.name]
        self.need_type(t)
        if t.kind == "struct":
            return "nx_" + t.name
        if t.kind == "array":
            return "nx_" + self.mangle(t)
        return "nx_" + self.mangle(t)

    def need_type(self, t):
        if t.kind == "prim" or t in self.emitted:
            return
        self.emitted.add(t)
        if t.kind == "struct":
            fields = " ".join(f"{self.ctype(ft)} f_{fn};" for fn, ft in self.ck.structs[t.name].field_types)
            self.typedefs.append(f"struct nx_{t.name} {{ {fields} }};")
        elif t.kind == "array":
            ec = self.ctype(t.elem)
            self.typedefs.append(f"typedef struct {{ {ec} data[{t.size}]; }} nx_{self.mangle(t)};")
        else:
            ec = self.ctype(t.elem)
            name = "nx_" + self.mangle(t)
            self.typedefs.append(f"typedef struct {{ {ec}* data; int64_t len, cap; }} {name}_body; typedef {name}_body* {name};")
            self.helpers.append(self.list_helpers(name, ec))

    def list_helpers(self, L, T):
        return f"""static {L} {L}_new(void) {{ {L} l = ({L})nx_alloc(sizeof(*l)); l->data = NULL; l->len = 0; l->cap = 0; return l; }}
static void {L}_push({L} l, {T} v) {{
    if (l->len == l->cap) {{ l->cap = l->cap ? l->cap * 2 : 8; l->data = ({T}*)realloc(l->data, sizeof({T}) * (size_t)l->cap); if (!l->data) exit(102); }}
    l->data[l->len++] = v;
}}
static {L} {L}_from({T} const* items, int64_t n) {{ {L} l = {L}_new(); for (int64_t i = 0; i < n; i++) {L}_push(l, items[i]); return l; }}
static {T} {L}_pop({L} l, int line) {{ if (l->len == 0) nx_panic("pop from an empty list", line); return l->data[--l->len]; }}
static {T}* {L}_at({L} l, int64_t i, int line) {{ return &l->data[nx_check_idx(i, l->len, line)]; }}
static {T} {L}_remove({L} l, int64_t i, int line) {{
    nx_check_idx(i, l->len, line);
    {T} v = l->data[i];
    memmove(&l->data[i], &l->data[i + 1], sizeof({T}) * (size_t)(l->len - i - 1));
    l->len--;
    return v;
}}"""

    def tostr(self, t, v):
        """C expression that turns value v of type t into a const char*."""
        if is_int(t):
            if INT_TYPES[t.name][1]:
                return f"nx_str_i64((int64_t)({v}))"
            return f"nx_str_u64((uint64_t)({v}))"
        if is_float(t):
            return f"nx_str_f64((double)({v}))"
        if t == BOOL:
            return f"nx_str_bool({v})"
        if t == STR:
            return v
        fname = "nx_tostr_" + self.mangle(t)
        if fname not in self.helper_done:
            self.helper_done.add(fname)
            ct = self.ctype(t)
            if t.kind == "struct":
                parts = []
                for i, (fn, ft) in enumerate(self.ck.structs[t.name].field_types):
                    sep = ", " if i else " "
                    inner = self.tostr_inner(ft, f"v.f_{fn}")
                    parts.append(f'r = nx_str_concat(r, "{sep}{fn}: "); r = nx_str_concat(r, {inner});')
                body = f'const char* r = "{t.name} {{"; ' + " ".join(parts) + ' r = nx_str_concat(r, " }"); return r;'
            else:
                if t.kind == "array":
                    n, item = str(t.size), "v.data[i]"
                else:
                    n, item = "v->len", "v->data[i]"
                inner = self.tostr_inner(t.elem, item)
                body = (f'const char* r = "["; for (int64_t i = 0; i < {n}; i++) {{ if (i) r = nx_str_concat(r, ", "); '
                        f'r = nx_str_concat(r, {inner}); }} return nx_str_concat(r, "]");')
            self.helpers.append(f"static const char* {fname}({ct} v) {{ {body} }}")
        return f"{fname}({v})"

    def tostr_inner(self, t, v):
        if t == STR:
            return f"nx_str_quote({v})"
        return self.tostr(t, v)

    def line(self, s):
        self.out.append("    " * self.ind + s)

    # ── program ──
    def gen(self):
        ck = self.ck
        for name in ck.structs:
            self.need_type(ck.struct_types[name])
        protos, bodies = [], []

        def emit_fn(node, cname, pts, ret, method=None):
            params = []
            if method is not None and method.self_mode:
                st = self.ctype(ck.struct_types[method.struct])
                params.append(f"{st}* nx_self" if method.self_mode == "var" else f"{st} nx_self")
            params += [f"{self.ctype(pt)} nx_{pn}" for (pn, _, _, _), pt in zip(node.params, pts)]
            sig = f"static {self.ctype(ret)} {cname}({', '.join(params) or 'void'})"
            protos.append(sig + ";")
            self.out = []
            self.ind = 0
            self.line(sig + " {")
            self.ind += 1
            for s in node.body.stmts:
                self.stmt(s)
            self.ind -= 1
            self.line("}")
            bodies.append("\n".join(self.out))

        for it in ck.items:
            if it.kind == "fn":
                pts, ret, _, cname, _ = ck.funcs[it.name]
                emit_fn(it, cname, pts, ret)
            elif it.kind == "impl":
                for m in it.methods:
                    meth = ck.methods[it.name][m.name]
                    emit_fn(m, meth.cname, meth.pts, meth.ret, meth)
            elif it.kind == "extern" and it.name not in MATH_FUNCS and it.name not in STDLIB_C:
                pts, ret, variadic, cname, _ = ck.funcs[it.name]
                params = ", ".join(self.ctype(pt) for pt in pts)
                if variadic:
                    params = params + ", ..." if params else "..."
                protos.append(f"{self.ctype(ret)} {cname}({params or 'void'});")
        mret = ck.funcs["main"][1]
        if mret == VOID:
            entry = "int main(void) {\n    main_nx();\n    return 0;\n}"
        else:
            entry = "int main(void) {\n    return (int)main_nx();\n}"
        parts = [C_RUNTIME.format(version=VERSION, math="#include <math.h>\n" if ck.uses_math else "")]
        fwd = [f"typedef struct nx_{n} nx_{n};" for n in ck.structs]
        if fwd:
            parts.append("\n".join(fwd))
        if self.typedefs:
            parts.append("\n".join(self.typedefs))
        if self.helpers:
            parts.append("\n".join(self.helpers))
        parts.append("\n".join(protos))
        parts.append("\n\n".join(bodies))
        parts.append(entry)
        return "\n\n".join(parts) + "\n"

    # ── statements ──
    def block_body(self, blk):
        self.ind += 1
        for s in blk.stmts:
            self.stmt(s)
        self.ind -= 1

    def stmt(self, s):
        k = s.kind
        if k == "let":
            self.line(f"{self.ctype(s.ty)} {s.cname} = {self.expr(s.init)};")
        elif k == "assign":
            tgt = self.expr(s.target)
            if s.op == "+=" and s.target.ty == STR:
                self.line(f"{tgt} = nx_str_concat({tgt}, {self.expr(s.value)});")
            else:
                self.line(f"{tgt} {s.op} {self.expr(s.value)};")
        elif k == "exprstmt":
            self.line(self.expr(s.expr) + ";")
        elif k == "if":
            self.line(f"if ({self.expr(s.cond)}) {{")
            self.block_body(s.then)
            self.gen_else(s.els)
        elif k == "while":
            self.line(f"while ({self.expr(s.cond)}) {{")
            self.block_body(s.body)
            self.line("}")
        elif k == "for":
            ct = self.ctype(s.ty)
            self.tmp += 1
            end = f"nx_end_{self.tmp}"
            self.line("{")
            self.ind += 1
            self.line(f"{ct} {end} = {self.expr(s.end)};")
            self.line(f"for ({ct} {s.cname} = {self.expr(s.start)}; {s.cname} < {end}; {s.cname}++) {{")
            self.block_body(s.body)
            self.line("}")
            self.ind -= 1
            self.line("}")
        elif k == "foreach":
            it = s.iterable.ty
            self.tmp += 1
            src, idx = f"nx_it_{self.tmp}", f"nx_i_{self.tmp}"
            self.line("{")
            self.ind += 1
            self.line(f"{self.ctype(it)} {src} = {self.expr(s.iterable)};")
            if it.kind == "list":
                cond, item = f"{idx} < {src}->len", f"{src}->data[{idx}]"
            elif it.kind == "array":
                cond, item = f"{idx} < {it.size}", f"{src}.data[{idx}]"
            else:
                cond, item = f"{src}[{idx}] != 0", f"(uint8_t){src}[{idx}]"
            self.line(f"for (int64_t {idx} = 0; {cond}; {idx}++) {{")
            self.ind += 1
            self.line(f"{self.ctype(s.ty)} {s.cname} = {item};")
            self.ind -= 1
            self.block_body(s.body)
            self.line("}")
            self.ind -= 1
            self.line("}")
        elif k == "return":
            self.line("return;" if s.value is None else f"return {self.expr(s.value)};")
        elif k == "break":
            self.line("break;")
        elif k == "continue":
            self.line("continue;")

    def gen_else(self, els):
        if els is None:
            self.line("}")
        elif els.kind == "if":
            self.line(f"}} else if ({self.expr(els.cond)}) {{")
            self.block_body(els.then)
            self.gen_else(els.els)
        else:
            self.line("} else {")
            self.block_body(els)
            self.line("}")

    # ── expressions ──
    def expr(self, e):
        k = e.kind
        if k == "int":
            if is_float(e.ty):
                return repr(float(e.value)) + ("f" if e.ty.name == "f32" else "")
            if e.ty.name == "u64":
                return f"UINT64_C({e.value})"
            if e.ty.name == "i64":
                return f"INT64_C({e.value})"
            return str(e.value)
        if k == "float":
            r = repr(float(e.value))
            if r in ("inf", "nan"):
                r = "1e309"
            return r + ("f" if e.ty.name == "f32" else "")
        if k == "bool":
            return "true" if e.value else "false"
        if k == "str":
            return c_string(e.value)
        if k == "name":
            return e.cname
        if k == "unary":
            return f"({e.op}{self.expr(e.operand)})"
        if k == "binary":
            if e.left.ty == STR:
                l, r = self.expr(e.left), self.expr(e.right)
                if e.op == "+":
                    return f"nx_str_concat({l}, {r})"
                return f"(nx_str_cmp({l}, {r}) {e.op} 0)"
            return f"({self.expr(e.left)} {e.op} {self.expr(e.right)})"
        if k == "cast":
            return f"(({self.ctype(e.ty)})({self.expr(e.expr)}))"
        if k == "field":
            return f"{self.expr(e.base)}.f_{e.name}"
        if k == "index":
            bt = e.base.ty
            if bt.kind == "list":
                return f"(*nx_{self.mangle(bt)}_at({self.expr(e.base)}, (int64_t)({self.expr(e.index)}), {e.line}))"
            if bt == STR:
                return f"nx_str_at({self.expr(e.base)}, (int64_t)({self.expr(e.index)}), {e.line})"
            return f"{self.expr(e.base)}.data[nx_check_idx((int64_t)({self.expr(e.index)}), {bt.size}, {e.line})]"
        if k == "structlit":
            vals = {fn: v for fn, v, _, _ in e.inits}
            inits = ", ".join(f".f_{fn} = {self.expr(vals[fn])}" for fn, _ in self.ck.structs[e.name].field_types)
            return f"(({self.ctype(e.ty)}){{ {inits} }})"
        if k == "arrayrep":
            t = e.ty
            fname = "nx_fill_" + self.mangle(t)
            if fname not in self.helper_done:
                self.helper_done.add(fname)
                ct, ec = self.ctype(t), self.ctype(t.elem)
                self.helpers.append(f"static {ct} {fname}({ec} v) {{ {ct} a; for (int64_t i = 0; i < {t.size}; i++) a.data[i] = v; return a; }}")
            return f"{fname}({self.expr(e.item)})"
        if k == "arraylit":
            ct = self.ctype(e.ty)
            if e.as_list:
                if not e.items:
                    return f"{ct}_new()"
                ec = self.ctype(e.ty.elem)
                items = ", ".join(self.expr(i) for i in e.items)
                return f"{ct}_from(({ec}[]){{ {items} }}, {len(e.items)})"
            items = ", ".join(self.expr(i) for i in e.items)
            return f"(({ct}){{ {{ {items} }} }})"
        if k == "call":
            if e.builtin:
                return self.gen_builtin(e)
            args = [self.expr(a) for a in e.args]
            if e.recv is not None:
                r = self.expr(e.recv)
                args.insert(0, f"&({r})" if e.recv_mode == "var" else r)
            return f"{e.cname}({', '.join(args)})"
        raise NyxError(f"internal: cannot generate {k}", e.line, e.col)

    def gen_builtin(self, e):
        name, a = e.builtin, e.bargs
        if name == "print":
            if not a:
                return 'printf("\\n")'
            fmt, vals = [], []
            for x in a:
                t, v = x.ty, self.expr(x)
                if is_int(t):
                    if INT_TYPES[t.name][1]:
                        fmt.append('%" PRId64 "')
                        vals.append(f"(int64_t)({v})")
                    else:
                        fmt.append('%" PRIu64 "')
                        vals.append(f"(uint64_t)({v})")
                elif is_float(t):
                    fmt.append("%g")
                    vals.append(f"(double)({v})")
                elif t == BOOL:
                    fmt.append("%s")
                    vals.append(f'(({v}) ? "true" : "false")')
                elif t == STR:
                    fmt.append("%s")
                    vals.append(v)
                else:
                    fmt.append("%s")
                    vals.append(self.tostr(t, v))
            return "printf(" + ", ".join(['"' + " ".join(fmt) + '\\n"'] + vals) + ")"
        if name == "str":
            return self.tostr(a[0].ty, self.expr(a[0]))
        if name == "len":
            t = a[0].ty
            if t.kind == "array":
                return f"INT64_C({t.size})"
            if t.kind == "list":
                return f"({self.expr(a[0])}->len)"
            return f"((int64_t)strlen({self.expr(a[0])}))"
        if name in ("push", "pop", "remove", "clear"):
            L = "nx_" + self.mangle(a[0].ty)
            lst = self.expr(a[0])
            if name == "push":
                return f"{L}_push({lst}, {self.expr(a[1])})"
            if name == "pop":
                return f"{L}_pop({lst}, {e.line})"
            if name == "remove":
                return f"{L}_remove({lst}, (int64_t)({self.expr(a[1])}), {e.line})"
            return f"(({lst})->len = 0)"
        if name == "substr":
            return f"nx_substr({self.expr(a[0])}, (int64_t)({self.expr(a[1])}), (int64_t)({self.expr(a[2])}), {e.line})"
        if name == "contains":
            return f"(nx_find({self.expr(a[0])}, {self.expr(a[1])}) >= 0)"
        if name == "find":
            return f"nx_find({self.expr(a[0])}, {self.expr(a[1])})"
        if name == "chr":
            return f"nx_chr((uint8_t)({self.expr(a[0])}))"
        if name == "read_line":
            return "nx_read_line()"
        if name == "parse_int":
            return f"nx_parse_int({self.expr(a[0])})"
        if name == "parse_float":
            return f"nx_parse_float({self.expr(a[0])})"
        if name == "time_ms":
            return "nx_time_ms()"
        if name == "exit":
            return f"exit((int)({self.expr(a[0])}))"
        raise NyxError(f"internal: builtin {name}", e.line, e.col)


def c_string(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif o < 32 or o == 127:
            out.append(f"\\{o:03o}")
        elif o > 127:
            out.append("".join(f"\\{b:03o}" for b in ch.encode("utf-8")))
        elif ch == "?":
            out.append("\\?")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


# ════════════════════════════════════════════════════════════════════
#  Driver
# ════════════════════════════════════════════════════════════════════

def tag_file(node, path):
    if isinstance(node, Node):
        node.file = path
        for v in node.__dict__.values():
            tag_file(v, path)
    elif isinstance(node, (list, tuple)):
        for v in node:
            tag_file(v, path)


def load_program(path, source=None, sources=None, seen=None):
    """Parse a file and everything it imports. Returns the list of items."""
    sources = {} if sources is None else sources
    seen = set() if seen is None else seen
    path = os.path.abspath(path) if path != "<input>" else path
    if path in seen:
        return []
    seen.add(path)
    if source is None:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    sources[path] = source
    try:
        items = Parser(lex(source)).program()
    except NyxError as ex:
        ex.file = ex.file or path
        raise
    out = []
    base = os.path.dirname(path) if path != "<input>" else os.getcwd()
    for it in items:
        tag_file(it, path)
        if it.kind == "import":
            if it.path.endswith(".nyx"):
                # a file path, relative to the importing file
                cands = [os.path.join(base, it.path)]
            else:
                # a bare name like "math" is a standard library module first
                cands = [os.path.join(LIB_DIR, it.path + ".nyx"), os.path.join(base, it.path + ".nyx")]
            target = next((c for c in cands if os.path.isfile(c)), None)
            if target is None:
                raise NyxError(f"cannot find module '{it.path}'", it.line, it.col, path)
            out.extend(load_program(target, None, sources, seen))
        else:
            out.append(it)
    return out


def compile_to_c(source, path="<input>", sources=None):
    items = load_program(path, source, sources)
    ck = Checker(items)
    ck.run()
    return CGen(ck).gen(), ck


def find_cc():
    for cc in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if cc and shutil.which(cc):
            return cc
    return None


def build(source, out_path, opt="-O2", path="<input>", sources=None):
    csrc, ck = compile_to_c(source, path, sources)
    cc = find_cc()
    if cc is None:
        raise RuntimeError("no C compiler found. Install gcc or clang, or set the CC variable")
    with tempfile.TemporaryDirectory() as td:
        cpath = os.path.join(td, "out.c")
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(csrc)
        cmd = [cc, opt, "-std=c11", "-w", cpath, "-o", out_path]
        if ck.uses_math:
            cmd.append("-lm")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("C compiler failed\n" + r.stderr)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = "usage: nyx (run|build|emit|check) file.nyx [-o output] [-O0|-O1|-O2|-O3]"
    if argv and argv[0] in ("-v", "--version", "version"):
        print(f"nyx {VERSION}")
        return 0
    if len(argv) < 2 or argv[0] not in ("run", "build", "emit", "check"):
        print(usage, file=sys.stderr)
        return 2
    cmd, path = argv[0], argv[1]
    out, opt = None, "-O2"
    rest = argv[2:]
    i = 0
    while i < len(rest):
        if rest[i] == "-o" and i + 1 < len(rest):
            out = rest[i + 1]
            i += 2
        elif rest[i] in ("-O0", "-O1", "-O2", "-O3"):
            opt = rest[i]
            i += 1
        else:
            print(usage, file=sys.stderr)
            return 2
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError as ex:
        print(f"nyx: cannot read {path}: {ex.strerror}", file=sys.stderr)
        return 1
    sources = {}
    apath = os.path.abspath(path)
    try:
        if cmd == "check":
            compile_to_c(source, apath, sources)
            print(f"{path}: ok")
            return 0
        if cmd == "emit":
            print(compile_to_c(source, apath, sources)[0])
            return 0
        if cmd == "build":
            if out is None:
                out = os.path.splitext(os.path.basename(path))[0]
                if os.name == "nt":
                    out += ".exe"
            build(source, out, opt, apath, sources)
            print(f"built {out}")
            return 0
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "prog.exe" if os.name == "nt" else "prog")
            build(source, exe, opt, apath, sources)
            return subprocess.call([exe])
    except NyxError as ex:
        f = ex.file or apath
        shown = os.path.relpath(f) if f != "<input>" else path
        print(ex.render(shown, sources.get(f, source)), file=sys.stderr)
        return 1
    except RuntimeError as ex:
        print(f"nyx: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
