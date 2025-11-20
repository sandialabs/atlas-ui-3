#!/usr/bin/env python3
"""
Simple script to generate a sample VTK file for testing the VTK viewer.
This creates a simple cube mesh in VTK legacy ASCII format.
"""

def generate_simple_cube_vtk():
    """Generate a simple cube in VTK legacy format."""
    vtk_content = """# vtk DataFile Version 3.0
Simple Cube for Testing
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
    return vtk_content


def generate_simple_sphere_vtk():
    """Generate a simple sphere using parametric equations."""
    import math
    
    # Sphere parameters
    radius = 1.0
    u_resolution = 20
    v_resolution = 20
    
    points = []
    polygons = []
    
    # Generate points
    for i in range(u_resolution + 1):
        u = (i / u_resolution) * 2 * math.pi
        for j in range(v_resolution + 1):
            v = (j / v_resolution) * math.pi
            x = radius * math.sin(v) * math.cos(u)
            y = radius * math.sin(v) * math.sin(u)
            z = radius * math.cos(v)
            points.append(f"{x:.6f} {y:.6f} {z:.6f}")
    
    # Generate polygons (quads)
    for i in range(u_resolution):
        for j in range(v_resolution):
            p0 = i * (v_resolution + 1) + j
            p1 = (i + 1) * (v_resolution + 1) + j
            p2 = (i + 1) * (v_resolution + 1) + (j + 1)
            p3 = i * (v_resolution + 1) + (j + 1)
            polygons.append(f"4 {p0} {p1} {p2} {p3}")
    
    vtk_content = f"""# vtk DataFile Version 3.0
Simple Sphere for Testing
ASCII
DATASET POLYDATA
POINTS {len(points)} float
{chr(10).join(points)}

POLYGONS {len(polygons)} {len(polygons) * 5}
{chr(10).join(polygons)}
"""
    return vtk_content


def generate_sample_stl():
    """Generate a simple pyramid in STL ASCII format."""
    stl_content = """solid SimplePyramid
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
    return stl_content


if __name__ == "__main__":
    import sys
    import os
    
    # Create test_data directory if it doesn't exist
    test_dir = os.path.join(os.path.dirname(__file__), "..", "test_data", "vtk_samples")
    os.makedirs(test_dir, exist_ok=True)
    
    # Generate cube
    cube_file = os.path.join(test_dir, "cube.vtk")
    with open(cube_file, "w") as f:
        f.write(generate_simple_cube_vtk())
    print(f"Generated: {cube_file}")
    
    # Generate sphere
    sphere_file = os.path.join(test_dir, "sphere.vtk")
    with open(sphere_file, "w") as f:
        f.write(generate_simple_sphere_vtk())
    print(f"Generated: {sphere_file}")
    
    # Generate pyramid STL
    pyramid_file = os.path.join(test_dir, "pyramid.stl")
    with open(pyramid_file, "w") as f:
        f.write(generate_sample_stl())
    print(f"Generated: {pyramid_file}")
    
    print("\nSample VTK files generated successfully!")
    print(f"Files are located in: {test_dir}")
    print("\nYou can upload these files to test the VTK viewer:")
    print("1. Upload via chat interface")
    print("2. Ask the AI to visualize them in the canvas")
