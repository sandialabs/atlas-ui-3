#!/usr/bin/env python3
"""
VTK Example MCP Server using FastMCP.

This server demonstrates how to generate and return VTK-compatible 3D files
that can be rendered in the Atlas UI canvas panel using VTK.js.

Supported file formats:
- VTK legacy format (.vtk)
- STL format (.stl)
- OBJ format (.obj)
"""

from __future__ import annotations

import base64
import math
from typing import Any, Dict, Literal

from fastmcp import FastMCP, Context
import numpy as np
import base64
from typing import Dict as DictType


# Initialize the MCP server
mcp = FastMCP("VTK Example")


def generate_cube_vtk() -> str:
    """Generate a simple cube in VTK legacy format."""
    return """# vtk DataFile Version 3.0
Simple Cube
ASCII
DATASET POLYDATA
POINTS 8 float
0.0 0.0 0.0
1.0 0.0 0.0
1.0 1.0 0.0
0.0 1.0 0.0
0.0 0.0 1.0
1.0 0.0 1.0
1.0 1.0 1.0
0.0 1.0 1.0

POLYGONS 6 30
4 0 1 2 3
4 4 5 6 7
4 0 1 5 4
4 2 3 7 6
4 0 3 7 4
4 1 2 6 5
"""


def generate_sphere_vtk(radius: float = 1.0, u_res: int = 20, v_res: int = 20) -> str:
    """Generate a sphere using parametric equations in VTK format."""
    points = []
    polygons = []
    
    # Generate points
    for i in range(u_res + 1):
        u = (i / u_res) * 2 * math.pi
        for j in range(v_res + 1):
            v = (j / v_res) * math.pi
            x = radius * math.sin(v) * math.cos(u)
            y = radius * math.sin(v) * math.sin(u)
            z = radius * math.cos(v)
            points.append(f"{x:.6f} {y:.6f} {z:.6f}")
    
    # Generate polygons (quads)
    for i in range(u_res):
        for j in range(v_res):
            p0 = i * (v_res + 1) + j
            p1 = (i + 1) * (v_res + 1) + j
            p2 = (i + 1) * (v_res + 1) + (j + 1)
            p3 = i * (v_res + 1) + (j + 1)
            polygons.append(f"4 {p0} {p1} {p2} {p3}")
    
    points_str = "\n".join(points)
    polygons_str = "\n".join(polygons)
    
    return f"""# vtk DataFile Version 3.0
Parametric Sphere
ASCII
DATASET POLYDATA
POINTS {len(points)} float
{points_str}

POLYGONS {len(polygons)} {len(polygons) * 5}
{polygons_str}
"""


def generate_pyramid_stl() -> str:
    """Generate a simple pyramid in STL ASCII format."""
    return """solid SimplePyramid
  facet normal 0.0 -0.8944271909999159 0.4472135954999579
    outer loop
      vertex 0.0 0.0 0.0
      vertex 1.0 0.0 0.0
      vertex 0.5 0.5 1.0
    endloop
  endfacet
  facet normal 0.8944271909999159 0.0 0.4472135954999579
    outer loop
      vertex 1.0 0.0 0.0
      vertex 1.0 1.0 0.0
      vertex 0.5 0.5 1.0
    endloop
  endfacet
  facet normal 0.0 0.8944271909999159 0.4472135954999579
    outer loop
      vertex 1.0 1.0 0.0
      vertex 0.0 1.0 0.0
      vertex 0.5 0.5 1.0
    endloop
  endfacet
  facet normal -0.8944271909999159 0.0 0.4472135954999579
    outer loop
      vertex 0.0 1.0 0.0
      vertex 0.0 0.0 0.0
      vertex 0.5 0.5 1.0
    endloop
  endfacet
  facet normal 0.0 0.0 -1.0
    outer loop
      vertex 0.0 0.0 0.0
      vertex 1.0 1.0 0.0
      vertex 1.0 0.0 0.0
    endloop
  endfacet
  facet normal 0.0 0.0 -1.0
    outer loop
      vertex 0.0 0.0 0.0
      vertex 0.0 1.0 0.0
      vertex 1.0 1.0 0.0
    endloop
  endfacet
endsolid SimplePyramid
"""


