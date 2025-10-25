#!/usr/bin/env python3
"""
Prompts MCP Server using FastMCP
Provides specialized system prompts that can be applied to modify the AI's behavior.
"""

from typing import Dict, Any
from fastmcp import FastMCP
from fastmcp.prompts.prompt import Message, PromptMessage, TextContent

# Initialize the MCP server
mcp = FastMCP("Prompts")


@mcp.prompt
def financial_tech_wizard() -> PromptMessage:
    """Think like a financial tech wizard - expert in fintech, trading algorithms, and financial markets."""
    content = """You are a financial technology wizard with deep expertise in:
- Financial markets, trading strategies, and algorithmic trading
- Fintech solutions, payment systems, and blockchain technology  
- Risk management, quantitative analysis, and financial modeling
- Regulatory compliance and financial technology innovation

Think analytically, provide data-driven insights, and consider both technical and business aspects when responding to financial questions. Use precise financial terminology and cite relevant market examples when appropriate."""
    
    return PromptMessage(role="user", content=TextContent(type="text", text=f"System: {content}\n\nUser: Please adopt this personality and expertise for our conversation."))


@mcp.prompt
def expert_dog_trainer() -> PromptMessage:
    """You are an expert dog trainer with years of experience in canine behavior and training."""
    content = """You are an expert dog trainer with over 15 years of experience in:
- Canine behavior analysis and psychology
- Positive reinforcement training methods
- Puppy training, obedience training, and behavioral modification
- Working with different breeds and temperaments
- Problem solving for common behavioral issues

Always provide practical, humane, and evidence-based training advice. Consider the dog's age, breed, and individual personality when making recommendations. Emphasize positive reinforcement and building trust between dog and owner."""
    
    return PromptMessage(role="user", content=TextContent(type="text", text=f"System: {content}\n\nUser: Please adopt this expertise for our conversation."))


@mcp.prompt
def creative_writer() -> PromptMessage:
    """You are a creative writing expert focused on storytelling, character development, and narrative craft."""
    content = """You are a creative writing expert with expertise in:
- Storytelling techniques, plot development, and narrative structure
- Character development, dialogue writing, and world-building
- Multiple genres including fiction, poetry, screenwriting, and creative nonfiction
- Writing craft, style, and literary devices
- Workshop facilitation and constructive feedback

Approach writing with creativity, technical skill, and attention to voice and style. Provide specific, actionable advice that helps writers develop their craft while honoring their unique creative vision."""
    
    return PromptMessage(role="user", content=TextContent(type="text", text=f"System: {content}\n\nUser: Please adopt this creative writing expertise for our conversation."))


@mcp.prompt
def ask_about_topic(topic: str) -> str:
    """Generates a user message asking for an explanation of a topic."""
    return f"Can you please explain the concept of '{topic}'?"


@mcp.prompt
def generate_code_request(language: str, task_description: str) -> PromptMessage:
    """Generates a user message requesting code generation."""
    content = f"Write a {language} function that performs the following task: {task_description}"
    return PromptMessage(role="user", content=TextContent(type="text", text=content))


@mcp.tool
def list_available_prompts() -> Dict[str, Any]:
    """
    Discover and enumerate all available AI personality and expertise system prompts for customizing assistant behavior.

    This prompt management tool provides comprehensive access to AI behavior modification capabilities:
    
    **System Prompt Categories:**
    - Professional expertise prompts (financial, technical, business)
    - Creative and artistic personality prompts (writing, design, storytelling)
    - Educational and training-focused prompts (teaching, coaching, mentoring)
    - Specialized domain knowledge prompts (industry-specific expertise)

    **Available Professional Prompts:**
    - Financial Tech Wizard: Fintech expertise, trading algorithms, market analysis
    - Expert Dog Trainer: Canine behavior, training methods, pet psychology
    - Creative Writer: Storytelling, character development, narrative craft

    **Prompt Customization Features:**
    - Detailed personality and expertise descriptions
    - Behavioral modification instructions
    - Domain-specific knowledge activation
    - Communication style and approach guidance

    **AI Behavior Modification:**
    - Specialized knowledge domain activation
    - Professional communication style adaptation
    - Expert-level analytical thinking patterns
    - Industry-specific terminology and concepts

    **Use Cases:**
    - Specialized consulting and advisory interactions
    - Educational content creation and tutoring
    - Professional analysis and problem-solving
    - Creative project development and brainstorming
    - Domain-specific research and investigation
    - Training and skill development sessions

    **Integration Features:**
    - Compatible with conversation management systems
    - Seamless personality switching capabilities
    - Context-aware prompt application
    - Multi-session personality persistence

    **Customization Benefits:**
    - Enhanced subject matter expertise
    - Improved response relevance and accuracy
    - Professional-grade analytical capabilities
    - Specialized communication patterns

    Returns:
        Dictionary containing:
        - available_prompts: Complete catalog of system prompts with descriptions
        - Each prompt includes: description, type, category, and usage guidelines
        - total_count: Number of available prompts
        - categories: Organized groupings of prompt types
        Or error message if prompt discovery fails
    """
    prompts = {
        "financial_tech_wizard": {
            "description": "Think like a financial tech wizard - expert in fintech, trading algorithms, and financial markets",
            "type": "system_prompt",
            "category": "professional"
        },
        "expert_dog_trainer": {
            "description": "You are an expert dog trainer with years of experience in canine behavior and training",
            "type": "system_prompt", 
            "category": "professional"
        },
        "creative_writer": {
            "description": "You are a creative writing expert focused on storytelling, character development, and narrative craft",
            "type": "system_prompt",
            "category": "creative"
        }
    }
    
    return {
        "results": {
            "available_prompts": prompts,
            "total_count": len(prompts),
            "categories": list(set(p["category"] for p in prompts.values()))
        }
    }


if __name__ == "__main__":
    mcp.run()