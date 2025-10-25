import { X, ChevronLeft, ChevronRight, Download, FileText, Image, File, Code } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useState, useEffect } from 'react'

// Helper function to process canvas content (strings and structured objects)
const processCanvasContent = (content) => {
  if (typeof content === 'string') {
    return content
  } else if (content && typeof content === 'object') {
    // Handle structured content objects that might contain markdown
    if (content.raw && typeof content.raw === 'string') {
      // If there's a raw property, use it (likely contains markdown)
      return content.raw
    } else if (content.text && typeof content.text === 'string') {
      // If there's a text property, use it
      return content.text
    } else {
      // Fallback to JSON for other objects
      try {
        return JSON.stringify(content, null, 2)
      } catch (e) {
        return String(content || '')
      }
    }
  } else {
    return String(content || '')
  }
}


const MIN_WIDTH = 300;
const MAX_WIDTH = window.innerWidth * 0.9;

const CanvasPanel = ({ isOpen, onClose, onWidthChange }) => {
  const { 
    canvasContent, 
    customUIContent, 
    canvasFiles, 
    currentCanvasFileIndex, 
    setCurrentCanvasFileIndex,
    downloadFile 
  } = useChat();
  const [isMobile, setIsMobile] = useState(false);
  const [width, setWidth] = useState(() => {
    if (window.innerWidth < 768) return window.innerWidth;
    return Math.max(400, Math.min(window.innerWidth * 0.5, window.innerWidth * 0.9));
  });
  const [isResizing, setIsResizing] = useState(false);
  const [currentFileContent, setCurrentFileContent] = useState(null);
  const [isLoadingFile, setIsLoadingFile] = useState(false);
  const [fileError, setFileError] = useState(null);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
      if (window.innerWidth < 768) {
        setWidth(window.innerWidth);
      } else {
        setWidth(Math.max(400, Math.min(window.innerWidth * 0.5, window.innerWidth * 0.9)));
      }
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Notify parent component when width changes
  useEffect(() => {
    if (onWidthChange) {
      onWidthChange(isOpen ? width : 0);
    }
  }, [width, isOpen, onWidthChange]);

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!isResizing) return;
      let newWidth = window.innerWidth - e.clientX;
      newWidth = Math.max(MIN_WIDTH, Math.min(newWidth, window.innerWidth * 0.9));
      setWidth(newWidth);
    };
    const handleMouseUp = () => setIsResizing(false);
    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    }
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing]);

  // Load current file content when canvas files or index changes
  useEffect(() => {
    const loadCurrentFile = async () => {
      if (!canvasFiles || canvasFiles.length === 0) {
        setCurrentFileContent(null);
        setFileError(null);
        return;
      }

      const currentFile = canvasFiles[currentCanvasFileIndex];
      if (!currentFile) {
        setCurrentFileContent(null);
        setFileError(null);
        return;
      }

      setIsLoadingFile(true);
      setFileError(null);

      try {
        // Fetch file content from the backend
        const response = await fetch(`/api/files/download/${currentFile.s3_key}`, {
          method: 'GET',
          credentials: 'include'
        });

        if (!response.ok) {
          throw new Error(`Failed to fetch file: ${response.statusText}`);
        }

        // Handle different file types
        if (currentFile.type === 'image') {
          const blob = await response.blob();
          const imageUrl = URL.createObjectURL(blob);
          setCurrentFileContent({ type: 'image', url: imageUrl, file: currentFile });
        } else if (currentFile.type === 'pdf') {
          const blob = await response.blob();
          const pdfUrl = URL.createObjectURL(blob);
          setCurrentFileContent({ type: 'pdf', url: pdfUrl, file: currentFile });
        } else {
          // Text-based files (HTML, text, code, etc.)
          const text = await response.text();
          setCurrentFileContent({ type: currentFile.type, content: text, file: currentFile });
        }
      } catch (error) {
        console.error('Error loading file:', error);
        setFileError(error.message);
        setCurrentFileContent(null);
      } finally {
        setIsLoadingFile(false);
      }
    };

    loadCurrentFile();
  }, [canvasFiles, currentCanvasFileIndex]);

  // Navigation functions
  const navigateToFile = (index) => {
    if (index >= 0 && index < canvasFiles.length) {
      setCurrentCanvasFileIndex(index);
    }
  };

  const goToPrevious = () => {
    navigateToFile(currentCanvasFileIndex - 1);
  };

  const goToNext = () => {
    navigateToFile(currentCanvasFileIndex + 1);
  };

  const handleDownload = () => {
    const currentFile = canvasFiles[currentCanvasFileIndex];
    if (currentFile && downloadFile) {
      downloadFile(currentFile.filename);
    }
  };

  const getFileIcon = (fileType) => {
    switch (fileType) {
      case 'image': return <Image className="w-4 h-4" />;
      case 'pdf': return <File className="w-4 h-4" />;
      case 'html': return <Code className="w-4 h-4" />;
      default: return <FileText className="w-4 h-4" />;
    }
  };

  const renderContent = () => {
    // Priority: Canvas files > Custom UI content > Canvas content > Empty state
    if (canvasFiles && canvasFiles.length > 0) {
      if (isLoadingFile) {
        return (
          <div className="flex items-center justify-center h-full text-gray-400">
            <p>Loading file...</p>
          </div>
        );
      }

      if (fileError) {
        return (
          <div className="p-4">
            <div className="text-red-400 text-center">
              Error loading file: {fileError}
            </div>
          </div>
        );
      }

      if (!currentFileContent) {
        return (
          <div className="flex items-center justify-center h-full text-gray-400">
            <p>No file content available</p>
          </div>
        );
      }

      // Render based on file type
      switch (currentFileContent.type) {
        case 'image':
          return (
            <div className="p-4">
              <div className="text-center">
                <img 
                  src={currentFileContent.url} 
                  alt={currentFileContent.file.filename}
                  className="max-w-full h-auto rounded-lg shadow-lg"
                />
              </div>
            </div>
          );

        case 'pdf':
          return (
            <div className="p-4 h-full">
              <iframe
                src={currentFileContent.url}
                className="w-full h-full border-0 rounded-lg"
                title={currentFileContent.file.filename}
              />
            </div>
          );

        case 'html':
          return (
            <div className="p-4">
              <div 
                className="prose prose-invert max-w-none"
                dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(currentFileContent.content) }}
              />
            </div>
          );

        default: // text, code, etc.
          try {
            // Try to parse as markdown first
            const markdownHtml = marked.parse(currentFileContent.content);
            const sanitizedHtml = DOMPurify.sanitize(markdownHtml);
            
            return (
              <div 
                className="prose prose-invert max-w-none p-4"
                dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
              />
            );
          } catch (error) {
            // Fallback to plain text with syntax highlighting for code files
            const fileExt = currentFileContent.file.filename.split('.').pop().toLowerCase();
            const isCodeFile = ['js', 'py', 'java', 'cpp', 'ts', 'jsx', 'tsx', 'css', 'html', 'json', 'sql'].includes(fileExt);
            
            return (
              <div className="p-4">
                <pre className={`whitespace-pre-wrap text-sm ${isCodeFile ? 'bg-gray-900 p-4 rounded-lg overflow-x-auto' : ''}`}>
                  <code className="text-gray-200">{currentFileContent.content}</code>
                </pre>
              </div>
            );
          }
      }
    }

    // Fallback to legacy custom UI content
    if (customUIContent && customUIContent.type === 'html_injection') {
      return (
        <div className="p-4">
          <div className="mb-4 text-sm text-gray-400 border-b border-gray-700 pb-2">
            Custom UI from {customUIContent.serverName} - {customUIContent.toolName}
          </div>
          <div 
            className="prose prose-invert max-w-none"
            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(customUIContent.content) }}
          />
        </div>
      )
    }
    
    if (customUIContent && customUIContent.type === 'error') {
      return (
        <div className="p-4">
          <div className="text-red-400 text-center">
            {customUIContent.content}
          </div>
        </div>
      )
    }

    // Fallback to legacy canvas content
    if (canvasContent) {
      const content = processCanvasContent(canvasContent)
      
      try {
        const markdownHtml = marked.parse(content)
        const sanitizedHtml = DOMPurify.sanitize(markdownHtml)

        return (
          <div 
            className="prose prose-invert max-w-none p-4"
            dangerouslySetInnerHTML={{ __html: sanitizedHtml }}
          />
        )
      } catch (error) {
        console.error('Error parsing canvas markdown content:', error)
        return (
          <div className="p-4 text-gray-200">
            <pre className="whitespace-pre-wrap">{content}</pre>
          </div>
        )
      }
    }

    // Empty state
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-center p-8">
        <p>Canvas content will appear here when tools generate files, visualizations, or when the AI uses the canvas tool.</p>
      </div>
    )
  }

  return (
    <aside
      className={`bg-gray-800 border-l border-gray-700 transform transition-all duration-300 ease-in-out ${isOpen ? 'flex flex-col' : 'hidden'}`}
      style={{
        width: isMobile ? '100vw' : `${width}px`,
        minWidth: isMobile ? '100vw' : `${MIN_WIDTH}px`,
        maxWidth: isMobile ? '100vw' : `${window.innerWidth * 0.9}px`,
        boxSizing: 'border-box',
      }}
    >
      {/* Draggable Divider */}
      {!isMobile && (
        <div
          style={{
            position: 'absolute',
            left: '-6px',
            top: 0,
            width: '12px',
            height: '100%',
            cursor: 'ew-resize',
            zIndex: 40,
          }}
          onMouseDown={() => setIsResizing(true)}
        >
          <div
            style={{
              width: '4px',
              height: '100%',
              margin: '0 auto',
              background: '#444',
              borderRadius: '2px',
              opacity: isResizing ? 0.8 : 0.5,
              transition: 'opacity 0.2s',
            }}
          />
        </div>
      )}
      {/* Header */}
      <div className="border-b border-gray-700 bg-gray-900">
        <div className="flex items-center justify-between p-4">
          <h2 className="text-lg font-semibold text-gray-100">Canvas</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        
        {/* File navigation */}
        {canvasFiles && canvasFiles.length > 0 && (
          <div className="px-4 pb-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm text-gray-400">
                {getFileIcon(canvasFiles[currentCanvasFileIndex]?.type)}
                <span className="truncate">
                  {canvasFiles[currentCanvasFileIndex]?.filename || 'Unknown file'}
                </span>
                {canvasFiles.length > 1 && (
                  <span className="text-xs bg-gray-700 px-2 py-1 rounded">
                    {currentCanvasFileIndex + 1} of {canvasFiles.length}
                  </span>
                )}
              </div>
              
              <div className="flex items-center gap-1">
                {/* Download button */}
                <button
                  onClick={handleDownload}
                  className="p-1.5 rounded bg-gray-700 hover:bg-gray-600 transition-colors"
                  title="Download file"
                >
                  <Download className="w-4 h-4" />
                </button>
                
                {/* Navigation buttons (only show if multiple files) */}
                {canvasFiles.length > 1 && (
                  <>
                    <button
                      onClick={goToPrevious}
                      disabled={currentCanvasFileIndex === 0}
                      className="p-1.5 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      title="Previous file"
                    >
                      <ChevronLeft className="w-4 h-4" />
                    </button>
                    <button
                      onClick={goToNext}
                      disabled={currentCanvasFileIndex === canvasFiles.length - 1}
                      className="p-1.5 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      title="Next file"
                    >
                      <ChevronRight className="w-4 h-4" />
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
      {/* Content */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        {renderContent()}
      </div>
    </aside>
  );
};

export default CanvasPanel;