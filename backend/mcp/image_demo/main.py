"""
MCP server that demonstrates returning ImageContent.
This is a test server to validate image display functionality.
"""

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import ImageContent, TextContent
import base64
from io import BytesIO

mcp = FastMCP("Image Demo MCP")


@mcp.tool()
def generate_test_image() -> ToolResult:
    """
    Generate a simple test PNG image and return it as ImageContent.
    This demonstrates the ImageContent return type for MCP tools.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        # If PIL is not available, return a minimal 1x1 PNG
        # This is a valid 1x1 red pixel PNG in base64
        minimal_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
        return ToolResult(
            content=[
                TextContent(type="text", text="Success: Generated fallback image (PIL not available). Returned image_0.png"),
                ImageContent(type="image", data=minimal_png, mimeType="image/png")
            ]
        )

    # Create a 400x300 image with a gradient background
    img = Image.new('RGB', (400, 300), color='white')
    draw = ImageDraw.Draw(img)

    # Draw a gradient
    for y in range(300):
        color_val = int(255 * (y / 300))
        draw.rectangle([(0, y), (400, y+1)], fill=(color_val, 100, 255 - color_val))

    # Draw some text
    try:
        # Try to use a default font
        font = ImageFont.load_default()
    except Exception:
        font = None

    draw.text((50, 130), "Test Image from MCP Tool", fill=(255, 255, 255), font=font)
    draw.text((50, 160), "This demonstrates ImageContent", fill=(255, 255, 255), font=font)

    # Convert to base64
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    return ToolResult(
        content=[
            TextContent(type="text", text="Success: Generated 400x300 PNG test image with gradient background. Returned image_0.png - the image should now be visible in the canvas panel."),
            ImageContent(type="image", data=img_base64, mimeType="image/png")
        ]
    )


@mcp.tool()
def generate_multiple_images() -> ToolResult:
    """
    Generate multiple test images and return them as a list of ImageContent.
    This tests handling multiple images in a single tool response.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        # Return minimal PNGs if PIL not available
        minimal_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
        return ToolResult(
            content=[
                TextContent(type="text", text="Success: Generated 2 fallback images (PIL not available). Returned image_0.png, image_1.png"),
                ImageContent(type="image", data=minimal_png, mimeType="image/png"),
                ImageContent(type="image", data=minimal_png, mimeType="image/png")
            ]
        )

    content_blocks = [
        TextContent(type="text", text="Success: Generated 3 colored PNG images (200x200 each: red, green, blue with white circles). Returned image_0.png, image_1.png, image_2.png - images should now be visible in the canvas panel.")
    ]
    colors = [(255, 100, 100), (100, 255, 100), (100, 100, 255)]

    for i, color in enumerate(colors):
        img = Image.new('RGB', (200, 200), color=color)
        draw = ImageDraw.Draw(img)

        # Draw a circle
        draw.ellipse([50, 50, 150, 150], fill=(255, 255, 255))

        # Convert to base64
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        content_blocks.append(ImageContent(
            type="image",
            data=img_base64,
            mimeType="image/png"
        ))

    return ToolResult(content=content_blocks)


if __name__ == "__main__":
    mcp.run()
