import { useEffect, useRef, useState } from 'react'
import { RotateCw, ZoomIn, ZoomOut, Maximize2 } from 'lucide-react'

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

const VtkViewer = ({ fileContent, filename }) => {
  const containerRef = useRef(null)
  const fullScreenRendererRef = useRef(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)

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
            break
          case 'vtp':
          case 'vtu':
          case 'vti':
          case 'vts':
          case 'vtr':
            reader = vtkXMLPolyDataReader.newInstance()
            break
          case 'stl':
            reader = vtkSTLReader.newInstance()
            break
          case 'obj':
            reader = vtkOBJReader.newInstance()
            break
          case 'ply':
            reader = vtkPLYReader.newInstance()
            break
          default:
            throw new Error(`Unsupported file format: ${extension}`)
        }

        // Parse the data
        reader.parseAsArrayBuffer(arrayBuffer)

        // Create mapper
        const mapper = vtkMapper.newInstance()
        mapper.setInputConnection(reader.getOutputPort())

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

      {!isLoading && !error && (
        <div className="absolute bottom-4 left-4 text-xs text-gray-400 bg-gray-800 px-3 py-2 rounded-lg shadow-lg z-20">
          Left-click: Rotate | Right-click: Pan | Scroll: Zoom
        </div>
      )}
    </div>
  )
}

export default VtkViewer
