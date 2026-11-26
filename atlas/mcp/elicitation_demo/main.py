#!/usr/bin/env python3
"""
Elicitation Demo MCP Server using FastMCP

This server demonstrates user elicitation capabilities - requesting
structured input from users during tool execution.

Supports:
- Scalar types (string, int, bool)
- Enum/constrained options
- Structured multi-field responses
- Multi-turn elicitation
- Several questions in one round trip

Every tool gathers input through ``UserInput`` (atlas/mcp_shared/user_input.py).
On MCP 2026-07-28 that means the multi round-trip pattern (SEP-2322): the tool
returns an ``InputRequiredResult`` and is called again with the answers. On an
older connection the same code falls back to ``ctx.elicit()``.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from fastmcp import Context
from mcp_types import InputRequiredResult

from atlas.mcp_shared.server_factory import create_stdio_server
from atlas.mcp_shared.user_input import UserInput

# Initialize the MCP server
mcp = create_stdio_server("Elicitation Demo")


@mcp.tool
async def get_user_name(ctx: Context) -> str | InputRequiredResult:
    """
    Simple elicitation example that asks for a string input.

    This tool demonstrates basic string elicitation - asking the user
    for their name and using it in the response.

    Returns:
        Greeting message with the user's name, or indication if not provided
    """
    form = UserInput(ctx)
    result = await form.ask("name", "What's your name?", str, title="Name")
    if result is None:
        return form.input_required()

    if result.action == "accept":
        return f"Hello, {result.data}! Nice to meet you."
    elif result.action == "decline":
        return "No name provided. That's okay!"
    else:  # cancel
        return "Operation cancelled."


@mcp.tool
async def pick_a_number(ctx: Context) -> str | InputRequiredResult:
    """
    Elicitation example that requests an integer input.

    This tool demonstrates numeric elicitation - asking the user
    for a number and performing a simple calculation.

    Returns:
        Information about the picked number, or indication if not provided
    """
    form = UserInput(ctx)
    result = await form.ask(
        "number", "Pick a number between 1 and 100!", int, title="Number"
    )
    if result is None:
        return form.input_required()

    if result.action == "accept":
        number = result.data
        doubled = number * 2
        return f"You picked {number}! Doubled, that's {doubled}."
    elif result.action == "decline":
        return "No number provided."
    else:  # cancel
        return "Operation cancelled."


@mcp.tool
async def confirm_action(ctx: Context) -> str | InputRequiredResult:
    """
    Elicitation example that requests boolean confirmation.

    This tool demonstrates boolean elicitation - asking the user
    to confirm or reject an action.

    Returns:
        Result of the confirmation decision
    """
    form = UserInput(ctx)
    result = await form.ask(
        "proceed", "Do you want to proceed with this action?", bool, title="Proceed"
    )
    if result is None:
        return form.input_required()

    if result.action == "accept":
        if result.data:
            return "Action confirmed! Proceeding..."
        else:
            return "Action not confirmed. Cancelled."
    elif result.action == "decline":
        return "No response provided."
    else:  # cancel
        return "Operation cancelled."


class Priority(Enum):
    """Priority levels for task creation."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@mcp.tool
async def set_priority(ctx: Context) -> str | InputRequiredResult:
    """
    Elicitation example using enum for constrained options.

    This tool demonstrates enum-based elicitation - asking the user
    to choose from a predefined set of priority levels.

    Returns:
        Confirmation of the selected priority level
    """
    form = UserInput(ctx)
    result = await form.ask("priority", "What priority level?", Priority)
    if result is None:
        return form.input_required()

    if result.action == "accept":
        return f"Priority set to: {result.data.value}"
    elif result.action == "decline":
        return "No priority set."
    else:  # cancel
        return "Operation cancelled."


@mcp.tool
async def choose_option(ctx: Context) -> str | InputRequiredResult:
    """
    Elicitation example using list of strings for simple options.

    This tool demonstrates string literal elicitation - asking the user
    to choose from a list of options provided as strings.

    Returns:
        Confirmation of the selected option
    """
    form = UserInput(ctx)
    result = await form.ask(
        "color",
        "Choose your favorite color:",
        ["red", "blue", "green", "yellow"],
        title="Color",
    )
    if result is None:
        return form.input_required()

    if result.action == "accept":
        return f"You chose: {result.data}"
    elif result.action == "decline":
        return "No color chosen."
    else:  # cancel
        return "Operation cancelled."


