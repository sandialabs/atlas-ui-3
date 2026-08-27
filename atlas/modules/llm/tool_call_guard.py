"""Guard for tool calls a model could not finish sending.

Separated from ``models.py`` -- which holds data models -- because this is
policy: what counts as a usable tool call, what may be repaired, and what must
be refused.

The rule the whole module follows: a repair may complete the *shape* of an
object, never the *content* of a value. Anything else trades a visible error for
a silently wrong action, which is the failure this guard exists to prevent.
"""

import json
from typing import Any, Dict, List, Optional, Tuple


def tool_call_function_field(tool_call: Any, field: str, default: Any = "") -> Any:
    """Read ``function.<field>`` from a tool call in either shape.

    Tool calls arrive as litellm pydantic models (attribute access) from the
    provider and as plain dicts from history round-trips and tests. The value is
    returned as the provider sent it -- usually a string, but ``arguments`` can
    arrive already decoded as a dict or list -- so the return type is not
    narrowed to ``str``; callers that need one coerce it themselves.
    """
    function = None
    if isinstance(tool_call, dict):
        function = tool_call.get("function")
    else:
        function = getattr(tool_call, "function", None)
    if function is None:
        return default
    if isinstance(function, dict):
        value = function.get(field)
    else:
        value = getattr(function, field, None)
    return default if value is None else value


def tool_call_arguments_are_valid_json(tool_call: Any) -> bool:
    """Whether a tool call's ``arguments`` can be parsed back out of history.

    Empty arguments count as valid: models legitimately emit ``""`` for
    no-argument tools, and providers accept it. Only a non-empty string that
    fails to parse is a real defect -- the signature of an output truncated
    partway through the JSON object.
    """
    arguments = tool_call_function_field(tool_call, "arguments", "")
    if not isinstance(arguments, str):
        # Some providers hand back an already-decoded object; nothing to parse.
        return arguments is None or isinstance(arguments, (dict, list))
    if not arguments.strip():
        return True
    try:
        json.loads(arguments)
    except ValueError:
        return False
    return True


def repair_structural_json(
    raw: str,
    allow_closing_brace: bool = True,
) -> Optional[Dict[str, Any]]:
    """Repair JSON that is only *structurally* incomplete, or return ``None``.

    Scope is deliberately narrow: balance missing braces, and nothing else. A
    repair may complete the shape of an object, never the content of a value.

    Some models routinely emit tool arguments without the enclosing braces
    (``"q": "hi"`` rather than ``{"q": "hi"}``). That is a well-formed intent in
    a sloppy envelope, and it was accepted long before the truncation guard
    existed, so the guard repairs it rather than failing the turn over it.

    Closing an *open string* is explicitly not done. It is safe only when the
    cut-off value happens to still be meaningful, and for the failure this guard
    exists for it was not: ``{"filename": "1787784579_..._topic`` would repair
    into a different, valid-looking filename that the tool then executes against
    confidently. Guessing at a value the model never finished sending trades a
    visible error for a silent wrong answer.
    """
    text = raw.strip()
    # Text that opens with a brace but never closes it is the signature of a
    # cut-off response: `{"path": "/data", "recursive": true`. Supplying the
    # closing brace there produces a complete-looking call whose remaining keys
    # were never sent, so callers that cannot rule truncation out turn it off.
    # Text missing the opening brace is a different animal -- a sloppy envelope
    # around a complete intent (`"q": "hi"`) -- and is always repaired.
    looks_cut_off = text.startswith("{") and not text.endswith("}")
    if looks_cut_off and not allow_closing_brace:
        return None
    if not text.startswith("{"):
        text = "{" + text
    if not text.endswith("}"):
        text = text + "}"
    try:
        result = json.loads(text)
    except ValueError:
        return None
    return result if isinstance(result, dict) else None


def tool_call_arguments_are_empty(tool_call: Any) -> bool:
    """Whether a tool call carries no arguments at all."""
    arguments = tool_call_function_field(tool_call, "arguments", "")
    if arguments is None:
        return True
    if isinstance(arguments, str):
        return not arguments.strip()
    return not arguments


# Reasons that mean the model finished saying what it meant to say. Anything
# else -- an output limit, a content filter, an unrecognized provider string --
# means the response may stop mid-thought, so a structural repair on the last
# call would be completing a shape that is not known to be complete.
CLEAN_FINISH_REASONS = frozenset({"stop", "tool_calls", "function_call"})