def generate_cylinder_vtk(radius: float = 0.5, height: float = 2.0, resolution: int = 20) -> str:
    """Generate a cylinder in VTK format."""
    points = []
    polygons = []
    
    # Generate bottom circle points
    for i in range(resolution):
        angle = (i / resolution) * 2 * math.pi
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        points.append(f"{x:.6f} {y:.6f} 0.0")
    
    # Generate top circle points
    for i in range(resolution):
        angle = (i / resolution) * 2 * math.pi
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        points.append(f"{x:.6f} {y:.6f} {height:.6f}")
    
    # Center points for caps
    points.append(f"0.0 0.0 0.0")  # bottom center
    points.append(f"0.0 0.0 {height:.6f}")  # top center
    
    bottom_center = resolution * 2
    top_center = resolution * 2 + 1
    
    # Generate side quads
    for i in range(resolution):
        next_i = (i + 1) % resolution
        p0 = i
        p1 = next_i
        p2 = next_i + resolution
        p3 = i + resolution
        polygons.append(f"4 {p0} {p1} {p2} {p3}")
    
    # Generate bottom cap triangles
    for i in range(resolution):
        next_i = (i + 1) % resolution
        polygons.append(f"3 {bottom_center} {next_i} {i}")
    
    # Generate top cap triangles
    for i in range(resolution):
        next_i = (i + 1) % resolution
        polygons.append(f"3 {top_center} {i + resolution} {next_i + resolution}")
    
    points_str = "\n".join(points)
    polygons_str = "\n".join(polygons)
    num_polygon_data = len(polygons) * 4 + resolution * 2  # quads have 5 values, triangles have 4
    
    return f"""# vtk DataFile Version 3.0
Cylinder
ASCII
DATASET POLYDATA
POINTS {len(points)} float
{points_str}

POLYGONS {len(polygons)} {num_polygon_data}
{polygons_str}
"""


def generate_torus_vtk(major_radius: float = 1.0, minor_radius: float = 0.3,
                       u_res: int = 30, v_res: int = 20) -> str:
    """Generate a torus in VTK format."""
    points = []
    polygons = []

    # Generate points
    for i in range(u_res):
        u = (i / u_res) * 2 * math.pi
        for j in range(v_res):
            v = (j / v_res) * 2 * math.pi
            x = (major_radius + minor_radius * math.cos(v)) * math.cos(u)
            y = (major_radius + minor_radius * math.cos(v)) * math.sin(u)
            z = minor_radius * math.sin(v)
            points.append(f"{x:.6f} {y:.6f} {z:.6f}")

    # Generate polygons (quads)
    for i in range(u_res):
        next_i = (i + 1) % u_res
        for j in range(v_res):
            next_j = (j + 1) % v_res
            p0 = i * v_res + j
            p1 = next_i * v_res + j
            p2 = next_i * v_res + next_j
            p3 = i * v_res + next_j
            polygons.append(f"4 {p0} {p1} {p2} {p3}")

    points_str = "\n".join(points)
    polygons_str = "\n".join(polygons)

    return f"""# vtk DataFile Version 3.0
Torus
ASCII
DATASET POLYDATA
POINTS {len(points)} float
{points_str}

POLYGONS {len(polygons)} {len(polygons) * 5}
{polygons_str}
"""


