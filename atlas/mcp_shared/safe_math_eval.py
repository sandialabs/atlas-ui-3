"""AST-based evaluator for arithmetic expressions supplied by tool callers.

``eval`` cannot be made safe by emptying ``__builtins__``. Python's object
model reaches the class hierarchy through ordinary attribute access, and any
module already imported by the interpreter -- ``os`` among them -- is a couple
of lookups away from an expression that never names it::

    [c for c in ().__class__.__mro__[1].__subclasses__()
       if c.__name__ == '_wrap_close'][0].__init__.__globals__['system']('id')

Every escape of that shape needs attribute access, subscripting, or a
comprehension. So instead of blocking payloads, this module parses the
expression and walks the tree, refusing any node that is not part of plain
arithmetic. ``Attribute``, ``Subscript``, comprehensions, lambdas, and
starred/keyword call arguments are simply not in the allowed set, which makes
the escape unrepresentable rather than merely unlikely.

Call resolution is deliberately narrow: the callee must be a bare ``Name``
that is present in the caller-supplied function table. There is no path by
which evaluation can reach a name the caller did not put there.
"""

from __future__ import annotations

import ast
from typing import Any, Callable, Mapping

__all__ = [
    "MAX_EXPONENT",
    "MAX_EXPRESSION_LENGTH",
    "MAX_RESULT_BITS",
    "MAX_SEQUENCE_ELEMENTS",
    "UnsafeExpressionError",
    "guard_exponent",
    "guard_operand_size",
    "safe_eval_math",
    "total_elements",
]

# Bound on input length. Kept as a named constant so the tool docstring, the
# error path, and the tests all agree on one value.
MAX_EXPRESSION_LENGTH = 200

# Guards against expressions that are cheap to write and ruinous to evaluate.
# ``9**9**9`` is five tokens and will not finish; neither will ``1 << 9**9``.
#
# Both bounds are applied to *evaluated* operands, not to literals in the
# source. Checking the parse tree is not enough: in ``9**9**9`` the outer
# exponent is a BinOp rather than a constant, so a literals-only check reads
# straight past it.
#
# MAX_EXPONENT catches the obvious case with a clear message. MAX_RESULT_BITS
# catches the chained case -- ``(10**1000)**1000`` keeps every individual
# exponent under the cap while the result grows without bound -- by bounding
# the size of the number the operation would have to build.
MAX_EXPONENT = 1000
MAX_RESULT_BITS = 100_000

# Sequence repetition is a third route to the same exhaustion: `(1,)*(10**8)`
# is twelve characters and allocates hundreds of megabytes. Tuples exist here
# only so `divmod` results and `sum((1, 2))` read naturally, so the cap on
# elements can be small.
MAX_SEQUENCE_ELEMENTS = 1000


class UnsafeExpressionError(ValueError):
    """Raised when an expression is malformed or uses a disallowed construct."""


# Binary, unary, and comparison operators that carry no side effects. Bitwise
# operators are included because they are ordinary integer arithmetic; the
# boolean operators (``and``/``or``) and ``in``/``is`` are not, because they
# invite type coercion questions the calculator has no reason to answer.
_BIN_OPS: Mapping[type, Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
    ast.BitAnd: lambda a, b: a & b,
    ast.BitOr: lambda a, b: a | b,
    ast.BitXor: lambda a, b: a ^ b,
    ast.LShift: lambda a, b: a << b,
    ast.RShift: lambda a, b: a >> b,
}

_UNARY_OPS: Mapping[type, Callable[[Any], Any]] = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
    ast.Invert: lambda a: ~a,
}

_COMPARE_OPS: Mapping[type, Callable[[Any, Any], Any]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}

# Literal types an expression may contain. Strings are excluded: the
# calculator has no use for them, and allowing them widens the surface of
# every function in the table that would otherwise reject a non-number.
#
# complex is excluded too. It evaluates fine but is not JSON-encodable, so a
# literal like `2j` produced a success payload that then raised TypeError in
# the serialization layer -- outside the caller's error contract entirely.
_ALLOWED_CONSTANTS = (int, float, bool)


