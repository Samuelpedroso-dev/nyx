<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:58A6FF,100:A371F7&height=170&section=header&text=Nyx&fontSize=72&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=A%20fast%2C%20strict%20systems%20language%20that%20compiles%20to%20C&descAlignY=62&descSize=17" width="100%" />

<img src="https://img.shields.io/badge/version-0.2.0-A371F7?style=for-the-badge&labelColor=161b22" />
<img src="https://img.shields.io/badge/compiles%20to-C11-58A6FF?style=for-the-badge&labelColor=161b22" />
<img src="https://img.shields.io/badge/tests-40%20passing-3FB950?style=for-the-badge&labelColor=161b22" />
<img src="https://img.shields.io/badge/license-MIT-c9d1d9?style=for-the-badge&labelColor=161b22" />

</div>

<br/>

Nyx is a statically typed programming language built for speed. It compiles to plain C and then to a native executable, so your code runs at C speed with a clean syntax, strict types and safety checks the C compiler would never give you.

The long term goal is a language for games and AI workloads, with control down to the bit and tight integration with AI tooling.

```rust
import "random"

struct Fighter {
    name: str,
    hp: i64,
}

impl Fighter {
    fn take(var self, damage: i64) {
        self.hp -= damage
    }
}

fn main() {
    var rng = Rng.new(42)
    var party: [Fighter] = [Fighter { name: "Nyx", hp: 100 }, Fighter { name: "Vega", hp: 80 }]
    party.push(Fighter { name: "Orion", hp: 120 })

    for i in 0..party.len() {
        party[i].take(rng.range(5, 20))
    }
    for f in party {
        print(f.name + " has " + str(f.hp) + " hp")
    }
}
```

<br/>

## ◆ Why Nyx

<table>
<tr>
<td width="50%" valign="top">

**⚡ Native speed**<br/>
Nyx compiles through C with full optimizations. Recursive `fib(35)` runs about **60x faster** than Python and list processing about **28x faster**.

**🧱 Bit level control**<br/>
Fixed width integers from `i8` to `u64`, hex and binary literals, and the full set of bitwise operators.

**🔒 Strict by default**<br/>
Variables are immutable unless declared with `var`. No silent conversions. Methods must say `var self` to change their struct.

**🧩 Modern building blocks**<br/>
Real strings, growable lists, structs with methods, `for each` loops and modules.

</td>
<td width="50%" valign="top">

**🛡️ Safe by design**<br/>
Every array, list and string access is bounds checked. Popping an empty list or slicing past the end stops with a clear message instead of corrupting memory.

**🔌 Direct C interop**<br/>
Declare any C function with `extern fn` and call it. The whole C ecosystem is available from day one.

**📦 Standard library**<br/>
`math`, `random` and `strings` modules, written in Nyx itself.

**💬 Clear errors**<br/>
Errors point at the exact file, line and column and tell you how to fix the problem.

</td>
</tr>
</table>

```
game.nyx:3:5: error: cannot modify 'speed', declare it with 'var' to make it mutable
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

**Types.** `i8` `i16` `i32` `i64` `u8` `u16` `u32` `u64` `f32` `f64` `bool` `str`, fixed arrays `[T; N]`, growable lists `[T]` and structs. Convert numbers with `as`, turn anything into text with `str(x)`.

```rust
let ratio = count as f64 / 3.0
let mask: u8 = 0b1010_0101
let label = "score " + str(score)
```

**Strings.** Concatenate with `+`, compare with `==` and `<`, index bytes, slice and search.

```rust
let msg = "Hello, " + name
print(msg.len(), msg.contains("Nyx"), msg.find(","), msg.substr(0, 5))
for c in "abc" { print(chr(c)) }
```

**Lists.** Grow with `push`, shrink with `pop` and `remove`, iterate with `for`.

```rust
var xs: [i64] = []
xs.push(10)
xs.push(20)
xs[0] = 5
let last = xs.pop()
for x in xs { print(x) }
print(xs)                 // [5]
```

**Structs and methods.** `self` reads a copy, `var self` changes the original, functions without `self` are called on the type.

```rust
struct Vec2 { x: f64, y: f64 }

impl Vec2 {
    fn new(x: f64, y: f64) -> Vec2 { return Vec2 { x: x, y: y } }
    fn length(self) -> f64 { return sqrt(self.x * self.x + self.y * self.y) }
    fn scale(var self, k: f64) { self.x *= k
                                 self.y *= k }
}