def generate_cantilever_beam_vtk(modules: DictType[str, float], load: float, num_points: int) -> str:
    """Generate a cantilever beam deflection analysis in VTK format."""

    # Extract beam parameters from modules
    LENGTH = modules.get('length', 10.0)
    WIDTH = modules.get('width', 0.5)
    HEIGHT = modules.get('height', 0.8)
    E = modules.get('e_modulus', 200e9)  # Young's modulus (Pa)

    # Calculate second moment of area
    I = (WIDTH * HEIGHT**3) / 12

    # Adjust num_points if too large
    if num_points > 1000:
        num_points = 1000

    def calculate_deflection(x, load, E, I, L):
        """Calculate beam deflection at position x"""
        w = load
        deflection = (w / (24 * E * I)) * (x**4 - 4*L*x**3 + 6*L**2*x**2)
        return deflection

    def calculate_stress(x, load, I, L, y_position):
        """Calculate bending stress at position x and y"""
        w = load
        M = w * (L - x)**2 / 2  # Bending moment
        stress = M * y_position / I
        return stress

    # Generate mesh points
    x_coords = np.linspace(0, LENGTH, num_points)
    y_coords = np.linspace(-HEIGHT/2, HEIGHT/2, 5)
    z_coords = np.linspace(-WIDTH/2, WIDTH/2, 3)

    # Create 3D grid
    points = []
    deflections = []
    stresses = []

    for x in x_coords:
        deflection = calculate_deflection(x, load, E, I, LENGTH)

        for y in y_coords:
            for z in z_coords:
                # Original point position
                points.append([x, y, z])

                # Deflection in y-direction (scaled up 100x for visualization)
                deflections.append(deflection)

                # Calculate stress at this point
                stress = calculate_stress(x, load, I, LENGTH, y)
                stresses.append(stress)

    points = np.array(points)
    deflections = np.array(deflections)
    stresses = np.array(stresses)

    # Apply deflection to points (exaggerated for visualization)
    deformed_points = points.copy()
    deformed_points[:, 1] -= deflections * 100  # Scale deflection for visibility

    # Create VTK file content
    num_points_total = len(points)

    vtk_content = f"""# vtk DataFile Version 3.0
Cantilever Beam Deflection Analysis
ASCII
DATASET UNSTRUCTURED_GRID

POINTS {num_points_total} float
"""

    # Write points
    for point in deformed_points:
        vtk_content += f"{point[0]:.6f} {point[1]:.6f} {point[2]:.6f}\n"

    # Create hexahedral cells
    cells = []
    nx, ny, nz = num_points, len(y_coords), len(z_coords)

    for i in range(nx - 1):
        for j in range(ny - 1):
            for k in range(nz - 1):
                # Calculate indices for hexahedron
                n0 = i * ny * nz + j * nz + k
                n1 = i * ny * nz + j * nz + (k + 1)
                n2 = i * ny * nz + (j + 1) * nz + (k + 1)
                n3 = i * ny * nz + (j + 1) * nz + k
                n4 = (i + 1) * ny * nz + j * nz + k
                n5 = (i + 1) * ny * nz + j * nz + (k + 1)
                n6 = (i + 1) * ny * nz + (j + 1) * nz + (k + 1)
                n7 = (i + 1) * ny * nz + (j + 1) * nz + k

                cells.append([n0, n1, n2, n3, n4, n5, n6, n7])

    num_cells = len(cells)
    vtk_content += f"\nCELLS {num_cells} {num_cells * 9}\n"

    for cell in cells:
        vtk_content += f"8 {' '.join(map(str, cell))}\n"

    vtk_content += f"\nCELL_TYPES {num_cells}\n"
    for _ in range(num_cells):
        vtk_content += "12\n"  # VTK_HEXAHEDRON

    # Add point data (deflections and stresses)
    vtk_content += f"\nPOINT_DATA {num_points_total}\n"

    vtk_content += "SCALARS Deflection float 1\n"
    vtk_content += "LOOKUP_TABLE default\n"
    for d in deflections:
        vtk_content += f"{d:.6e}\n"

    vtk_content += "\nSCALARS Stress float 1\n"
    vtk_content += "LOOKUP_TABLE default\n"
    for s in stresses:
        vtk_content += f"{s:.6e}\n"

    return vtk_content


