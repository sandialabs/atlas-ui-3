#!/usr/bin/env python3
"""
Prompts MCP Server using FastMCP
Provides specialized system prompts that can be applied to modify the AI's behavior.
"""

from typing import Any, Dict

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Prompts")


@mcp.prompt
def financial_tech_wizard() -> str:
    """Think like a financial tech wizard - expert in fintech, trading algorithms, and financial markets."""
    return """System: You are a financial technology wizard with deep expertise in:
- Financial markets, trading strategies, and algorithmic trading
- Fintech solutions, payment systems, and blockchain technology
- Risk management, quantitative analysis, and financial modeling
- Regulatory compliance and financial technology innovation

Think analytically, provide data-driven insights, and consider both technical and business aspects when responding to financial questions. Use precise financial terminology and cite relevant market examples when appropriate.

User: Please adopt this personality and expertise for our conversation."""


@mcp.prompt
def expert_dog_trainer() -> str:
    """You are an expert dog trainer with years of experience in canine behavior and training."""
    return """System: You are an expert dog trainer with over 15 years of experience in:
- Canine behavior analysis and psychology
- Positive reinforcement training methods
- Puppy training, obedience training, and behavioral modification
- Working with different breeds and temperaments
- Problem solving for common behavioral issues

Always provide practical, humane, and evidence-based training advice. Consider the dog's age, breed, and individual personality when making recommendations. Emphasize positive reinforcement and building trust between dog and owner.

User: Please adopt this expertise for our conversation."""


@mcp.prompt
def creative_writer() -> str:
    """You are a creative writing expert focused on storytelling, character development, and narrative craft."""
    return """System: You are a creative writing expert with expertise in:
- Storytelling techniques, plot development, and narrative structure
- Character development, dialogue writing, and world-building
- Multiple genres including fiction, poetry, screenwriting, and creative nonfiction
- Writing craft, style, and literary devices
- Workshop facilitation and constructive feedback

Approach writing with creativity, technical skill, and attention to voice and style. Provide specific, actionable advice that helps writers develop their craft while honoring their unique creative vision.

User: Please adopt this creative writing expertise for our conversation."""


@mcp.prompt
def truncation_demo_super_long_description() -> str:
    """Truncation demo: intentionally long description for UI testing.

    This prompt exists to validate that the frontend truncation logic for prompt descriptions works as intended.
    It is deliberately verbose (well over 500 characters) so the Tools & Integrations UI can show:
    - An info icon next to the prompt name
    - An expandable description panel
    - The truncated rendering (start + "..." + end) for very long descriptions

    Suggested manual check:
    1) Open Tools & Integrations
    2) Expand the MCP server named "Prompts"
    3) Find this prompt and click the info icon
    4) Confirm the description is truncated with the first and last portions visible

    Additional filler to ensure we exceed the truncation threshold:
    Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
    Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.
    Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.
    Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
    Repeat for length: lorem ipsum, lorem ipsum, lorem ipsum, lorem ipsum, lorem ipsum, lorem ipsum, lorem ipsum.

    Now let's talk about road runners and cactus in New Mexico - these iconic symbols of the American southwest have captivated people for generations. The roadrunner, known scientifically as Geococcyx californianus, is a large ground-dwelling bird that can reach speeds up to 20 miles per hour. These clever birds are often found in the deserts and grasslands of New Mexico, using their long legs and tails for balance while navigating the rugged terrain. Famous for their distinctive cooing calls and the legendary belief that they run in front of cars to create a wind vacuum that helps them fly, roadrunners are actually quite terrestrial.

    The cactus of New Mexico are equally fascinating, with over 100 species found throughout the state. Perhaps most iconic is the saguaro cactus, though they're more common in Arizona. In New Mexico, you'll find the cholla cactus with its distinctive hooked spines that easily detach and cling to anything that brushes against them, the prickly pear cactus that produces sweet edible fruits, and the yucca plant which, while technically not a cactus, shares many similar adaptations for desert life. These plants have evolved amazing survival strategies including thick waxy coatings to reduce water loss, shallow but extensive root systems to capture rare rainfall, and spines that not only deter herbivores but also create shade for the plant itself.

    The relationship between roadrunners and cactus in New Mexico's ecosystem is quite interesting. Roadrunners often use cactus as perches to survey their hunting grounds, and they've developed hunting techniques that involve luring prey toward prickly barriers where the animal gets stuck, allowing the roadrunner to easily capture it. This predator-prey dynamic has been playing out across New Mexico's deserts for thousands of years, adapting to the harsh environmental conditions that both species share.

    Speaking of desert adaptations, both roadrunners and cactus have remarkable abilities to survive extreme heat and minimal water. Roadrunners can go without drinking water for long periods by obtaining moisture from their food, particularly from the insects and small reptiles they consume. Cactus store water in their thick stems and can go months without rainfall. During the monsoon season in New Mexico, typically from June through September, both roadrunners and cactus benefit from the sudden abundance of water and food sources.

    The cultural significance of roadrunners and cactus in New Mexico cannot be overstated. The roadrunner appears in Native American folklore, particularly in the stories of the Pueblo people, where it represents cleverness and survival. Cactus have been used for food, medicine, and even as building materials by indigenous peoples for centuries. Today, both continue to symbolize the beauty and resilience of the desert southwest, appearing in art, literature, and popular culture.

    Interestingly, New Mexico's roadsides and walking trails are often lined with various cactus species, creating natural barriers and habitats for wildlife. Roadrunners can frequently be seen darting across highways or scurrying alongside desert paths, sometimes pausing to bob their heads in that characteristic way that has made them so endearing. The state's diverse geography means you'll find different cactus species at varying elevations - from the lowland deserts around Las Cruces to the mountain foothills near Santa Fe.

    End marker: the UI should still show this ending segment after truncation, and we've now covered the fascinating intersection of roadrunners and cactus in New Mexico's unique ecosystem.
    """
    return """System: You are running a UI truncation demo. Keep responses short.

If the user asks what this is for, explain that it is a test prompt whose description is intentionally long to validate UI truncation behavior, and that it also contains extended information about roadrunners and cactus found in New Mexico.

User: Please adopt this behavior for our conversation."""


@mcp.prompt
def ask_about_topic(topic: str) -> str:
    """Generates a user message asking for an explanation of a topic."""
    return f"Can you please explain the concept of '{topic}'?"


@mcp.prompt
def generate_code_request(language: str, task_description: str) -> str:
    """Generates a user message requesting code generation."""
    return f"Write a {language} function that performs the following task: {task_description}"


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
        },
        "truncation_demo_super_long_description": {
            "description": (
                "Truncation demo: intentionally long description for UI testing. "
                "This prompt exists to validate that the frontend truncation logic for prompt descriptions works as intended. "
                "It is deliberately verbose (well over 500 characters) so the Tools & Integrations UI can show an info icon, "
                "an expandable description panel, and a truncated rendering (start + '...' + end) for very long descriptions. "
                "Additional filler to ensure we exceed the truncation threshold: Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
                "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation. "
                "End marker: the UI should still show this ending segment after truncation."
            ),
            "type": "system_prompt",
            "category": "demo"
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
    mcp.run(show_banner=False)
