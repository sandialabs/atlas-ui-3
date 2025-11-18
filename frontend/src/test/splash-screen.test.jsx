import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SplashScreen from '../components/SplashScreen'

describe('SplashScreen', () => {
  beforeEach(() => {
    // Clear localStorage before each test
    localStorage.clear()
    vi.clearAllMocks()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('should not render when config is null', () => {
    const { container } = render(<SplashScreen config={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('should not render when config.enabled is false', () => {
    const config = {
      enabled: false,
      title: 'Test Title',
      messages: [{ type: 'text', content: 'Test message' }]
    }
    const { container } = render(<SplashScreen config={config} />)
    expect(container.firstChild).toBeNull()
  })

  it('should render splash screen when enabled', () => {
    const config = {
      enabled: true,
      title: 'Welcome',
      messages: [{ type: 'text', content: 'Welcome to the app' }],
      dismissible: true,
      require_accept: false,
      dismiss_button_text: 'Close'
    }
    render(<SplashScreen config={config} />)
    
    expect(screen.getByText('Welcome')).toBeInTheDocument()
    expect(screen.getByText('Welcome to the app')).toBeInTheDocument()
    expect(screen.getByText('Close')).toBeInTheDocument()
  })

  it('should render multiple messages with headings and text', () => {
    const config = {
      enabled: true,
      title: 'Policies',
      messages: [
        { type: 'heading', content: 'Cookie Policy' },
        { type: 'text', content: 'We use cookies.' },
        { type: 'heading', content: 'Privacy Policy' },
        { type: 'text', content: 'Your data is secure.' }
      ],
      dismissible: true
    }
    render(<SplashScreen config={config} />)
    
    expect(screen.getByText('Cookie Policy')).toBeInTheDocument()
    expect(screen.getByText('We use cookies.')).toBeInTheDocument()
    expect(screen.getByText('Privacy Policy')).toBeInTheDocument()
    expect(screen.getByText('Your data is secure.')).toBeInTheDocument()
  })

  it('should show Accept button when require_accept is true', () => {
    const config = {
      enabled: true,
      title: 'Terms',
      messages: [{ type: 'text', content: 'Please accept the terms' }],
      require_accept: true,
      accept_button_text: 'I Accept'
    }
    render(<SplashScreen config={config} />)
    
    expect(screen.getByText('I Accept')).toBeInTheDocument()
  })

  it('should show Dismiss button when dismissible is true and require_accept is false', () => {
    const config = {
      enabled: true,
      title: 'Info',
      messages: [{ type: 'text', content: 'Some info' }],
      dismissible: true,
      require_accept: false,
      dismiss_button_text: 'Close'
    }
    render(<SplashScreen config={config} />)
    
    expect(screen.getByText('Close')).toBeInTheDocument()
  })

  it('should call onClose when dismiss button is clicked', () => {
    const onClose = vi.fn()
    const config = {
      enabled: true,
      title: 'Info',
      messages: [{ type: 'text', content: 'Some info' }],
      dismissible: true,
      require_accept: false,
      dismiss_button_text: 'Close',
      dismiss_duration_days: 30
    }
    render(<SplashScreen config={config} onClose={onClose} />)
    
    const closeButton = screen.getByText('Close')
    fireEvent.click(closeButton)
    
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('should save dismissal to localStorage when dismissed', () => {
    const config = {
      enabled: true,
      title: 'Info',
      messages: [{ type: 'text', content: 'Some info' }],
      dismissible: true,
      require_accept: false,
      dismiss_button_text: 'Close',
      dismiss_duration_days: 30
    }
    render(<SplashScreen config={config} />)
    
    const closeButton = screen.getByText('Close')
    fireEvent.click(closeButton)
    
    const dismissedData = localStorage.getItem('splash-screen-dismissed')
    expect(dismissedData).not.toBeNull()
    
    const parsed = JSON.parse(dismissedData)
    expect(parsed).toHaveProperty('timestamp')
    expect(parsed).toHaveProperty('version')
  })

  it('should save acceptance to localStorage when accepted', () => {
    const config = {
      enabled: true,
      title: 'Terms',
      messages: [{ type: 'text', content: 'Accept terms' }],
      require_accept: true,
      accept_button_text: 'I Accept',
      dismiss_duration_days: 30
    }
    render(<SplashScreen config={config} />)
    
    const acceptButton = screen.getByText('I Accept')
    fireEvent.click(acceptButton)
    
    const dismissedData = localStorage.getItem('splash-screen-dismissed')
    expect(dismissedData).not.toBeNull()
    
    const parsed = JSON.parse(dismissedData)
    expect(parsed).toHaveProperty('timestamp')
    expect(parsed).toHaveProperty('accepted', true)
  })

  it('should not render if dismissed within duration', () => {
    // Pre-populate localStorage with recent dismissal
    const dismissedData = {
      timestamp: new Date().toISOString(),
      version: 'Test'
    }
    localStorage.setItem('splash-screen-dismissed', JSON.stringify(dismissedData))
    
    const config = {
      enabled: true,
      title: 'Info',
      messages: [{ type: 'text', content: 'Should not show' }],
      dismissible: true,
      dismiss_duration_days: 30,
      show_on_every_visit: false
    }
    
    const { container } = render(<SplashScreen config={config} />)
    expect(container.firstChild).toBeNull()
  })

  it('should render if dismissed outside duration', () => {
    // Pre-populate localStorage with old dismissal (40 days ago)
    const oldDate = new Date()
    oldDate.setDate(oldDate.getDate() - 40)
    const dismissedData = {
      timestamp: oldDate.toISOString(),
      version: 'Test'
    }
    localStorage.setItem('splash-screen-dismissed', JSON.stringify(dismissedData))
    
    const config = {
      enabled: true,
      title: 'Info',
      messages: [{ type: 'text', content: 'Should show again' }],
      dismissible: true,
      dismiss_duration_days: 30,
      show_on_every_visit: false
    }
    
    render(<SplashScreen config={config} />)
    expect(screen.getByText('Should show again')).toBeInTheDocument()
  })

  it('should render every time if show_on_every_visit is true', () => {
    // Pre-populate localStorage with recent dismissal
    const dismissedData = {
      timestamp: new Date().toISOString(),
      version: 'Test'
    }
    localStorage.setItem('splash-screen-dismissed', JSON.stringify(dismissedData))
    
    const config = {
      enabled: true,
      title: 'Info',
      messages: [{ type: 'text', content: 'Always show' }],
      dismissible: true,
      dismiss_duration_days: 30,
      show_on_every_visit: true
    }
    
    render(<SplashScreen config={config} />)
    expect(screen.getByText('Always show')).toBeInTheDocument()
  })

  it('should show close button (X) when dismissible and not require_accept', () => {
    const config = {
      enabled: true,
      title: 'Info',
      messages: [{ type: 'text', content: 'Some info' }],
      dismissible: true,
      require_accept: false
    }
    render(<SplashScreen config={config} />)
    
    // Check for close button (X icon) in the header
    const closeButton = screen.getByLabelText('Close')
    expect(closeButton).toBeInTheDocument()
  })

  it('should not show close button (X) when require_accept is true', () => {
    const config = {
      enabled: true,
      title: 'Terms',
      messages: [{ type: 'text', content: 'Must accept' }],
      dismissible: true,
      require_accept: true
    }
    render(<SplashScreen config={config} />)
    
    // Close button should not be in the document
    const closeButton = screen.queryByLabelText('Close')
    expect(closeButton).not.toBeInTheDocument()
  })
})
