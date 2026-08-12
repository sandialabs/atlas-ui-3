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

__all__ = ["MAX_EXPRESSION_LENGTH", "UnsafeExpressionError", "safe_eval_math"]

# Bound on input length. Kept as a named constant so the tool docstring, the
# error path, and the tests all agree on one value.
MAX_EXPRESSION_LENGTH = 200

# Guard against expressions that are cheap to write and ruinous to evaluate:
# ``9**9**9`` is four tokens and will not finish. Applied to every ``**``
# whose operands are literal ints, which is the only form that can be checked
# before evaluation.
MAX_LITERAL_EXPONENT = 1000


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
_ALLOWED_CONSTANTS = (int, float, complex, bool)


def _describe(node: ast.AST) -> str:
    """Human-readable name for a rejected node, for the error message."""
    return type(node).__name__


def _check_pow_bounds(node: ast.BinOp) -> None:
    """Reject literal exponents large enough to hang the process."""
    exponent = node.right
    if isinstance(exponent, ast.Constant) and isinstance(exponent.value, int):
        if abs(exponent.value) > MAX_LITERAL_EXPONENT:
            raise UnsafeExpressionError(
                f"Exponent too large (limit {MAX_LITERAL_EXPONENT})"
            )


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
        if isinstance(node.op, ast.Pow):
            _check_pow_bounds(node)
        return handler(_evaluate(node.left, names), _evaluate(node.right, names))

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
    raise UnsafeExpressionError(f"Unsupported expression element: {_describe(node)}")


def safe_eval_math(expression: str, names: Mapping[str, Any]) -> Any:
    """Evaluate ``expression`` using only arithmetic and functions in ``names``.

    Args:
        expression: The expression source. Must be at most
            :data:`MAX_EXPRESSION_LENGTH` characters.
        names: Mapping of the constants and callables the expression may use.
            Nothing outside this mapping is reachable.

    Returns:
        The value of the expression.

    Raises:
        UnsafeExpressionError: If the expression is too long, does not parse,
            or contains any construct outside plain arithmetic.
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
