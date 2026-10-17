#!/usr/bin/env python3
"""Nyx test suite.

Runs every example and compares its output with the matching .expected file,
then checks that invalid programs are rejected with the right error message.

    python3 tests/run_tests.py
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import nyx  # noqa: E402

# Each case is (name, source, text that must appear in the error).
ERROR_CASES = [
    ("immutable assignment", "fn main() { let x = 1\n x = 2 }", "declare it with 'var'"),
    ("type mismatch", "fn main() { let x: i32 = true }", "expected i32 but got bool"),
    ("mixed types", "fn main() { let a: i32 = 1\n let b: i64 = 2\n let c = a + b }", "different types"),
    ("unknown name", "fn main() { print(y) }", "unknown name 'y'"),
    ("missing return", "fn f(x: i64) -> i64 { if x > 0 { return 1 } }\nfn main() {}", "every path"),
    ("wrong arg count", "fn f(a: i64) -> i64 { return a }\nfn main() { f(1, 2) }", "takes 1 argument"),
    ("no main", "fn helper() {}", "no 'fn main()'"),
    ("literal overflow", "fn main() { let b: u8 = 300 }", "does not fit in u8"),
    ("break outside loop", "fn main() { break }", "outside of a loop"),
    ("unknown field", "struct P { x: f64 }\nfn main() { let p = P { x: 1.0 }\n print(p.y) }", "no field 'y'"),
    ("missing field", "struct P { x: f64, y: f64 }\nfn main() { let p = P { x: 1.0 } }", "missing field"),
    ("recursive struct", "struct N { next: N }\nfn main() {}", "contains itself"),
    ("bitwise on float", "fn main() { let x = 1.5 & 2.0 }", "integer operands"),
    ("bad cast", "fn main() { let s = \"a\" as i64 }", "cannot cast"),
    ("redeclared", "fn main() { let a = 1\n let a = 2 }", "already declared"),
    ("unterminated string", "fn main() { print(\"oops) }", "unterminated string"),
]

RUNTIME_CASES = [
    ("bounds check", "fn main() { var a = [1, 2, 3]\n var i = 5\n a[i] = 0 }", "out of bounds", 101),
]


def main():
    passed = failed = 0

    def report(ok, name, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  ok    {name}")
        else:
            failed += 1
            print(f"  FAIL  {name}  {detail}")

    print("examples")
    ex_dir = os.path.join(ROOT, "examples")
    for fname in sorted(os.listdir(ex_dir)):
        if not fname.endswith(".nyx"):
            continue
        path = os.path.join(ex_dir, fname)
        expected_path = path[:-4] + ".expected"
        r = subprocess.run([sys.executable, os.path.join(ROOT, "nyx.py"), "run", path],
                           capture_output=True, text=True)
        expected = open(expected_path, encoding="utf-8").read() if os.path.exists(expected_path) else None
        if r.returncode != 0:
            report(False, fname, r.stderr.strip())
        elif expected is not None and r.stdout != expected:
            report(False, fname, "output differs from .expected")
        else:
            report(True, fname)

    print("compile errors")
    for name, src, needle in ERROR_CASES:
        try:
            nyx.compile_to_c(src)
            report(False, name, "compiled but should have failed")
        except nyx.NyxError as ex:
            report(needle in ex.msg, name, f"got '{ex.msg}'")

    print("runtime checks")
    for name, src, needle, code in RUNTIME_CASES:
        with tempfile.TemporaryDirectory() as td:
            exe = os.path.join(td, "prog")
            nyx.build(src, exe)
            r = subprocess.run([exe], capture_output=True, text=True)
            report(r.returncode == code and needle in r.stderr, name, f"exit {r.returncode}, {r.stderr.strip()}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
