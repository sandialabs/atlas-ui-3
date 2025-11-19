import { useEffect, useRef, useState } from 'react'
import { RotateCw, ZoomIn, ZoomOut, Maximize2, Scissors, Play, Pause } from 'lucide-react'

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
  const autoRotateIntervalRef = useRef(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [slicingEnabled, setSlicingEnabled] = useState(false)
  const [slicePosition, setSlicePosition] = useState(50)
  const [sliceAxis, setSliceAxis] = useState('z')
  const [customNormal, setCustomNormal] = useState({ x: 0, y: 0, z: 1 })
  const [isAutoRotating, setIsAutoRotating] = useState(false)

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

  const toggleAutoRotate = () => {
    setIsAutoRotating(!isAutoRotating)
  }

  // Auto-rotation effect
  useEffect(() => {
    if (!isAutoRotating || !fullScreenRendererRef.current) {
      if (autoRotateIntervalRef.current) {
        clearInterval(autoRotateIntervalRef.current)
        autoRotateIntervalRef.current = null
      }
      return
    }

    const renderer = fullScreenRendererRef.current.getRenderer()
    const renderWindow = fullScreenRendererRef.current.getRenderWindow()
    const camera = renderer.getActiveCamera()

    autoRotateIntervalRef.current = setInterval(() => {
      camera.azimuth(0.5) // Rotate 0.5 degrees per frame (slow rotation)
      renderer.resetCameraClippingRange() // Fix clipping issues during rotation
      renderWindow.render()
    }, 33) // ~30 fps

    return () => {
      if (autoRotateIntervalRef.current) {
        clearInterval(autoRotateIntervalRef.current)
        autoRotateIntervalRef.current = null
      }
    }
  }, [isAutoRotating])

  // Update custom normal when axis changes
  useEffect(() => {
    if (sliceAxis === 'x') {
      setCustomNormal({ x: 1, y: 0, z: 0 })
    } else if (sliceAxis === 'y') {
      setCustomNormal({ x: 0, y: 1, z: 0 })
    } else if (sliceAxis === 'z') {
      setCustomNormal({ x: 0, y: 0, z: 1 })
    }
  }, [sliceAxis])

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
      
      // Calculate position based on slice percentage
      let position
      if (sliceAxis === 'x') {
        position = xMin + (xMax - xMin) * (slicePosition / 100)
      } else if (sliceAxis === 'y') {
        position = yMin + (yMax - yMin) * (slicePosition / 100)
      } else { // z
        position = zMin + (zMax - zMin) * (slicePosition / 100)
      }

      // Set plane origin (at calculated position along the axis)
      const origin = [
        sliceAxis === 'x' ? position : 0,
        sliceAxis === 'y' ? position : 0,
        sliceAxis === 'z' ? position : 0
      ]
      
      plane.setOrigin(origin[0], origin[1], origin[2])
      plane.setNormal(customNormal.x, customNormal.y, customNormal.z)

      // Add clipping plane to mapper
      mapper.addClippingPlane(plane)
      
      // Important: Call modified() to trigger update
      mapper.modified()
    }

    renderWindow.render()
  }, [slicingEnabled, slicePosition, sliceAxis, customNormal])

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
            onClick={toggleAutoRotate}
            className={`p-2 rounded-lg transition-colors shadow-lg ${
              isAutoRotating ? 'bg-green-600 hover:bg-green-500' : 'bg-gray-700 hover:bg-gray-600'
            }`}
            title="Auto Rotate"
          >
            {isAutoRotating ? <Pause className="w-5 h-5 text-white" /> : <Play className="w-5 h-5 text-white" />}
          </button>
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
        <div className="absolute bottom-16 left-4 bg-gray-800 p-4 rounded-lg shadow-lg z-20 min-w-80">
          <div className="text-sm text-gray-300 mb-3 font-semibold">Section View Controls</div>
          
          {/* Preset axis buttons */}
          <div className="mb-4">
            <div className="text-xs text-gray-400 mb-2">Quick Axes</div>
            <div className="flex gap-2">
              <button
                onClick={() => setSliceAxis('x')}
                className={`px-4 py-1.5 text-xs rounded transition-colors ${
                  sliceAxis === 'x' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                X
              </button>
              <button
                onClick={() => setSliceAxis('y')}
                className={`px-4 py-1.5 text-xs rounded transition-colors ${
                  sliceAxis === 'y' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                Y
              </button>
              <button
                onClick={() => setSliceAxis('z')}
                className={`px-4 py-1.5 text-xs rounded transition-colors ${
                  sliceAxis === 'z' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                Z
              </button>
            </div>
          </div>

          {/* Custom normal direction */}
          <div className="mb-4">
            <div className="text-xs text-gray-400 mb-2">Plane Normal Direction</div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-xs text-gray-500">X</label>
                <input
                  type="number"
                  min="-1"
                  max="1"
                  step="0.1"
                  value={customNormal.x}
                  onChange={(e) => setCustomNormal({ ...customNormal, x: Number(e.target.value) })}
                  className="w-full px-2 py-1 text-xs bg-gray-700 text-gray-200 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">Y</label>
                <input
                  type="number"
                  min="-1"
                  max="1"
                  step="0.1"
                  value={customNormal.y}
                  onChange={(e) => setCustomNormal({ ...customNormal, y: Number(e.target.value) })}
                  className="w-full px-2 py-1 text-xs bg-gray-700 text-gray-200 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">Z</label>
                <input
                  type="number"
                  min="-1"
                  max="1"
                  step="0.1"
                  value={customNormal.z}
                  onChange={(e) => setCustomNormal({ ...customNormal, z: Number(e.target.value) })}
                  className="w-full px-2 py-1 text-xs bg-gray-700 text-gray-200 rounded border border-gray-600 focus:border-blue-500 focus:outline-none"
                />
              </div>
            </div>
          </div>

          {/* Position slider - larger */}
          <div className="mb-2">
            <div className="text-xs text-gray-400 mb-2">Plane Position: {slicePosition}%</div>
            <input
              type="range"
              min="0"
              max="100"
              value={slicePosition}
              onChange={(e) => setSlicePosition(Number(e.target.value))}
              className="w-full h-3 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
              style={{
                background: `linear-gradient(to right, #3b82f6 0%, #3b82f6 ${slicePosition}%, #374151 ${slicePosition}%, #374151 100%)`
              }}
            />
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
