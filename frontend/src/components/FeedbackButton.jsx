import { useState } from 'react'
import { MessageCircle, X, ThumbsUp, ThumbsDown, Minus, Send } from 'lucide-react'

const FeedbackButton = () => {
  const [isOpen, setIsOpen] = useState(false)
  const [selectedRating, setSelectedRating] = useState(null)
  const [comment, setComment] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [showSuccessMessage, setShowSuccessMessage] = useState(false)

  const submitFeedback = async () => {
    if (selectedRating === null) return

    setIsSubmitting(true)
    
    try {
      // Capture session information
      const sessionInfo = {
        timestamp: new Date().toISOString(),
        userAgent: navigator.userAgent,
        url: window.location.href,
        screenResolution: `${window.screen.width}x${window.screen.height}`,
        viewportSize: `${window.innerWidth}x${window.innerHeight}`,
        language: navigator.language,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
      }

      const feedbackData = {
        rating: selectedRating,
        comment: comment.trim(),
        session: sessionInfo
      }

      const response = await fetch('/api/feedback', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(feedbackData)
      })

      if (response.ok) {
        setShowSuccessMessage(true)
        setTimeout(() => {
          setIsOpen(false)
          setSelectedRating(null)
          setComment('')
          setShowSuccessMessage(false)
        }, 2000)
      } else {
        console.error('Failed to submit feedback')
      }
    } catch (error) {
      console.error('Error submitting feedback:', error)
    } finally {
      setIsSubmitting(false)
    }
  }

  const getRatingIcon = (rating) => {
    switch (rating) {
      case -1: return <ThumbsDown className="w-4 h-4" />
      case 0: return <Minus className="w-4 h-4" />
      case 1: return <ThumbsUp className="w-4 h-4" />
      default: return null
    }
  }

  const getRatingLabel = (rating) => {
    switch (rating) {
      case -1: return 'Negative'
      case 0: return 'Neutral'
      case 1: return 'Positive'
      default: return ''
    }
  }

  const getRatingColor = (rating) => {
    switch (rating) {
      case -1: return 'text-red-400 bg-red-900/20 border-red-500'
      case 0: return 'text-yellow-400 bg-yellow-900/20 border-yellow-500'
      case 1: return 'text-green-400 bg-green-900/20 border-green-500'
      default: return 'text-gray-400 bg-gray-700 border-gray-600'
    }
  }

  return (
    <>
      {/* Feedback Button */}
      <button
        onClick={() => setIsOpen(true)}
        className="fixed bottom-4 right-4 z-40 p-3 bg-blue-600 hover:bg-blue-700 text-white rounded-full shadow-lg transition-all duration-200 hover:scale-105"
        title="Give feedback"
      >
        <MessageCircle className="w-5 h-5" />
      </button>

      {/* Feedback Overlay */}
      {isOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center p-4">
          <div className="bg-gray-800 rounded-lg w-full max-w-md border border-gray-700">
            <div className="p-6">
              {/* Header */}
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-white">Share Your Feedback</h3>
                <button
                  onClick={() => setIsOpen(false)}
                  className="text-gray-400 hover:text-white transition-colors"
                  disabled={isSubmitting}
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {showSuccessMessage ? (
                <div className="text-center py-8">
                  <div className="w-12 h-12 bg-green-600 rounded-full flex items-center justify-center mx-auto mb-4">
                    <ThumbsUp className="w-6 h-6 text-white" />
                  </div>
                  <p className="text-green-400 font-medium">Thank you for your feedback!</p>
                  <p className="text-gray-400 text-sm mt-2">We appreciate your input</p>
                </div>
              ) : (
                <>
                  {/* Rating Selection */}
                  <div className="mb-6">
                    <label className="block text-sm font-medium text-gray-300 mb-3">
                      How was your experience?
                    </label>
                    <div className="flex gap-3 justify-center">
                      {[-1, 0, 1].map((rating) => (
                        <button
                          key={rating}
                          onClick={() => setSelectedRating(rating)}
                          className={`flex flex-col items-center gap-2 p-4 rounded-lg border-2 transition-all ${
                            selectedRating === rating
                              ? getRatingColor(rating)
                              : 'text-gray-400 bg-gray-700 border-gray-600 hover:border-gray-500'
                          }`}
                          disabled={isSubmitting}
                        >
                          {getRatingIcon(rating)}
                          <span className="text-xs font-medium">{getRatingLabel(rating)}</span>
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Comment Input */}
                  <div className="mb-6">
                    <label className="block text-sm font-medium text-gray-300 mb-2">
                      Additional comments (optional)
                    </label>
                    <textarea
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder="Tell us more about your experience..."
                      className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-200 placeholder-gray-400 resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      rows={3}
                      maxLength={500}
                      disabled={isSubmitting}
                    />
                    <div className="text-xs text-gray-400 mt-1">
                      {comment.length}/500 characters
                    </div>
                  </div>

                  {/* Action Buttons */}
                  <div className="flex gap-3">
                    <button
                      onClick={() => setIsOpen(false)}
                      className="flex-1 px-4 py-2 bg-gray-600 hover:bg-gray-700 text-gray-200 rounded-lg transition-colors"
                      disabled={isSubmitting}
                    >
                      Cancel
                    </button>
                    <button
                      onClick={submitFeedback}
                      disabled={selectedRating === null || isSubmitting}
                      className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                        selectedRating !== null && !isSubmitting
                          ? 'bg-blue-600 hover:bg-blue-700 text-white'
                          : 'bg-gray-600 text-gray-400 cursor-not-allowed'
                      }`}
                    >
                      {isSubmitting ? (
                        <>
                          <div className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                          Submitting...
                        </>
                      ) : (
                        <>
                          <Send className="w-4 h-4" />
                          Submit
                        </>
                      )}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export default FeedbackButton