@mcp.tool
async def choose_toppings(ctx: Context) -> str | InputRequiredResult:
    """
    Elicitation example with a multi-select list and titled options.

    This tool demonstrates two newer schema shapes: a titled single choice
    (each option has a value and a display title) and a multi-select list.
    Both questions are asked in the same round trip, so the user answers
    them one after the other without the tool running in between.

    Returns:
        Summary of the chosen size and toppings
    """
    form = UserInput(ctx)
    size = await form.ask(
        "size",
        "What size pizza?",
        {"s": {"title": "Small (10 inch)"}, "m": {"title": "Medium (12 inch)"}, "l": {"title": "Large (14 inch)"}},
        title="Size",
    )
    toppings = await form.ask(
        "toppings",
        "Pick any toppings you like:",
        [["mushrooms", "peppers", "olives", "onions"]],
        title="Toppings",
    )
    if size is None or toppings is None:
        return form.input_required()

    if size.action != "accept" or toppings.action != "accept":
        return "Order cancelled."
    chosen = ", ".join(toppings.data) if toppings.data else "no toppings"
    return f"Ordered a size {size.data} pizza with {chosen}."


@dataclass
class TaskDetails:
    """Structured data for task creation."""
    title: str
    description: str
    priority: Literal["low", "medium", "high"]
    due_date: str


@mcp.tool
async def create_task(ctx: Context) -> str | InputRequiredResult:
    """
    Elicitation example with structured multi-field response.

    This tool demonstrates structured elicitation - asking the user
    to provide multiple fields at once using a dataclass.

    Returns:
        Summary of the created task with all provided details
    """
    form = UserInput(ctx)
    result = await form.ask("task", "Please provide task details", TaskDetails)
    if result is None:
        return form.input_required()

    if result.action == "accept":
        task = result.data
        return (
            f"Task created successfully!\n"
            f"Title: {task.title}\n"
            f"Description: {task.description}\n"
            f"Priority: {task.priority}\n"
            f"Due Date: {task.due_date}"
        )
    elif result.action == "decline":
        return "Task creation declined."
    else:  # cancel
        return "Task creation cancelled."


@mcp.tool
async def multi_turn_survey(ctx: Context) -> str | InputRequiredResult:
    """
    Multi-turn elicitation example that collects information step by step.

    This tool demonstrates progressive elicitation - asking multiple
    questions in sequence to gather information gradually. Each question is
    its own round trip: the tool is re-run with the new answer, and the
    answers given so far come back in the request state.

    Returns:
        Summary of all collected survey responses
    """
    form = UserInput(ctx)

    # Step 1: Get name
    name_result = await form.ask("name", "What's your name?", str, title="Name")
    if name_result is None:
        return form.input_required()
    if name_result.action != "accept":
        return "Survey cancelled at name question."
    name = name_result.data

    # Step 2: Get age
    age_result = await form.ask("age", "What's your age?", int, title="Age")
    if age_result is None:
        return form.input_required()
    if age_result.action != "accept":
        return f"Survey cancelled at age question. Thanks anyway, {name}!"
    age = age_result.data

    # Step 3: Get favorite food
    food_result = await form.ask(
        "food",
        "What's your favorite food?",
        ["pizza", "sushi", "tacos", "burgers", "other"],
        title="Favorite food",
    )
    if food_result is None:
        return form.input_required()
    if food_result.action != "accept":
        return f"Survey cancelled at food question. Thanks for participating, {name}!"
    favorite_food = food_result.data

    # Step 4: Get satisfaction rating
    rating_result = await form.ask(
        "rating",
        "How satisfied are you with this survey? (1-10)",
        int,
        title="Rating",
    )
    if rating_result is None:
        return form.input_required()
    if rating_result.action != "accept":
        return f"Survey almost complete! Thanks for your responses, {name}!"
    rating = rating_result.data

    # All responses collected successfully
    return (
        f"Survey Complete! Thank you, {name}!\n\n"
        f"Name: {name}\n"
        f"Age: {age}\n"
        f"Favorite Food: {favorite_food}\n"
        f"Satisfaction Rating: {rating}/10\n\n"
        f"We appreciate your time!"
    )


@mcp.tool
async def approve_deletion(ctx: Context) -> str | InputRequiredResult:
    """
    Elicitation example requesting a yes/no approval.

    This tool demonstrates approval elicitation - asking the user to approve
    or reject a destructive action. FastMCP 4 no longer sends an empty form
    for this (clients rendered it inconsistently), so the approval is a
    single boolean field.

    Returns:
        Result of the deletion approval
    """
    form = UserInput(ctx)
    result = await form.ask(
        "approve",
        "Are you sure you want to delete this item? This action cannot be undone.",
        bool,
        title="Yes, delete it",
    )
    if result is None:
        return form.input_required()

    if result.action == "accept":
        if result.data:
            return "Item deleted successfully."
        return "Deletion not approved. Item was not deleted."
    elif result.action == "decline":
        return "Deletion declined. Item was not deleted."
    else:  # cancel
        return "Operation cancelled. Item was not deleted."


if __name__ == "__main__":
    mcp.run(show_banner=False)
