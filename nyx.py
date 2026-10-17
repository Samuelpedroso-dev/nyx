#!/usr/bin/env python3
"""Nyx compiler, version 0.1.

Nyx is a small, statically typed systems language that compiles to C.
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

VERSION = "0.1.0"


# ════════════════════════════════════════════════════════════════════
#  Errors
# ════════════════════════════════════════════════════════════════════

class NyxError(Exception):
    def __init__(self, msg, line=0, col=0):
        super().__init__(msg)
        self.msg, self.line, self.col = msg, line, col

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

    def __repr__(self):
        return f"Tok({self.kind},{self.value!r},{self.line}:{self.col})"


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
                while j < n and (src[j] in "0123456789abcdefABCDEF_"):
                    j += 1
                text = src[i:j].replace("_", "")
                val = int(text, 16)
            elif src.startswith(("0b", "0B"), i):
                j += 2
                while j < n and src[j] in "01_":
                    j += 1
                text = src[i:j].replace("_", "")
                val = int(text, 2)
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

    def __repr__(self):
        fields = {k: v for k, v in self.__dict__.items() if k not in ("line", "col")}
        return f"Node({fields})"


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

    # helpers
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

    # program
    def program(self):
        items = []
        while not self.at("eof"):
            if self.at("kw", "fn"):
                items.append(self.func())
            elif self.at("kw", "struct"):
                items.append(self.struct())
            elif self.at("kw", "extern"):
                items.append(self.extern())
            else:
                tk = self.peek()
                raise NyxError("expected 'fn', 'struct' or 'extern' at top level", tk.line, tk.col)
        return items

    def type_expr(self):
        tk = self.peek()
        if self.accept("op", "["):
            elem = self.type_expr()
            self.eat("op", ";")
            size = self.eat("int")
            self.eat("op", "]")
            if size.value <= 0:
                raise NyxError("array size must be positive", size.line, size.col)
            return Node("tarray", tk.line, tk.col, elem=elem, size=size.value)
        name = self.eat("ident")
        return Node("tname", tk.line, tk.col, name=name.value)

    def params(self):
        self.eat("op", "(")
        ps, variadic = [], False
        while not self.at_op(")"):
            if self.accept("op", "..."):
                variadic = True
                break
            name = self.eat("ident")
            self.eat("op", ":")
            ps.append((name.value, self.type_expr(), name.line, name.col))
            if not self.accept("op", ","):
                break
        self.eat("op", ")")
        ret = None
        if self.accept("op", "->"):
            ret = self.type_expr()
        return ps, ret, variadic

    def func(self):
        kw = self.eat("kw", "fn")
        name = self.eat("ident")
        ps, ret, variadic = self.params()
        if variadic:
            raise NyxError("only extern functions can be variadic", kw.line, kw.col)
        body = self.block()
        return Node("fn", kw.line, kw.col, name=name.value, params=ps, ret=ret, body=body)

    def extern(self):
        kw = self.eat("kw", "extern")
        self.eat("kw", "fn")
        name = self.eat("ident")
        ps, ret, variadic = self.params()
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
                self.eat("op", "..")
                end = self.expr()
                return Node("for", tk.line, tk.col, var=var.value, start=start, end=end, body=self.block())
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
            # struct literal  Name { field: value, ... }
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
            if not items:
                raise NyxError("empty array literal", tk.line, tk.col)
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
        return self.name


INT_TYPES = {
    "i8": ("int8_t", True, 8), "i16": ("int16_t", True, 16),
    "i32": ("int32_t", True, 32), "i64": ("int64_t", True, 64),
    "u8": ("uint8_t", False, 8), "u16": ("uint16_t", False, 16),
    "u32": ("uint32_t", False, 32), "u64": ("uint64_t", False, 64),
}
FLOAT_TYPES = {"f32": "float", "f64": "double"}
PRIMS = {n: Type("prim", n) for n in [*INT_TYPES, *FLOAT_TYPES, "bool", "str", "void"]}
I64, F64, BOOL, STR, VOID = PRIMS["i64"], PRIMS["f64"], PRIMS["bool"], PRIMS["str"], PRIMS["void"]


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


# ════════════════════════════════════════════════════════════════════
#  Type checker
# ════════════════════════════════════════════════════════════════════

class Sym:
    def __init__(self, ty, mutable, cname):
        self.ty, self.mutable, self.cname = ty, mutable, cname


class Checker:
    def __init__(self, items):
        self.items = items
        self.structs = {}      # name -> Node
        self.struct_types = {}  # name -> Type
        self.funcs = {}        # name -> (param types, ret type, variadic, cname, node)
        self.scopes = []
        self.cur_ret = None
        self.loop_depth = 0
        self.uses_math = False

    # scopes
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

    # types
    def resolve(self, tn):
        if tn is None:
            return VOID
        if tn.kind == "tarray":
            elem = self.resolve(tn.elem)
            if elem == VOID:
                raise NyxError("array of void is not allowed", tn.line, tn.col)
            return Type("array", elem=elem, size=tn.size)
        if tn.name in PRIMS:
            return PRIMS[tn.name]
        if tn.name in self.struct_types:
            return self.struct_types[tn.name]
        raise NyxError(f"unknown type '{tn.name}'", tn.line, tn.col)

    def run(self):
        for it in self.items:
            if it.kind == "struct":
                if it.name in self.structs or it.name in PRIMS:
                    raise NyxError(f"type '{it.name}' is already defined", it.line, it.col)
                self.structs[it.name] = it
                self.struct_types[it.name] = Type("struct", name=it.name)
        for it in self.items:
            if it.kind == "struct":
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
        self.check_recursive_structs()
        for it in self.items:
            if it.kind in ("fn", "extern"):
                if it.name in self.funcs or it.name in ("print", "len"):
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
        if "main" not in self.funcs or self.funcs["main"][4].kind != "fn":
            raise NyxError("program has no 'fn main()'", 1, 1)
        mpts, mret, _, _, mnode = self.funcs["main"]
        if mpts or mret not in (VOID, PRIMS["i32"], I64):
            raise NyxError("main must take no parameters and return nothing, i32 or i64", mnode.line, mnode.col)
        for it in self.items:
            if it.kind == "fn":
                self.check_fn(it)

    def check_recursive_structs(self):
        state = {}

        def visit(name, node):
            if state.get(name) == 1:
                raise NyxError(f"struct '{name}' contains itself by value", node.line, node.col)
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

    def check_fn(self, fn):
        pts, ret, _, _, _ = self.funcs[fn.name]
        self.cur_ret = ret
        self.push()
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
        return False

    def has_break(self, node):
        if node.kind == "break":
            return True
        if node.kind in ("while", "for"):
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
            self.push()
            self.declare(s.var, Sym(st, False, s.cname), s)
            self.loop_depth += 1
            self.check_block(s.body, new_scope=False)
            self.loop_depth -= 1
            self.pop()
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

    def check_place(self, e):
        if e.kind == "name":
            sym = self.lookup(e.name)
            if sym is None:
                raise NyxError(f"unknown name '{e.name}'", e.line, e.col)
            if not sym.mutable:
                raise NyxError(f"cannot assign to '{e.name}', declare it with 'var' to make it mutable", e.line, e.col)
            e.ty, e.cname = sym.ty, sym.cname
            return sym.ty
        if e.kind == "field":
            self.check_place(e.base)
            return self.check_field(e)
        if e.kind == "index":
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
        raise NyxError(f"struct {bt.name} has no field '{e.name}'", e.line, e.col)

    def check_index(self, e):
        bt = e.base.ty
        if bt.kind != "array":
            raise NyxError(f"type {bt} cannot be indexed", e.line, e.col)
        it = self.check_expr(e.index, I64)
        if not is_int(it):
            raise NyxError("array index must be an integer", e.index.line, e.index.col)
        e.ty = bt.elem
        return bt.elem

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
            ok = (is_num(src) and is_num(dst)) or (is_int(src) and dst == BOOL) or (src == BOOL and is_int(dst)) or src == dst
            if not ok:
                raise NyxError(f"cannot cast {src} to {dst}", e.line, e.col)
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
            elem_want = want.elem if (want is not None and want.kind == "array") else None
            et = self.check_expr(e.item, elem_want)
            if et == VOID:
                raise NyxError("array of void is not allowed", e.line, e.col)
            return Type("array", elem=et, size=e.size)
        if k == "arraylit":
            elem_want = want.elem if (want is not None and want.kind == "array") else None
            et = self.check_expr(e.items[0], elem_want)
            for it in e.items[1:]:
                self.expect(self.check_expr(it, et), et, it)
            if et == VOID:
                raise NyxError("array of void is not allowed", e.line, e.col)
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
            raise NyxError(f"operands of '{op}' have different types, {lt} and {rt}. Use 'as' to convert", e.line, e.col)
        if op in ("==", "!="):
            if lt.kind != "prim" or lt == VOID or lt == STR:
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

    def check_call(self, e):
        if e.callee.kind != "name":
            raise NyxError("only named functions can be called", e.line, e.col)
        name = e.callee.name
        if name == "print":
            for a in e.args:
                t = self.check_expr(a)
                if t.kind != "prim" or t == VOID:
                    raise NyxError(f"print cannot show values of type {t}", a.line, a.col)
            e.builtin = "print"
            return VOID
        if name == "len":
            if len(e.args) != 1:
                raise NyxError("len takes exactly one argument", e.line, e.col)
            t = self.check_expr(e.args[0])
            if t.kind != "array":
                raise NyxError(f"len needs an array, got {t}", e.line, e.col)
            e.builtin = "len"
            return I64
        if name not in self.funcs:
            raise NyxError(f"unknown function '{name}'", e.callee.line, e.callee.col)
        pts, ret, variadic, cname, _ = self.funcs[name]
        if len(e.args) < len(pts) or (len(e.args) > len(pts) and not variadic):
            raise NyxError(f"'{name}' takes {len(pts)} argument(s) but got {len(e.args)}", e.line, e.col)
        for i, a in enumerate(e.args):
            if i < len(pts):
                self.expect(self.check_expr(a, pts[i]), pts[i], a)
            else:
                self.check_expr(a)
        e.builtin = None
        e.cname = cname
        return ret


MATH_FUNCS = {
    "sqrt", "sin", "cos", "tan", "atan2", "pow", "exp", "log", "fabs", "floor", "ceil",
    "sqrtf", "sinf", "cosf", "powf", "fmin", "fmax", "fmod", "round",
}


# ════════════════════════════════════════════════════════════════════
#  C code generation
# ════════════════════════════════════════════════════════════════════

C_PRELUDE = r"""/* Generated by the Nyx compiler {version}. Do not edit by hand. */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <inttypes.h>
{math}
static inline int64_t nx_check_idx(int64_t i, int64_t n, int line) {{
    if (i < 0 || i >= n) {{
        fprintf(stderr, "nyx runtime error: index %" PRId64 " out of bounds for length %" PRId64 " (line %d)\n", i, n, line);
        exit(101);
    }}
    return i;
}}
"""


class CGen:
    def __init__(self, checker):
        self.ck = checker
        self.out = []
        self.ind = 0
        self.typedefs = []
        self.emitted_types = set()
        self.fillers = set()
        self.tmp = 0

    def ctype(self, t):
        if t.kind == "prim":
            if t.name in INT_TYPES:
                return INT_TYPES[t.name][0]
            if t.name in FLOAT_TYPES:
                return FLOAT_TYPES[t.name]
            return {"bool": "bool", "str": "const char*", "void": "void"}[t.name]
        self.need_type(t)
        return self.type_cname(t)

    def type_cname(self, t):
        if t.kind == "struct":
            return "nx_" + t.name
        return "nx_arr_" + self.mangle(t.elem) + "_" + str(t.size)

    def mangle(self, t):
        if t.kind == "array":
            return "a" + self.mangle(t.elem) + "_" + str(t.size)
        return t.name

    def need_type(self, t):
        if t.kind == "prim" or t in self.emitted_types:
            return
        self.emitted_types.add(t)
        if t.kind == "array":
            ec = self.ctype(t.elem)
            self.typedefs.append(f"typedef struct {{ {ec} data[{t.size}]; }} {self.type_cname(t)};")
        else:
            fields = [f"{self.ctype(ft)} f_{fn};" for fn, ft in self.ck.structs[t.name].field_types]
            body = " ".join(fields)
            self.typedefs.append(f"typedef struct {{ {body} }} {self.type_cname(t)};")

    def line(self, s):
        self.out.append("    " * self.ind + s)

    def gen(self):
        ck = self.ck
        for name in ck.structs:
            self.need_type(ck.struct_types[name])
        protos, bodies = [], []
        for it in ck.items:
            if it.kind == "fn":
                pts, ret, _, cname, _ = ck.funcs[it.name]
                params = ", ".join(f"{self.ctype(pt)} nx_{pn}" for (pn, _, _, _), pt in zip(it.params, pts)) or "void"
                sig = f"static {self.ctype(ret)} {cname}({params})"
                protos.append(sig + ";")
                self.out = []
                self.ind = 0
                self.line(sig + " {")
                self.ind += 1
                for s in it.body.stmts:
                    self.stmt(s)
                self.ind -= 1
                self.line("}")
                bodies.append("\n".join(self.out))
            elif it.kind == "extern" and it.name not in MATH_FUNCS and it.name not in STDLIB_C:
                pts, ret, variadic, cname, _ = ck.funcs[it.name]
                params = ", ".join(self.ctype(pt) for pt in pts)
                if variadic:
                    params = (params + ", ..." if params else "...")
                protos.append(f"{self.ctype(ret)} {cname}({params or 'void'});")
        mret = ck.funcs["main"][1]
        if mret == VOID:
            entry = "int main(void) {\n    main_nx();\n    return 0;\n}"
        else:
            entry = "int main(void) {\n    return (int)main_nx();\n}"
        prelude = C_PRELUDE.format(version=VERSION, math="#include <math.h>\n" if ck.uses_math else "")
        parts = [prelude]
        if self.typedefs:
            parts.append("\n".join(self.typedefs) + "\n")
        parts.append("\n".join(protos) + "\n")
        parts.append("\n\n".join(bodies) + "\n")
        parts.append(entry + "\n")
        return "\n".join(parts)

    # statements
    def block_body(self, blk):
        self.ind += 1
        for s in blk.stmts:
            self.stmt(s)
        self.ind -= 1

    def stmt(self, s):
        k = s.kind
        if k == "let":
            self.line(f"{self.ctype(s.ty)} {s.cname} = {self.expr(s.init, s.ty)};")
        elif k == "assign":
            tgt = self.expr(s.target)
            self.line(f"{tgt} {s.op} {self.expr(s.value, s.target.ty)};")
        elif k == "exprstmt":
            self.line(self.expr(s.expr) + ";")
        elif k == "if":
            self.gen_if(s, "if")
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
        elif k == "return":
            self.line("return;" if s.value is None else f"return {self.expr(s.value)};")
        elif k == "break":
            self.line("break;")
        elif k == "continue":
            self.line("continue;")

    def gen_if(self, s, prefix):
        self.line(f"{prefix} ({self.expr(s.cond)}) {{")
        self.block_body(s.then)
        if s.els is None:
            self.line("}")
        elif s.els.kind == "if":
            self.gen_if_chain(s.els)
        else:
            self.line("} else {")
            self.block_body(s.els)
            self.line("}")

    def gen_if_chain(self, s):
        self.line(f"}} else if ({self.expr(s.cond)}) {{")
        self.block_body(s.then)
        if s.els is None:
            self.line("}")
        elif s.els.kind == "if":
            self.gen_if_chain(s.els)
        else:
            self.line("} else {")
            self.block_body(s.els)
            self.line("}")

    # expressions
    def expr(self, e, want=None):
        k = e.kind
        if k == "int":
            if is_float(e.ty):
                return repr(float(e.value))
            if e.ty.name in ("u64",):
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
            return f"({self.expr(e.left)} {e.op} {self.expr(e.right)})"
        if k == "cast":
            return f"(({self.ctype(e.ty)})({self.expr(e.expr)}))"
        if k == "field":
            return f"{self.expr(e.base)}.f_{e.name}"
        if k == "index":
            n = e.base.ty.size
            return f"{self.expr(e.base)}.data[nx_check_idx((int64_t)({self.expr(e.index)}), {n}, {e.line})]"
        if k == "structlit":
            vals = {fn: v for fn, v, _, _ in e.inits}
            inits = ", ".join(f".f_{fn} = {self.expr(vals[fn])}" for fn, _ in self.ck.structs[e.name].field_types)
            return f"(({self.ctype(e.ty)}){{ {inits} }})"
        if k == "arrayrep":
            t = e.ty
            fname = "nx_fill_" + self.mangle(t.elem) + "_" + str(t.size)
            if fname not in self.fillers:
                self.fillers.add(fname)
                ct, ec = self.ctype(t), self.ctype(t.elem)
                self.typedefs.append(
                    f"static inline {ct} {fname}({ec} v) {{ {ct} a; for (int64_t i = 0; i < {t.size}; i++) a.data[i] = v; return a; }}")
            return f"{fname}({self.expr(e.item)})"
        if k == "arraylit":
            items = ", ".join(self.expr(i) for i in e.items)
            return f"(({self.ctype(e.ty)}){{ {{ {items} }} }})"
        if k == "call":
            if e.builtin == "len":
                return f"INT64_C({e.args[0].ty.size})"
            if e.builtin == "print":
                return self.gen_print(e.args)
            args = ", ".join(self.expr(a) for a in e.args)
            return f"{e.cname}({args})"
        raise NyxError(f"internal: cannot generate {k}", e.line, e.col)

    def gen_print(self, args):
        fmt, vals = [], []
        for a in args:
            t = a.ty
            v = self.expr(a)
            if is_int(t):
                if INT_TYPES[t.name][1]:
                    fmt.append("%\" PRId64 \"")
                    vals.append(f"(int64_t)({v})")
                else:
                    fmt.append("%\" PRIu64 \"")
                    vals.append(f"(uint64_t)({v})")
            elif is_float(t):
                fmt.append("%g")
                vals.append(f"(double)({v})")
            elif t == BOOL:
                fmt.append("%s")
                vals.append(f"(({v}) ? \"true\" : \"false\")")
            else:
                fmt.append("%s")
                vals.append(v)
        f = '"' + " ".join(fmt) + '\\n"'
        return "printf(" + ", ".join([f] + vals) + ")"


STDLIB_C = {"abs", "rand", "srand", "exit", "putchar", "getchar", "printf", "puts"}


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
            out.append(f'\\{o:03o}')
        elif o > 127:
            out.append("".join(f'\\{b:03o}' for b in ch.encode("utf-8")))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


# ════════════════════════════════════════════════════════════════════
#  Driver
# ════════════════════════════════════════════════════════════════════

def compile_to_c(source):
    items = Parser(lex(source)).program()
    ck = Checker(items)
    ck.run()
    return CGen(ck).gen(), ck


def find_cc():
    for cc in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if cc and shutil.which(cc):
            return cc
    return None


def build(source, out_path, opt="-O2"):
    csrc, ck = compile_to_c(source)
    cc = find_cc()
    if cc is None:
        raise RuntimeError("no C compiler found. Install gcc or clang, or set the CC variable")
    with tempfile.TemporaryDirectory() as td:
        cpath = os.path.join(td, "out.c")
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(csrc)
        cmd = [cc, opt, "-std=c99", "-w", cpath, "-o", out_path]
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
    try:
        if cmd == "check":
            compile_to_c(source)
            print(f"{path}: ok")
            return 0
        if cmd == "emit":
            print(compile_to_c(source)[0])
            return 0
        if cmd == "build":
            if out is None:
                out = os.path.splitext(os.path.basename(path))[0]
                if os.name == "nt":
                    out += ".exe"
            build(source, out, opt)
            print(f"built {out}")
            return 0
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "prog.exe" if os.name == "nt" else "prog")
            build(source, exe, opt)
            return subprocess.call([exe])
    except NyxError as ex:
        print(ex.render(path, source), file=sys.stderr)
        return 1
    except RuntimeError as ex:
        print(f"nyx: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