# Caller-facing wording for the node types actually reachable from a
# mathematical expression. The message is the only feedback an LLM caller
# gets, so "Unsupported expression element: List" -- naming a CPython AST
# class -- turns a fixable mistake into a dead end, where "use parentheses:
# sum((1, 2, 3))" turns it into a successful retry.
_NODE_GUIDANCE = {
    "List": "list syntax is not supported; use parentheses, e.g. sum((1, 2, 3))",
    "Dict": "dict syntax is not supported",
    "Set": "set syntax is not supported; use parentheses for a group of numbers",
    "Attribute": "attribute access (x.y) is not supported",
    "Subscript": "indexing (x[0]) is not supported",
    "ListComp": "comprehensions are not supported",
    "SetComp": "comprehensions are not supported",
    "DictComp": "comprehensions are not supported",
    "GeneratorExp": "generator expressions are not supported",
    "Lambda": "lambdas are not supported",
    "IfExp": "conditional expressions (a if b else c) are not supported",
    "BoolOp": "and/or are not supported; this evaluates numbers, not logic",
    "JoinedStr": "f-strings are not supported",
    "FormattedValue": "f-strings are not supported",
    "NamedExpr": "assignment (:=) is not supported",
    "Await": "await is not supported",
    "Starred": "argument unpacking (*args) is not supported",
    "Slice": "slicing is not supported",
}


def _describe(node: ast.AST) -> str:
    """Caller-facing description of a rejected node.

    Falls back to the AST class name for node types not worth enumerating --
    those are unreachable from anything resembling a maths expression, so the
    precise wording matters less than not crashing on them.
    """
    name = type(node).__name__
    guidance = _NODE_GUIDANCE.get(name)
    return guidance if guidance else name


def _bit_length(value: Any) -> int:
    """Approximate size in bits of an evaluated operand, or 0 if not an int."""
    return value.bit_length() if isinstance(value, int) else 0


def guard_exponent(base: Any, exponent: Any) -> None:
    """Reject a power operation whose result would be ruinously large.

    Exported so that callers exposing ``pow`` in their name table can apply
    the same bound -- otherwise ``pow(9, 999999)`` walks straight past the
    guard that ``9 ** 999999`` trips.

    Float operands are not checked: they overflow to ``inf`` in constant time
    and cannot be used to exhaust memory.
    """
    if not isinstance(exponent, int) or isinstance(exponent, bool):
        return
    if abs(exponent) > MAX_EXPONENT:
        raise UnsafeExpressionError(f"Exponent too large (limit {MAX_EXPONENT})")
    if _bit_length(base) * abs(exponent) > MAX_RESULT_BITS:
        raise UnsafeExpressionError("Result would be too large to compute")


