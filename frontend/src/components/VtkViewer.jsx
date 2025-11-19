import { useEffect, useRef, useState } from 'react'
import { RotateCw, ZoomIn, ZoomOut, Maximize2, Scissors } from 'lucide-react'

// Import VTK.js modules
import '@kitware/vtk.js/Rendering/Profiles/Geometry'
import vtkFullScreenRenderWindow from '@kitware/vtk.js/Rendering/Misc/FullScreenRenderWindow'
import vtkActor from '@kitware/vtk.js/Rendering/Core/Actor'
import vtkMapper from '@kitware/vtk.js/Rendering/Core/Mapper'
import vtkPolyDataReader from '@kitware/vtk.js/IO/Legacy/PolyDataReader'
import vtkXMLPolyDataReader from '@kitware/vtk.js/IO/XML/XMLPolyDataReader'
import vtkSTLReader from '@kitware/vtk.js/IO/Geometry/STLReader'
import vtkOBJReader from '@kitware/vtk.js/IO/Misc/OBJReader'
import vtkPLYReader from '@kitware/vtk.js/IO/Geometry/PLYReader'
import vtkPlane from '@kitware/vtk.js/Common/DataModel/Plane'
import vtkPolyDataNormals from '@kitware/vtk.js/Filters/Core/PolyDataNormals'

