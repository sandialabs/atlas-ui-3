/**
 * Tests for VTK file type detection and handling
 */

import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useFiles } from '../hooks/chat/useFiles';

describe('VTK File Type Detection', () => {
  it('should detect VTK format files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('model.vtk')).toBe('vtk');
    expect(getFileType('MODEL.VTK')).toBe('vtk');
  });

  it('should detect VTP (VTK PolyData) files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('mesh.vtp')).toBe('vtk');
    expect(getFileType('MESH.VTP')).toBe('vtk');
  });

  it('should detect VTU (VTK Unstructured Grid) files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('simulation.vtu')).toBe('vtk');
  });

  it('should detect STL files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('part.stl')).toBe('vtk');
    expect(getFileType('PART.STL')).toBe('vtk');
  });

  it('should detect OBJ files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('model.obj')).toBe('vtk');
  });

  it('should detect PLY files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('scan.ply')).toBe('vtk');
  });

  it('should detect VTI (VTK Image Data) files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('volume.vti')).toBe('vtk');
  });

  it('should detect VTS (VTK Structured Grid) files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('grid.vts')).toBe('vtk');
  });

  it('should detect VTR (VTK Rectilinear Grid) files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('rectilinear.vtr')).toBe('vtk');
  });

  it('should detect GLTF files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('scene.gltf')).toBe('vtk');
  });

  it('should detect GLB files', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('model.glb')).toBe('vtk');
  });

  it('should detect image files correctly', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('photo.png')).toBe('image');
    expect(getFileType('photo.jpg')).toBe('image');
  });

  it('should detect PDF files correctly', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('document.pdf')).toBe('pdf');
  });

  it('should detect HTML files correctly', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('page.html')).toBe('html');
  });

  it('should return other for unknown file types', () => {
    const { result } = renderHook(() => useFiles());
    const { getFileType } = result.current;
    
    expect(getFileType('file.xyz')).toBe('other');
    expect(getFileType('file.unknown')).toBe('other');
  });
});
