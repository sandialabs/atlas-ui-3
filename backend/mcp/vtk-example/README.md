# VTK Example MCP Server

A demonstration MCP server that generates 3D geometric shapes in VTK and STL formats for visualization in the Atlas UI canvas panel.

## Overview

This MCP server showcases how to create and return VTK-compatible 3D files that will be automatically rendered using VTK.js in the Atlas UI canvas. It's perfect for testing the 3D visualization capabilities and serves as a reference implementation for other tools that need to generate 3D content.

## Features

- **Multiple Shape Generation**: Create cubes, spheres, pyramids, cylinders, and tori
- **Format Support**: Generate files in VTK legacy ASCII or STL format
- **Progress Reporting**: Real-time feedback during generation
- **Artifact System**: Files automatically appear in canvas with 3D viewer
- **Batch Generation**: Create multiple sample files at once

## Tools

### `generate_3d_shape`

Generate a single 3D geometric shape.

**Parameters:**
- `shape` (string): The shape to generate
  - `"cube"` - Simple 1x1x1 unit cube
  - `"sphere"` - Parametric sphere (radius 1.0)
  - `"pyramid"` - Four-sided pyramid
  - `"cylinder"` - Circular cylinder (radius 0.5, height 2.0)
  - `"torus"` - Donut shape (major radius 1.0, minor radius 0.3)
- `return_format` (string): Output format
  - `"vtk"` - VTK legacy ASCII format (default)
  - `"stl"` - STereoLithography ASCII format (pyramid only)

**Example Usage:**
```
Generate a sphere in VTK format
Generate a torus and display it
Create a cylinder for visualization
```

**Returns:**
The generated file as an artifact that automatically opens in the canvas panel with the VTK.js 3D viewer.

### `generate_sample_files`

Generate a collection of sample 3D files for testing.

**Parameters:**
None

**Example Usage:**
```
Generate sample 3D files
Show me some VTK examples
Create test shapes for visualization
```

**Returns:**
Five different 3D shapes (cube, sphere, pyramid, cylinder, torus) as artifacts. The sphere is displayed first, and you can navigate between files using the canvas panel controls.

## Technical Details

### File Formats

**VTK Legacy Format (.vtk)**
- ASCII text format
- Human-readable
- Widely compatible with scientific visualization tools
- Supports polygonal data (POLYDATA)

**STL Format (.stl)**
- ASCII text format
- Common in 3D printing and CAD
- Represents surfaces as triangular meshes
- Includes surface normals

### Shape Generation

All shapes are generated programmatically using parametric equations:

- **Sphere**: Uses spherical coordinates (u, v) with configurable resolution
- **Cylinder**: Combines circular profiles with side quads and triangle caps
- **Torus**: Uses toroidal coordinates for donut shape
- **Cube/Pyramid**: Simple vertex and face definitions

### Integration with Canvas

The server uses the MCP artifact system to return files:

```python
{
    "artifacts": [
        {
            "name": "sphere.vtk",
            "b64": base64_encoded_content,
            "mime": "application/octet-stream",
            "viewer": "vtk"
        }
    ],
    "display": {
        "open_canvas": True,
        "primary_file": "sphere.vtk",
        "viewer_hint": "vtk"
    }
}
```

This automatically:
1. Opens the canvas panel
2. Decodes the base64 content
3. Detects the file type based on extension
4. Renders in the VTK.js viewer with interactive controls

## Configuration

To add this server to Atlas UI, add an entry to your MCP configuration:

```json
{
  "name": "vtk-example",
  "transport": "stdio",
  "command": "python",
  "args": ["backend/mcp/vtk-example/main.py"],
  "cwd": "backend/mcp/vtk-example",
  "groups": ["user"],
  "description": "Generate 3D shapes for VTK visualization"
}
```

## Development

### Running Standalone

```bash
python main.py
```

### Testing

You can test the server using the FastMCP inspector:

```bash
fastmcp inspect main.py
```

### Adding New Shapes

To add a new shape:

1. Create a generator function that returns VTK or STL format string
2. Add the shape to the `shape` parameter's Literal type
3. Add a case in the `generate_3d_shape` function
4. Update documentation

Example:
```python
def generate_cone_vtk(radius: float = 1.0, height: float = 2.0) -> str:
    # Implementation here
    pass
```

## References

- [VTK File Formats](https://vtk.org/wp-content/uploads/2015/04/file-formats.pdf)
- [STL Format Specification](https://en.wikipedia.org/wiki/STL_(file_format))
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [VTK.js Documentation](https://kitware.github.io/vtk-js/)

## License

Same as Atlas UI project (MIT License)