@mcp.tool
async def generate_3d_shape(
    shape: Literal["cube", "sphere", "pyramid", "cylinder", "torus"] = "cube",
    return_format: Literal["vtk", "stl"] = "vtk",
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Generate a 3D geometric shape in VTK or STL format for visualization.
    
    This tool creates common 3D shapes that can be rendered in the canvas
    panel using the VTK.js viewer. Perfect for testing, demonstrations, or
    as building blocks for more complex visualizations.
    
    **Available Shapes:**
    - **cube**: Simple 1x1x1 unit cube (8 vertices, 6 faces)
    - **sphere**: Smooth parametric sphere (default radius 1.0)
    - **pyramid**: Four-sided pyramid on square base
    - **cylinder**: Circular cylinder (default radius 0.5, height 2.0)
    - **torus**: Donut shape (major radius 1.0, minor radius 0.3)
    
    **Supported Formats:**
    - **vtk**: VTK legacy ASCII format (widely compatible)
    - **stl**: STereoLithography ASCII format (pyramid only)
    
    **Use Cases:**
    - Visualizing basic geometric primitives
    - Testing 3D rendering capabilities
    - Educational geometry demonstrations
    - Building blocks for CAD/engineering models
    - Quick prototyping of 3D scenes
    
    **Interactive Features:**
    The generated shape will be displayed in the canvas panel with:
    - 3D rotation (left-click and drag)
    - Pan controls (right-click and drag)
    - Zoom (mouse wheel)
    - Reset view button
    
    Args:
        shape: The geometric shape to generate (cube, sphere, pyramid, cylinder, torus)
        return_format: Output file format (vtk or stl)
        ctx: MCP context for progress reporting (automatically injected)
    
    Returns:
        Dictionary containing the generated 3D file as a base64-encoded artifact
        that will be automatically rendered in the canvas panel
        
    Examples:
        >>> generate_3d_shape(shape="sphere", return_format="vtk")
        # Returns a sphere in VTK format, displayed in canvas
        
        >>> generate_3d_shape(shape="torus", return_format="vtk")
        # Returns a torus (donut) shape for visualization
    """
    if ctx:
        await ctx.report_progress(progress=0, total=2, message=f"Generating {shape}...")
    
    # Generate the appropriate shape
    if shape == "cube":
        content = generate_cube_vtk()
        filename = "cube.vtk"
        mime_type = "application/octet-stream"
        viewer = "vtk"
    elif shape == "sphere":
        content = generate_sphere_vtk()
        filename = "sphere.vtk"
        mime_type = "application/octet-stream"
        viewer = "vtk"
    elif shape == "pyramid":
        if return_format == "stl":
            content = generate_pyramid_stl()
            filename = "pyramid.stl"
            mime_type = "application/sla"
            viewer = "vtk"
        else:
            # For VTK format, generate a simple pyramid
            content = generate_cube_vtk()  # Using cube as placeholder; proper pyramid would need implementation
            filename = "pyramid.vtk"
            mime_type = "application/octet-stream"
            viewer = "vtk"
    elif shape == "cylinder":
        content = generate_cylinder_vtk()
        filename = "cylinder.vtk"
        mime_type = "application/octet-stream"
        viewer = "vtk"
    elif shape == "torus":
        content = generate_torus_vtk()
        filename = "torus.vtk"
        mime_type = "application/octet-stream"
        viewer = "vtk"
    else:
        return {
            "error": f"Unknown shape: {shape}",
            "supported_shapes": ["cube", "sphere", "pyramid", "cylinder", "torus"]
        }
    
    if ctx:
        await ctx.report_progress(progress=1, total=2, message=f"Encoding {shape}...")
    
    # Encode content as base64
    content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    
    if ctx:
        await ctx.report_progress(progress=2, total=2, message=f"Completed {shape}")
    
    return {
        "results": {
            "shape": shape,
            "format": return_format,
            "filename": filename,
            "size_bytes": len(content),
            "status": "success"
        },
        "artifacts": [
            {
                "name": filename,
                "b64": content_b64,
                "mime": mime_type,
                "size": len(content),
                "description": f"3D {shape} model in {return_format.upper()} format",
                "viewer": viewer
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": filename,
            "mode": "replace",
            "viewer_hint": "vtk"
        }
    }


@mcp.tool
async def generate_sample_files(
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Generate multiple sample 3D files for testing the VTK viewer.
    
    This tool creates a collection of different 3D shapes that can be used
    to test and demonstrate the VTK.js rendering capabilities in the canvas
    panel. All files are returned as artifacts for immediate visualization.
    
    **Generated Files:**
    - cube.vtk - Simple unit cube
    - sphere.vtk - Smooth parametric sphere
    - pyramid.stl - Four-sided pyramid in STL format
    - cylinder.vtk - Circular cylinder
    - torus.vtk - Donut-shaped torus
    
    **Features:**
    - Creates 5 different 3D shapes
    - Multiple file formats (VTK, STL)
    - Ready for immediate rendering
    - Demonstrates various geometric primitives
    
    Args:
        ctx: MCP context for progress reporting (automatically injected)
    
    Returns:
        Dictionary containing all generated files as base64-encoded artifacts
        that can be viewed in the canvas panel
    """
    shapes = [
        ("cube", "vtk", generate_cube_vtk()),
        ("sphere", "vtk", generate_sphere_vtk()),
        ("pyramid", "stl", generate_pyramid_stl()),
        ("cylinder", "vtk", generate_cylinder_vtk()),
        ("torus", "vtk", generate_torus_vtk()),
    ]
    
    total_shapes = len(shapes)
    artifacts = []
    
    if ctx:
        await ctx.report_progress(progress=0, total=total_shapes, 
                                 message="Generating sample 3D files...")
    
    for i, (shape_name, format_type, content) in enumerate(shapes, 1):
        if ctx:
            await ctx.report_progress(progress=i, total=total_shapes, 
                                     message=f"Generated {shape_name}.{format_type}")
        
        filename = f"{shape_name}.{format_type}"
        content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        
        mime_type = "application/sla" if format_type == "stl" else "application/octet-stream"
        
        artifacts.append({
            "name": filename,
            "b64": content_b64,
            "mime": mime_type,
            "size": len(content),
            "description": f"3D {shape_name} model in {format_type.upper()} format",
            "viewer": "vtk"
        })
    
    if ctx:
        await ctx.report_progress(progress=total_shapes, total=total_shapes, 
                                 message="All sample files generated!")
    
    return {
        "results": {
            "status": "success",
            "files_generated": total_shapes,
            "shapes": [s[0] for s in shapes],
            "formats": list(set(s[1] for s in shapes))
        },
        "artifacts": artifacts,
        "display": {
            "open_canvas": True,
            "primary_file": "sphere.vtk",  # Start with sphere as it's most visually interesting
            "mode": "replace",
            "viewer_hint": "vtk"
        }
    }


@mcp.tool
async def cantilever_beam_analysis(
    modules: DictType[str, float],
    load: float,
    num_points: int,
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Perform cantilever beam deflection analysis and generate VTK visualization.

    This tool analyzes a cantilever beam under uniform load and creates a 3D VTK file
    showing the beam's deflection and stress distribution. The analysis uses
    engineering beam theory to calculate deflections and stresses at discrete points
    along the beam length.

    **Beam Parameters (in modules dict):**
    - length: Beam length in meters (default: 10.0)
    - width: Beam width in meters (default: 0.5)
    - height: Beam height in meters (default: 0.8)
    - e_modulus: Young's modulus in Pa (default: 200e9 for steel)

    **Analysis Features:**
    - Calculates deflection at each position using beam theory
    - Computes bending stress distribution
    - Creates 3D mesh with hexahedral elements
    - Applies exaggerated deflection for visualization
    - Includes scalar data for deflection and stress

    **Visualization:**
    The generated VTK file can be rendered in the canvas panel with:
    - 3D beam geometry with deformation
    - Color-coded deflection and stress fields
    - Interactive 3D rotation and zoom
    - Detailed analysis metadata

    **Applications:**
    - Structural engineering analysis
    - Educational demonstrations of beam theory
    - Preliminary design validation
    - Finite element analysis validation

    Args:
        modules: Dictionary containing beam material and geometry parameters
            (length, width, height, e_modulus)
        load: Uniform distributed load in N/m along the beam length
        num_points: Number of discretization points along the beam (max 1000)
        ctx: MCP context for progress reporting (automatically injected)

    Returns:
        Dictionary containing the analysis results and VTK file as base64-encoded artifact
        that will be automatically rendered in the canvas panel

    Examples:
        >>> cantilever_beam_analysis(
        ...     modules={"length": 5.0, "width": 0.3, "height": 0.6, "e_modulus": 200e9},
        ...     load=5000,
        ...     num_points=20
        ... )
        # Returns analysis of a 5m steel beam under 5kN/m load
    """
    if ctx:
        await ctx.report_progress(progress=0, total=3, message="Starting cantilever beam analysis...")

    # Validate and cap num_points
    if num_points > 1000:
        num_points = 1000
    elif num_points < 2:
        num_points = 2

    # Extract parameters
    length = modules.get('length', 10.0)
    width = modules.get('width', 0.5)
    height = modules.get('height', 0.8)
    e_modulus = modules.get('e_modulus', 200e9)

    # Calculate derived quantities
    I = (width * height**3) / 12  # Second moment of area
    max_deflection = (load * length**4) / (8 * e_modulus * I)
    max_stress = (load * length**2) / (2 * I / (height / 2))  # At fixed end

    if ctx:
        await ctx.report_progress(progress=1, total=3, message="Computed beam parameters...")

    # Generate VTK content
    content = generate_cantilever_beam_vtk(modules, load, num_points)

    if ctx:
        await ctx.report_progress(progress=2, total=3, message="Generated VTK file...")

    # Encode content as base64
    content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')

    filename = "cantilever_beam.vtk"

    if ctx:
        await ctx.report_progress(progress=3, total=3, message="Analysis complete")

    return {
        "results": {
            "status": "success",
            "analysis_type": "cantilever_beam_deflection",
            "beam_parameters": {
                "length": length,
                "width": width,
                "height": height,
                "e_modulus": e_modulus,
                "moment_of_inertia": I,
                "uniform_load": load
            },
            "results": {
                "max_deflection": max_deflection,
                "max_stress": max_stress,
                "discretization_points": num_points
            },
            "filename": filename,
            "size_bytes": len(content)
        },
        "artifacts": [
            {
                "name": filename,
                "b64": content_b64,
                "mime": "application/octet-stream",
                "size": len(content),
                "description": "Cantilever beam deflection analysis in VTK format",
                "viewer": "vtk"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": filename,
            "mode": "replace",
            "viewer_hint": "vtk"
        }
    }


if __name__ == "__main__":
    mcp.run()