const VtkViewer = ({ fileContent, filename }) => {
  const containerRef = useRef(null)
  const fullScreenRendererRef = useRef(null)
  const polyDataRef = useRef(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [slicingEnabled, setSlicingEnabled] = useState(false)
  const [slicePosition, setSlicePosition] = useState(50)
  const [sliceAxis, setSliceAxis] = useState('z')

  useEffect(() => {
    if (!containerRef.current || !fileContent) {
      return
    }

    let fullScreenRenderer = null
    let actor = null

    const initializeViewer = async () => {
      try {
        setIsLoading(true)
        setError(null)

        // Clear any existing content
        if (containerRef.current) {
          containerRef.current.innerHTML = ''
        }

        // Create full screen render window
        fullScreenRenderer = vtkFullScreenRenderWindow.newInstance({
          container: containerRef.current,
          background: [0.1, 0.1, 0.1]
        })
        fullScreenRendererRef.current = fullScreenRenderer

        const renderer = fullScreenRenderer.getRenderer()
        const renderWindow = fullScreenRenderer.getRenderWindow()

        // Get the appropriate reader based on file extension
        const extension = filename.toLowerCase().split('.').pop()
        let reader = null

        // Convert content to ArrayBuffer if it's a string (base64)
        let arrayBuffer
        if (typeof fileContent === 'string') {
          // Assume base64 encoded
          const binaryString = atob(fileContent)
          const bytes = new Uint8Array(binaryString.length)
          for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i)
          }
          arrayBuffer = bytes.buffer
        } else if (fileContent instanceof ArrayBuffer) {
          arrayBuffer = fileContent
        } else if (fileContent instanceof Blob) {
          arrayBuffer = await fileContent.arrayBuffer()
        } else {
          throw new Error('Unsupported content type')
        }

        // Select appropriate reader based on file type
        switch (extension) {
          case 'vtk':
            reader = vtkPolyDataReader.newInstance()
            // Legacy VTK format uses parseAsText for ASCII files
            if (typeof fileContent === 'string') {
              // If we have base64 string, decode to text first
              const textContent = new TextDecoder().decode(arrayBuffer)
              reader.parseAsText(textContent)
            } else {
              // For binary content, convert ArrayBuffer to string
              const textContent = new TextDecoder().decode(arrayBuffer)
              reader.parseAsText(textContent)
            }
            break
          case 'vtp':
          case 'vtu':
          case 'vti':
          case 'vts':
          case 'vtr':
            reader = vtkXMLPolyDataReader.newInstance()
            reader.parseAsArrayBuffer(arrayBuffer)
            break
          case 'stl':
            reader = vtkSTLReader.newInstance()
            reader.parseAsArrayBuffer(arrayBuffer)
            break
          case 'obj':
            reader = vtkOBJReader.newInstance()
            reader.parseAsArrayBuffer(arrayBuffer)
            break
          case 'ply':
            reader = vtkPLYReader.newInstance()
            reader.parseAsArrayBuffer(arrayBuffer)
            break
          default:
            throw new Error(`Unsupported file format: ${extension}`)
        }

        // Store polydata for slicing operations
        const polyData = reader.getOutputData()
        polyDataRef.current = polyData

        // Compute normals if not present (fixes WebGL samplerBuffer errors)
        const normalsFilter = vtkPolyDataNormals.newInstance()
        normalsFilter.setInputConnection(reader.getOutputPort())
        normalsFilter.setComputePointNormals(true)
        normalsFilter.setComputeCellNormals(false)

        // Create mapper
        const mapper = vtkMapper.newInstance()
        mapper.setInputConnection(normalsFilter.getOutputPort())

        // Create actor
        actor = vtkActor.newInstance()
        actor.setMapper(mapper)
        
        // Set some nice default properties
        actor.getProperty().setColor(0.8, 0.8, 0.9)
        actor.getProperty().setAmbient(0.3)
        actor.getProperty().setDiffuse(0.7)
        actor.getProperty().setSpecular(0.3)
        actor.getProperty().setSpecularPower(30)

        // Add actor to renderer
        renderer.addActor(actor)
        renderer.resetCamera()
        renderWindow.render()

        setIsLoading(false)
      } catch (err) {
        console.error('Error loading VTK file:', err)
        setError(err.message || 'Failed to load file')
        setIsLoading(false)
      }
    }

    initializeViewer()

    // Cleanup function
    return () => {
      if (fullScreenRendererRef.current) {
        fullScreenRendererRef.current.delete()
        fullScreenRendererRef.current = null
      }
      if (actor) {
        actor.delete()
      }
    }
  }, [fileContent, filename])

  const handleResetView = () => {
    if (fullScreenRendererRef.current) {
      const renderer = fullScreenRendererRef.current.getRenderer()
      const renderWindow = fullScreenRendererRef.current.getRenderWindow()
      renderer.resetCamera()
      renderWindow.render()
    }
  }

  const handleZoomIn = () => {
    if (fullScreenRendererRef.current) {
      const renderer = fullScreenRendererRef.current.getRenderer()
      const renderWindow = fullScreenRendererRef.current.getRenderWindow()
      const camera = renderer.getActiveCamera()
      camera.zoom(1.2)
      renderWindow.render()
    }
  }

  const handleZoomOut = () => {
    if (fullScreenRendererRef.current) {
      const renderer = fullScreenRendererRef.current.getRenderer()
      const renderWindow = fullScreenRendererRef.current.getRenderWindow()
      const camera = renderer.getActiveCamera()
      camera.zoom(0.8)
      renderWindow.render()
    }
  }

  const toggleSlicing = () => {
    setSlicingEnabled(!slicingEnabled)
  }

  // Apply slicing when enabled or slice parameters change
  useEffect(() => {
    if (!fullScreenRendererRef.current || !polyDataRef.current) {
      return
    }

    const renderer = fullScreenRendererRef.current.getRenderer()
    const renderWindow = fullScreenRendererRef.current.getRenderWindow()
    const actors = renderer.getActors()
    
    if (actors.length === 0) return
    
    const actor = actors[0]
    const mapper = actor.getMapper()

    // Remove all existing clipping planes
    mapper.removeAllClippingPlanes()

    if (slicingEnabled) {
      // Get bounds of the data
      const bounds = polyDataRef.current.getBounds()
      const [xMin, xMax, yMin, yMax, zMin, zMax] = bounds

      // Create clipping plane
      const plane = vtkPlane.newInstance()
      
      // Set plane position and normal based on axis
      if (sliceAxis === 'x') {
        const x = xMin + (xMax - xMin) * (slicePosition / 100)
        plane.setOrigin(x, 0, 0)
        plane.setNormal(1, 0, 0)
      } else if (sliceAxis === 'y') {
        const y = yMin + (yMax - yMin) * (slicePosition / 100)
        plane.setOrigin(0, y, 0)
        plane.setNormal(0, 1, 0)
      } else { // z
        const z = zMin + (zMax - zMin) * (slicePosition / 100)
        plane.setOrigin(0, 0, z)
        plane.setNormal(0, 0, 1)
      }

      // Add clipping plane to mapper
      mapper.addClippingPlane(plane)
      
      // Important: Call modified() to trigger update
      mapper.modified()
    }

    renderWindow.render()
  }, [slicingEnabled, slicePosition, sliceAxis])

  return (
    <div className="relative w-full h-full bg-gray-900">
      {isLoading && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-900 z-10">
          <div className="text-gray-400">Loading 3D model...</div>
        </div>
      )}
      
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-gray-900 z-10">
          <div className="text-red-400 text-center p-4">
            <p className="font-semibold mb-2">Error loading 3D model</p>
            <p className="text-sm">{error}</p>
          </div>
        </div>
      )}

      <div 
        ref={containerRef} 
        className="w-full h-full"
        style={{ minHeight: '400px' }}
      />

      {!isLoading && !error && (
        <div className="absolute top-4 right-4 flex flex-col gap-2 z-20">
          <button
            onClick={toggleSlicing}
            className={`p-2 rounded-lg transition-colors shadow-lg ${
              slicingEnabled ? 'bg-blue-600 hover:bg-blue-500' : 'bg-gray-700 hover:bg-gray-600'
            }`}
            title="Toggle Slicing"
          >
            <Scissors className="w-5 h-5 text-white" />
          </button>
          <button
            onClick={handleResetView}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors shadow-lg"
            title="Reset View"
          >
            <Maximize2 className="w-5 h-5 text-white" />
          </button>
          <button
            onClick={handleZoomIn}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors shadow-lg"
            title="Zoom In"
          >
            <ZoomIn className="w-5 h-5 text-white" />
          </button>
          <button
            onClick={handleZoomOut}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors shadow-lg"
            title="Zoom Out"
          >
            <ZoomOut className="w-5 h-5 text-white" />
          </button>
        </div>
      )}

      {!isLoading && !error && slicingEnabled && (
        <div className="absolute bottom-16 left-4 bg-gray-800 p-3 rounded-lg shadow-lg z-20">
          <div className="text-xs text-gray-300 mb-2 font-semibold">Section View</div>
          <div className="flex gap-2 mb-3">
            <button
              onClick={() => setSliceAxis('x')}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                sliceAxis === 'x' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              X
            </button>
            <button
              onClick={() => setSliceAxis('y')}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                sliceAxis === 'y' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              Y
            </button>
            <button
              onClick={() => setSliceAxis('z')}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                sliceAxis === 'z' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              Z
            </button>
          </div>
          <input
            type="range"
            min="0"
            max="100"
            value={slicePosition}
            onChange={(e) => setSlicePosition(Number(e.target.value))}
            className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
          />
          <div className="text-xs text-gray-400 mt-1 text-center">
            {sliceAxis.toUpperCase()}: {slicePosition}%
          </div>
        </div>
      )}

      {!isLoading && !error && (
        <div className="absolute bottom-4 left-4 text-xs text-gray-400 bg-gray-800 px-3 py-2 rounded-lg shadow-lg z-20">
          Left-click: Rotate | Right-click: Pan | Scroll: Zoom
        </div>
      )}
    </div>
  )
}

export default VtkViewer