var v = Vec2.new(3.0, 4.0)
v.scale(2.0)
print(v)                  // Vec2 { x: 6, y: 8 }
```

**Control flow.** `if` and `else if`, `while`, ranges and `for each`, `break` and `continue`.

```rust
for i in 0..10 {
    if i % 2 == 0 { continue }
    print(i)
}
```

**Modules.** Import the standard library by name or your own files by path.

```rust
import "math"            // lib/math.nyx
import "utils.nyx"       // next to this file
```

**C interop.**

```rust
extern fn puts(s: str) -> i32
```

<br/>

## ◆ Standard Library

| Module | Contents |
|:--|:--|
| [`math`](lib/math.nyx) | `sqrt` `sin` `cos` `pow` `floor` and friends, `pi` `min` `max` `clamp` `lerp`, integer versions |
| [`random`](lib/random.nyx) | `Rng` with `next` `range` `float` `chance`, deterministic for a given seed |
| [`strings`](lib/strings.nyx) | `split` `join` `trim` `repeat` `to_upper` `to_lower` `starts_with` `ends_with` |

Built in everywhere are `print` `str` `len` `push` `pop` `remove` `clear` `substr` `contains` `find` `chr` `read_line` `parse_int` `parse_float` `time_ms` and `exit`.

<br/>

## ◆ Examples

| File | Shows |
|:--|:--|
| [`hello.nyx`](examples/hello.nyx) | The first program |
| [`arena.nyx`](examples/arena.nyx) | A small game simulation using everything together |
| [`methods.nyx`](examples/methods.nyx) | Structs with methods and `var self` |
| [`lists.nyx`](examples/lists.nyx) | Growable lists of numbers and structs |
| [`strings.nyx`](examples/strings.nyx) | String building and the strings module |
| [`bits.nyx`](examples/bits.nyx) | Popcount, bit reversal, RGBA packing |
| [`particles.nyx`](examples/particles.nyx) | A game style physics step |
| [`sieve.nyx`](examples/sieve.nyx) | Fixed arrays and the Sieve of Eratosthenes |
| [`fib.nyx`](examples/fib.nyx) | Recursion and loops |
| [`math.nyx`](examples/math.nyx) | Calling C functions directly |

<br/>

## ◆ How It Works

```
source.nyx ──► lexer ──► parser ──► type checker ──► C code ──► gcc / clang ──► native binary
```

The whole compiler lives in [`nyx.py`](nyx.py), under 2000 lines of Python with no dependencies. Each stage is a small, readable class, which makes the project a good place to learn how compilers work.

<br/>

## ◆ Tests

```bash
python3 tests/run_tests.py
```

The suite runs all 10 examples against their expected output, checks that 26 kinds of invalid programs are rejected with the right message, and verifies 4 runtime safety checks. The generated C is also checked with AddressSanitizer and UndefinedBehaviorSanitizer.

<br/>

## ◆ Benchmark

Median of several runs on an Intel Xeon 2.1 GHz, gcc -O2.

| Task | Nyx 0.2 | Python 3.13 | Speedup |
|:--|--:|--:|--:|
| Recursive `fib(35)` | 0.02 s | 1.19 s | ~60x |
| Push and sum 10 million list items | 0.06 s | 1.65 s | ~28x |

Results vary with the machine and the C compiler.

<br/>

## ◆ Roadmap

<table>
<tr><td>◆</td><td>Automatic memory management, so long running programs release what they no longer use</td></tr>
<tr><td>◆</td><td>Hash maps and generics</td></tr>
<tr><td>◆</td><td>Enums and pattern matching</td></tr>
<tr><td>◆</td><td>SIMD types for game and AI math</td></tr>
<tr><td>◆</td><td>Direct native code generation through LLVM</td></tr>
<tr><td>◆</td><td>AI assisted tooling built into the compiler</td></tr>
<tr><td>◆</td><td>Self hosting, the Nyx compiler written in Nyx</td></tr>
</table>

<br/>

## ◆ Status

Nyx 0.2 is an early release. The core language is complete enough for tools, simulations and small games, and every feature above is tested.

One important limit today: memory used by strings and lists is only released when the program exits. That is fine for scripts, tools and simulations, but a program that builds new strings every frame for hours will keep growing. Automatic memory management is the next milestone. Expect breaking changes before 1.0. Issues and ideas are welcome.

<br/>

## ◆ Author

Created by **[Samuel Pedroso](https://github.com/Samuelpedroso-dev)**, Brazil. Released under the MIT license.

If Nyx interests you, a ⭐ helps the project grow.

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:58A6FF,100:A371F7&height=90&section=footer" width="100%" />