def response_was_cut_off(finish_reason: Optional[str]) -> bool:
    """Whether ``finish_reason`` indicates the response may be incomplete.

    ``None`` counts as clean: many providers omit the field on the chunks Atlas
    accumulates, and treating a missing value as a truncation would drop
    legitimate no-argument calls across the board.
    """
    if finish_reason is None:
        return False
    return finish_reason not in CLEAN_FINISH_REASONS


def partition_tool_calls_by_json_validity(
    tool_calls: Optional[List[Any]],
    finish_reason: Optional[str] = None,
) -> Tuple[List[Any], List[Any]]:
    """Split tool calls into ``(valid, malformed)`` by argument parseability.

    Malformed calls must be dropped rather than executed or persisted: their
    arguments are unparseable, so the call cannot be run faithfully, and
    re-sending them makes the provider reject every later turn in the
    conversation (see ``LLMMalformedToolCallError``).

    Everything extra hangs off ``finish_reason``, which has three meaningful
    states rather than two, and the **last** call is the only one they apply to
    -- it is the only one a cut-off can have reached, so a genuine no-argument
    call earlier in the same response is always honoured:

    * **clean** (``stop``/``tool_calls``): the model said everything it meant to.
      Repair anything repairable.
    * **cut off** (``length``, ``content_filter``, or a reason we do not know):
      the response may stop mid-thought. Empty arguments are treated as a call
      truncated before its first delta rather than a no-argument call.
    * **absent** (``None``): providers routinely omit it on the chunks Atlas
      accumulates. Treating that as a cut-off would drop legitimate no-argument
      calls across the board, so empty arguments are still honoured -- but it is
      not positive evidence of completeness either, so the closing-brace repair
      below stays off.

    Supplying a missing *closing* brace is what turns
    ``{"path": "/data", "recursive": true`` into a complete-looking call whose
    remaining keys were never sent. That half of the repair therefore requires
    positive evidence the response finished; a missing *opening* brace is only a
    sloppy envelope and is always repaired.
    """
    calls = [tool_call for tool_call in (tool_calls or []) if tool_call is not None]
    known_complete = finish_reason in CLEAN_FINISH_REASONS
    cut_off = response_was_cut_off(finish_reason)
    valid: List[Any] = []
    malformed: List[Any] = []
    for index, tool_call in enumerate(calls):
        is_last = index == len(calls) - 1
        ok = tool_call_arguments_are_valid_json(tool_call)
        if not ok:
            # Writing the repair back is what keeps history parseable on the
            # next request, which repairing downstream in the executor never did.
            ok = _repair_arguments_in_place(
                tool_call,
                # Earlier calls completed before anything was cut off, so they
                # get the full repair regardless of how the response ended.
                allow_closing_brace=known_complete or not is_last,
            )
        if ok and is_last and cut_off and tool_call_arguments_are_empty(tool_call):
            # Cut off before the first argument delta: parses fine as "no
            # arguments" and would execute with {}.
            ok = False
        (valid if ok else malformed).append(tool_call)
    return valid, malformed


def dropped_call_warning(names: List[str], truncated: bool) -> str:
    """User-facing note that tool calls were discarded but the turn continued.

    Defined once and shared by both mode runners: the copy has to stay honest
    about *why* the calls were dropped, and about what has and has not happened
    yet when it is published.
    """
    listed = ", ".join(f"'{name}'" for name in names)
    many = len(names) > 1
    noun = "calls" if many else "call"
    subject = "they" if many else "it"
    reason = (
        f"{subject} ran out of room before finishing"
        if truncated
        else f"{subject} could not be read as valid JSON"
    )
    return (
        f"The model's {noun} to {listed} could not be run because {reason}. The "
        "rest of this step is continuing with the tool calls that arrived intact."
    )


def _repair_arguments_in_place(tool_call: Any, allow_closing_brace: bool = True) -> bool:
    """Try a structural repair of ``arguments``; write it back and report success.

    The repaired string replaces the original so the assistant message written
    to history carries JSON the provider can re-parse on every later turn.
    """
    arguments = tool_call_function_field(tool_call, "arguments", "")
    if not isinstance(arguments, str) or not arguments.strip():
        return False
    repaired = repair_structural_json(arguments, allow_closing_brace=allow_closing_brace)
    if repaired is None:
        return False
    encoded = json.dumps(repaired)
    function = (
        tool_call.get("function") if isinstance(tool_call, dict)
        else getattr(tool_call, "function", None)
    )
    if function is None:
        return False
    try:
        if isinstance(function, dict):
            function["arguments"] = encoded
        else:
            function.arguments = encoded
    except (AttributeError, TypeError, ValueError):
        # A frozen provider model cannot be rewritten; refusing the call is
        # safer than letting the unrepaired string reach history.
        return False
    return True