def guard_operand_size(value: Any, limit: int = MAX_EXPONENT) -> Any:
    """Reject an argument large enough to make a growth function hang.

    ``factorial``, ``comb``, and ``perm`` are as effective as ``**`` for
    burning CPU, and they are ordinary calls rather than operators, so the
    evaluator cannot see them. Callers wrap those functions with this.

    Floats are bounded as well as ints. Some of these functions historically
    accepted integral floats, and ``round``'s ndigits does today, so an
    int-only check would leave a hole that depends on the Python version.

    Returns the value so wrappers can write ``func(guard_operand_size(n))``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # NaN compares false against everything, so test for the safe case.
        if not abs(value) <= limit:
            raise UnsafeExpressionError(f"Argument too large (limit {limit})")
    return value


def total_elements(value: Any, limit: int = MAX_SEQUENCE_ELEMENTS) -> int:
    """Count elements in a possibly-nested tuple, stopping once past ``limit``.

    Counting only the top level is not enough. Repetition composes: each
    ``* n`` multiplies whatever the operand already contains, so
    ``(((1,)*1000,)*1000,)*1000`` keeps every individual operand at one
    top-level element while building 10**9 leaves, in 25 characters.

    Returns ``limit + 1`` as soon as the budget is exceeded, so a structure
    that is already enormous costs no more to reject than a small one.
    """
    if not isinstance(value, tuple):
        return 1

    counted = 0
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, tuple):
            counted += len(item)
            if counted > limit:
                return limit + 1
            pending.extend(item)
        if counted > limit:
            return limit + 1
    return counted


def _guard_repetition(left: Any, right: Any) -> None:
    """Reject sequence repetition that would allocate an enormous tuple.

    ``ast.Mult`` is ordinary arithmetic for numbers, but with a sequence on
    either side Python reinterprets it as repetition, so the numeric size
    guards never see it.
    """
    for sequence, count in ((left, right), (right, left)):
        if isinstance(sequence, tuple) and isinstance(count, int) and not isinstance(count, bool):
            # Size the operand recursively: nesting is how repetition evades a
            # top-level count.
            existing = total_elements(sequence)
            if existing * abs(count) > MAX_SEQUENCE_ELEMENTS:
                raise UnsafeExpressionError(
                    f"Sequence too large (limit {MAX_SEQUENCE_ELEMENTS} elements)"
                )


def _guard_shift(left: Any, right: Any) -> None:
    """Reject a left shift whose result would be ruinously large."""
    if not isinstance(right, int) or isinstance(right, bool):
        return
    if right < 0:
        return  # Python raises ValueError itself; let it surface normally.
    if _bit_length(left) + right > MAX_RESULT_BITS:
        raise UnsafeExpressionError("Result would be too large to compute")


def _evaluate(node: ast.AST, names: Mapping[str, Any]) -> Any:
    """Recursively evaluate an allowed node, rejecting everything else."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, names)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, _ALLOWED_CONSTANTS):
            return node.value
        raise UnsafeExpressionError(
            f"Unsupported literal of type {type(node.value).__name__}"
        )

    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Load) and node.id in names:
            return names[node.id]
        raise UnsafeExpressionError(f"Unknown name: {node.id}")

    if isinstance(node, ast.BinOp):
        handler = _BIN_OPS.get(type(node.op))
        if handler is None:
            raise UnsafeExpressionError(f"Unsupported operator: {_describe(node.op)}")
        # Both operands are evaluated before the size guards run, so a
        # computed exponent such as the outer 9 in ``9**9**9`` is checked by
        # value rather than by whether it happened to be written as a literal.
        left = _evaluate(node.left, names)
        right = _evaluate(node.right, names)
        if isinstance(node.op, ast.Pow):
            guard_exponent(left, right)
        elif isinstance(node.op, ast.LShift):
            _guard_shift(left, right)
        elif isinstance(node.op, ast.Mult):
            _guard_repetition(left, right)
        return handler(left, right)

    if isinstance(node, ast.UnaryOp):
        handler = _UNARY_OPS.get(type(node.op))
        if handler is None:
            raise UnsafeExpressionError(f"Unsupported operator: {_describe(node.op)}")
        return handler(_evaluate(node.operand, names))

    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, names)
        for op, comparator in zip(node.ops, node.comparators):
            handler = _COMPARE_OPS.get(type(op))
            if handler is None:
                raise UnsafeExpressionError(f"Unsupported comparison: {_describe(op)}")
            right = _evaluate(comparator, names)
            if not handler(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        # The callee must be a bare name from the caller's table. Rejecting
        # anything else is what keeps `().__class__` and friends unreachable:
        # there is no expression that yields a callable this evaluator did
        # not already hold.
        if not isinstance(node.func, ast.Name):
            raise UnsafeExpressionError("Only direct calls to allowed functions")
        func = names.get(node.func.id)
        if func is None or not callable(func):
            raise UnsafeExpressionError(f"Unknown function: {node.func.id}")
        if node.keywords:
            raise UnsafeExpressionError("Keyword arguments are not supported")
        args = []
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                raise UnsafeExpressionError("Argument unpacking is not supported")
            args.append(_evaluate(arg, names))
        return func(*args)

    if isinstance(node, ast.Tuple):
        # Present only so `divmod` results and `max(1, 2)`-style grouping read
        # naturally; tuples cannot be indexed here because Subscript is denied.
        if not isinstance(node.ctx, ast.Load):
            raise UnsafeExpressionError("Unsupported tuple context")
        return tuple(_evaluate(element, names) for element in node.elts)

    # Everything not handled above -- Attribute, Subscript, comprehensions,
    # Lambda, IfExp, JoinedStr, Await, NamedExpr, and the rest -- lands here.
    raise UnsafeExpressionError(f"Unsupported expression: {_describe(node)}")


def safe_eval_math(expression: str, names: Mapping[str, Any]) -> Any:
    """Evaluate ``expression`` using only arithmetic and functions in ``names``.

    Args:
        expression: The expression source. Must be at most
            :data:`MAX_EXPRESSION_LENGTH` characters.
        names: Mapping of the constants and callables the expression may use.
            Nothing outside this mapping is reachable. Callables must be safe
            to invoke with arbitrary numeric arguments -- this function bounds
            the operators it evaluates itself, but it cannot know the cost of
            a function the caller supplied. Wrap growth functions such as
            ``factorial`` with :func:`guard_operand_size` and ``pow`` with
            :func:`guard_exponent` before passing them in.

    Returns:
        The value of the expression.

    Raises:
        UnsafeExpressionError: If the expression is too long, does not parse,
            contains any construct outside plain arithmetic, or would build a
            number too large to compute.
        Exception: Ordinary arithmetic errors from the operation itself --
            ZeroDivisionError, OverflowError, the ValueError ``sqrt(-1)``
            raises -- propagate unchanged. They are maths errors, not safety
            violations, and the caller reports them as such.
    """
    if not isinstance(expression, str):
        raise UnsafeExpressionError("Expression must be a string")

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise UnsafeExpressionError(
            f"Expression too long (limit {MAX_EXPRESSION_LENGTH} characters)"
        )

    if not expression.strip():
        raise UnsafeExpressionError("Expression is empty")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Could not parse expression: {exc.msg}") from exc

    return _evaluate(tree, names)
