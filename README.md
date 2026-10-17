<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:58A6FF,100:A371F7&height=170&section=header&text=Nyx&fontSize=72&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=A%20small%20systems%20language%20that%20compiles%20to%20C&descAlignY=62&descSize=17" width="100%" />

<img src="https://img.shields.io/badge/version-0.1.0-A371F7?style=for-the-badge&labelColor=161b22" />
<img src="https://img.shields.io/badge/compiles%20to-C99-58A6FF?style=for-the-badge&labelColor=161b22" />
<img src="https://img.shields.io/badge/tests-23%20passing-3FB950?style=for-the-badge&labelColor=161b22" />
<img src="https://img.shields.io/badge/license-MIT-c9d1d9?style=for-the-badge&labelColor=161b22" />

</div>

<br/>

Nyx is a statically typed, low level programming language built for speed. It compiles to plain C and then to a native executable, so your code runs at C speed with a cleaner syntax, strict types and safety checks the C compiler would never give you.

The long term goal is a language for games and AI workloads, with control down to the bit and tight integration with AI tooling. Version 0.1 is the foundation.

```rust
struct Vec2 {
    x: f64,
    y: f64,
}

fn add(a: Vec2, b: Vec2) -> Vec2 {
    return Vec2 { x: a.x + b.x, y: a.y + b.y }
}

fn main() {
    var pos = Vec2 { x: 0.0, y: 10.0 }
    let vel = Vec2 { x: 2.0, y: -1.0 }
    for frame in 0..3 {
        pos = add(pos, vel)
        print("frame", frame, "x", pos.x, "y", pos.y)
    }
}
```

<br/>

## ◆ Why Nyx

<table>
<tr>
<td width="50%" valign="top">

**⚡ Native speed**<br/>
Nyx compiles through C with full optimizations. Recursive `fib(35)` runs about **60x faster** than the same code in Python.

**🧱 Bit level control**<br/>
Fixed width integers from `i8` to `u64`, hex and binary literals, and the full set of bitwise operators.

**🔒 Strict by default**<br/>
Variables are immutable unless declared with `var`. No silent conversions between types. Missing returns are compile errors.

</td>
<td width="50%" valign="top">

**🛡️ Safe arrays**<br/>
Every array access is bounds checked. An out of range index stops the program with a clear message instead of corrupting memory.

**🔌 Direct C interop**<br/>
Declare any C function with `extern fn` and call it. The whole C ecosystem is available from day one.

**💬 Clear errors**<br/>
Errors point at the exact line and column and tell you how to fix the problem.

</td>
</tr>
</table>

```
speed.nyx:3:5: error: cannot assign to 'speed', declare it with 'var' to make it mutable
     3 |     speed = 20
       |     ^
```

<br/>

## ◆ Getting Started

Nyx needs **Python 3.8+** and a C compiler (**gcc** or **clang**).

```bash
git clone https://github.com/Samuelpedroso-dev/nyx.git
cd nyx
python3 nyx.py run examples/hello.nyx
```

| Command | What it does |
|:--|:--|
| `python3 nyx.py run file.nyx` | Compile and run |
| `python3 nyx.py build file.nyx -o app` | Build a native executable |
| `python3 nyx.py emit file.nyx` | Print the generated C code |
| `python3 nyx.py check file.nyx` | Type check without building |

Add `-O0`, `-O1`, `-O2` or `-O3` to choose the optimization level. The default is `-O2`.

<br/>

## ◆ Language Tour

**Variables.** `let` is immutable, `var` is mutable. Types are inferred or written explicitly.

```rust
let name = "Nyx"          // str
let count: i32 = 10       // explicit type
var score = 0             // i64 by default
score += 5
```

**Types.** `i8` `i16` `i32` `i64` `u8` `u16` `u32` `u64` `f32` `f64` `bool` `str`, fixed size arrays `[T; N]` and structs. Convert between numeric types with `as`.

```rust
let ratio = count as f64 / 3.0
let mask: u8 = 0b1010_0101
let big: u64 = 0xFFFF_FFFF
```

**Functions.** Can be called before they are defined.

```rust
fn clamp(x: i64, lo: i64, hi: i64) -> i64 {
    if x < lo { return lo }
    if x > hi { return hi }
    return x
}
```

**Control flow.** `if` and `else if`, `while`, ranges with `for`, `break` and `continue`.

```rust
for i in 0..10 {
    if i % 2 == 0 { continue }
    print(i)
}
```

**Arrays.** Fixed size, passed by value, bounds checked.

```rust
var grid = [0; 64]        // 64 zeros
grid[3] = 7
print(len(grid))          // 64
```

**C interop.**

```rust
extern fn sqrt(x: f64) -> f64

fn main() {
    print(sqrt(2.0))
}
```

<br/>

## ◆ Examples

| File | Shows |
|:--|:--|
| [`hello.nyx`](examples/hello.nyx) | The first program |
| [`fib.nyx`](examples/fib.nyx) | Recursion and loops |
| [`bits.nyx`](examples/bits.nyx) | Popcount, bit reversal, RGBA packing |
| [`particles.nyx`](examples/particles.nyx) | Structs and a game style physics step |
| [`sieve.nyx`](examples/sieve.nyx) | Arrays and the Sieve of Eratosthenes |
| [`math.nyx`](examples/math.nyx) | Calling C math functions |

<br/>

## ◆ How It Works

```
source.nyx ──► lexer ──► parser ──► type checker ──► C code ──► gcc / clang ──► native binary
```

The whole compiler lives in [`nyx.py`](nyx.py), about 1300 lines of Python with no dependencies. Each stage is a small, readable class, which makes the project a good place to learn how compilers work.

<br/>

## ◆ Tests

```bash
python3 tests/run_tests.py
```

The suite runs every example against its expected output, checks that 16 kinds of invalid programs are rejected with the right message, and verifies the runtime bounds check.

<br/>

## ◆ Benchmark

Recursive `fib(35)`, median of several runs, Intel Xeon 2.1 GHz.

| Language | Time |
|:--|--:|
| **Nyx 0.1** (gcc -O2) | **0.02 s** |
| Python 3.13 | 1.19 s |

This measures function call speed. Results vary with the machine and the C compiler.

<br/>

## ◆ Roadmap

<table>
<tr><td>◆</td><td>Dynamic arrays, slices and a standard library</td></tr>
<tr><td>◆</td><td>Generics and methods on structs</td></tr>
<tr><td>◆</td><td>SIMD types for game and AI math</td></tr>
<tr><td>◆</td><td>Direct native code generation through LLVM</td></tr>
<tr><td>◆</td><td>AI assisted tooling built into the compiler</td></tr>
<tr><td>◆</td><td>Self hosting, the Nyx compiler written in Nyx</td></tr>
</table>

<br/>

## ◆ Status

Nyx 0.1 is an early release. The core language works and is tested, but there is no heap allocation, no strings beyond literals and no modules yet. Expect breaking changes before 1.0. Issues and ideas are welcome.

<br/>

## ◆ Author

Created by **[Samuel Pedroso](https://github.com/Samuelpedroso-dev)**, Brazil. Released under the MIT license.

If Nyx interests you, a ⭐ helps the project grow.

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:58A6FF,100:A371F7&height=90&section=footer" width="100%" />
