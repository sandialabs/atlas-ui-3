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
"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from fastmcp import Context, FastMCP

# Initialize the MCP server
mcp = FastMCP("Elicitation Demo")


@mcp.tool
async def get_user_name(ctx: Context) -> str:
    """
    Simple elicitation example that asks for a string input.

    This tool demonstrates basic string elicitation - asking the user
    for their name and using it in the response.

    Returns:
        Greeting message with the user's name, or indication if not provided
    """
    result = await ctx.elicit("What's your name?", response_type=str)

    if result.action == "accept":
        return f"Hello, {result.data}! Nice to meet you."
    elif result.action == "decline":
        return "No name provided. That's okay!"
    else:  # cancel
        return "Operation cancelled."


@mcp.tool
async def pick_a_number(ctx: Context) -> str:
    """
    Elicitation example that requests an integer input.

    This tool demonstrates numeric elicitation - asking the user
    for a number and performing a simple calculation.

    Returns:
        Information about the picked number, or indication if not provided
    """
    result = await ctx.elicit("Pick a number between 1 and 100!", response_type=int)

    if result.action == "accept":
        number = result.data
        doubled = number * 2
        return f"You picked {number}! Doubled, that's {doubled}."
    elif result.action == "decline":
        return "No number provided."
    else:  # cancel
        return "Operation cancelled."


@mcp.tool
async def confirm_action(ctx: Context) -> str:
    """
    Elicitation example that requests boolean confirmation.

    This tool demonstrates boolean elicitation - asking the user
    to confirm or reject an action.

    Returns:
        Result of the confirmation decision
    """
    result = await ctx.elicit("Do you want to proceed with this action?", response_type=bool)

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
async def set_priority(ctx: Context) -> str:
    """
    Elicitation example using enum for constrained options.

    This tool demonstrates enum-based elicitation - asking the user
    to choose from a predefined set of priority levels.

    Returns:
        Confirmation of the selected priority level
    """
    result = await ctx.elicit("What priority level?", response_type=Priority)

    if result.action == "accept":
        return f"Priority set to: {result.data.value}"
    elif result.action == "decline":
        return "No priority set."
    else:  # cancel
        return "Operation cancelled."


@mcp.tool
async def choose_option(ctx: Context) -> str:
    """
    Elicitation example using list of strings for simple options.

    This tool demonstrates string literal elicitation - asking the user
    to choose from a list of options provided as strings.

    Returns:
        Confirmation of the selected option
    """
    result = await ctx.elicit(
        "Choose your favorite color:",
        response_type=["red", "blue", "green", "yellow"]
    )

    if result.action == "accept":
        return f"You chose: {result.data}"
    elif result.action == "decline":
        return "No color chosen."
    else:  # cancel
        return "Operation cancelled."


@dataclass
class TaskDetails:
    """Structured data for task creation."""
    title: str
    description: str
    priority: Literal["low", "medium", "high"]
    due_date: str


@mcp.tool
async def create_task(ctx: Context) -> str:
    """
    Elicitation example with structured multi-field response.

    This tool demonstrates structured elicitation - asking the user
    to provide multiple fields at once using a dataclass.

    Returns:
        Summary of the created task with all provided details
    """
    result = await ctx.elicit(
        "Please provide task details",
        response_type=TaskDetails
    )

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
async def multi_turn_survey(ctx: Context) -> str:
    """
    Multi-turn elicitation example that collects information step by step.

    This tool demonstrates progressive elicitation - asking multiple
    questions in sequence to gather information gradually.

    Returns:
        Summary of all collected survey responses
    """
    # Step 1: Get name
    name_result = await ctx.elicit("What's your name?", response_type=str)
    if name_result.action != "accept":
        return "Survey cancelled at name question."
    name = name_result.data

    # Step 2: Get age
    age_result = await ctx.elicit("What's your age?", response_type=int)
    if age_result.action != "accept":
        return f"Survey cancelled at age question. Thanks anyway, {name}!"
    age = age_result.data

    # Step 3: Get favorite food
    food_result = await ctx.elicit(
        "What's your favorite food?",
        response_type=["pizza", "sushi", "tacos", "burgers", "other"]
    )
    if food_result.action != "accept":
        return f"Survey cancelled at food question. Thanks for participating, {name}!"
    favorite_food = food_result.data

    # Step 4: Get satisfaction rating
    rating_result = await ctx.elicit(
        "How satisfied are you with this survey? (1-10)",
        response_type=int
    )
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
async def approve_deletion(ctx: Context) -> str:
    """
    Elicitation example requesting approval with no data response.

    This tool demonstrates approval-only elicitation - asking the user
    to simply approve or reject without providing any additional data.

    Returns:
        Result of the deletion approval
    """
    result = await ctx.elicit(
        "Are you sure you want to delete this item? This action cannot be undone.",
        response_type=None
    )

    if result.action == "accept":
        return "Item deleted successfully."
    elif result.action == "decline":
        return "Deletion declined. Item was not deleted."
    else:  # cancel
        return "Operation cancelled. Item was not deleted."


if __name__ == "__main__":
    mcp.run()
