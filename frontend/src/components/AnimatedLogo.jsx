import { useState, useEffect, useRef, useCallback } from 'react'

const TILT_MAX = 5
const LERP_SPEED = 0.08

function lerp(a, b, t) {
  return a + (b - a) * t
}

function randRange(min, max) {
  return min + Math.random() * (max - min)
}

// Pulse config in viewBox coordinates (897x163)
const PULSE_CX = 89       // thunderbird center X
const PULSE_CY = 82       // thunderbird center Y
const PULSE_MAX_R = 900   // sweep past the text
const RING_DUR = 6         // seconds per ring animation
const PAIR_GAP = [150, 450]    // ms between the two rings in a pair
const PAIR_INTERVAL = [12000, 20000] // ms between pulse pairs
const POS_JITTER = 12     // +/- px position jitter per ring

const AnimatedLogo = ({ appName }) => {
  const containerRef = useRef(null)
  const imgRef = useRef(null)
  const rafRef = useRef(null)
  const currentRef = useRef({ x: 0.5, y: 0.5 })
  const targetRef = useRef({ x: 0.5, y: 0.5 })
  const ringIdRef = useRef(0)
  const startedRef = useRef(new Set())

  const [renderPos, setRenderPos] = useState({ x: 0.5, y: 0.5 })
  const [isHovering, setIsHovering] = useState(false)
  const [logoLoaded, setLogoLoaded] = useState(false)
  const [imgDims, setImgDims] = useState({ width: 0, height: 0 })
  const [activeRings, setActiveRings] = useState([])

  // Smooth animation loop using RAF
  useEffect(() => {
    let running = true
    const animate = () => {
      if (!running) return
      const cur = currentRef.current
      const tgt = targetRef.current
      const newX = lerp(cur.x, tgt.x, LERP_SPEED)
      const newY = lerp(cur.y, tgt.y, LERP_SPEED)
      if (Math.abs(newX - cur.x) > 0.0001 || Math.abs(newY - cur.y) > 0.0001) {
        currentRef.current = { x: newX, y: newY }
        setRenderPos({ x: newX, y: newY })
      }
      rafRef.current = requestAnimationFrame(animate)
    }
    rafRef.current = requestAnimationFrame(animate)
    return () => {
      running = false
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  // Track mouse position globally
  useEffect(() => {
    const handleMove = (e) => {
      if (!containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const cx = rect.left + rect.width / 2
      const cy = rect.top + rect.height / 2
      targetRef.current = {
        x: 0.5 + (e.clientX - cx) / window.innerWidth,
        y: 0.5 + (e.clientY - cy) / window.innerHeight,
      }
    }
    window.addEventListener('mousemove', handleMove)
    return () => window.removeEventListener('mousemove', handleMove)
  }, [])

  // Pulse pair scheduler - fires two rings back-to-back with jitter
  useEffect(() => {
    let mounted = true
    const timers = new Set()

    const schedule = (fn, ms) => {
      const id = setTimeout(() => {
        timers.delete(id)
        if (mounted) fn()
      }, ms)
      timers.add(id)
    }

    const spawnRing = () => {
      const id = ++ringIdRef.current
      const jx = (Math.random() - 0.5) * POS_JITTER * 2
      const jy = (Math.random() - 0.5) * POS_JITTER * 2
      setActiveRings(prev => [...prev, { id, cx: PULSE_CX + jx, cy: PULSE_CY + jy }])
      // Remove after animation completes
      schedule(() => {
        startedRef.current.delete(String(id))
        setActiveRings(prev => prev.filter(r => r.id !== id))
      }, RING_DUR * 1000 + 200)
    }

    const firePair = () => {
      spawnRing()
      schedule(spawnRing, randRange(...PAIR_GAP))
    }

    const schedulePair = () => {
      schedule(() => {
        firePair()
        schedulePair()
      }, randRange(...PAIR_INTERVAL))
    }

    // Fire first pair shortly after load
    schedule(firePair, randRange(1000, 3000))
    // Then start the recurring schedule
    schedulePair()

    return () => {
      mounted = false
      timers.forEach(clearTimeout)
    }
  }, [])

  const handleMouseEnter = useCallback(() => setIsHovering(true), [])
  const handleMouseLeave = useCallback(() => {
    setIsHovering(false)
    targetRef.current = { x: 0.5, y: 0.5 }
  }, [])

  const handleImgLoad = useCallback((e) => {
    setLogoLoaded(true)
    setImgDims({ width: e.target.offsetWidth, height: e.target.offsetHeight })
  }, [])

  // Keep dimensions in sync on resize
  useEffect(() => {
    if (!imgRef.current || typeof ResizeObserver === 'undefined') return
    const obs = new ResizeObserver(() => {
      if (imgRef.current) {
        setImgDims({
          width: imgRef.current.offsetWidth,
          height: imgRef.current.offsetHeight,
        })
      }
    })
    obs.observe(imgRef.current)
    return () => obs.disconnect()
  }, [logoLoaded])

  // Ref callback to trigger SMIL animations when a ring mounts
  const ringRefCallback = useCallback((el) => {
    if (!el) return
    const id = el.dataset.ringId
    if (startedRef.current.has(id)) return
    startedRef.current.add(id)
    const anims = el.querySelectorAll('animate')
    anims.forEach(a => {
      try { a.beginElement() } catch { /* ignore */ }
    })
  }, [])

  // Compute transforms
  const tiltX = (renderPos.x - 0.5) * TILT_MAX * 2
  const tiltY = -(renderPos.y - 0.5) * TILT_MAX * 2
  const showPulse = logoLoaded && imgDims.width > 0

  return (
    <div
      ref={containerRef}
      className="animated-logo-container"
      style={{ perspective: '800px' }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Ambient glow behind the thunderbird */}
      <div
        className="animated-logo-glow"
        style={{ opacity: isHovering ? 0.5 : 0.12 }}
      />

      {/* Float wrapper */}
      <div className="animated-logo-float">
        {/* Scale wrapper */}
        <div
          className="animated-logo-scale"
          style={{ transform: `scale(${isHovering ? 1.03 : 1})` }}
        >
          {/* Tilt wrapper */}
          <div
            style={{
              transform: `rotateY(${tiltX}deg) rotateX(${tiltY}deg)`,
              transformStyle: 'preserve-3d',
              willChange: 'transform',
            }}
          >
            {/* Image + pulse overlay wrapper */}
            <div className="relative inline-block">
              <img
                ref={imgRef}
                src="/logo.png"
                alt={`${appName} Logo`}
                className="max-w-48 sm:max-w-80 md:max-w-xl lg:max-w-3xl mx-auto object-contain"
                onLoad={handleImgLoad}
                onError={(e) => { e.target.style.display = 'none' }}
                draggable={false}
              />

              {/* Energy pulse SVG overlay - overflow visible so rings expand beyond logo */}
              {showPulse && (
                <svg
                  className="absolute top-0 left-0 pointer-events-none"
                  width={imgDims.width}
                  height={imgDims.height}
                  viewBox="0 0 897 163"
                  preserveAspectRatio="xMidYMid meet"
                  style={{ overflow: 'visible' }}
                >
                  <defs>
                    <filter id="pulse-blur" x="-50%" y="-50%" width="200%" height="200%">
                      <feGaussianBlur stdDeviation="4" />
                    </filter>
                  </defs>

                  {activeRings.map(ring => (
                    <circle
                      key={ring.id}
                      data-ring-id={ring.id}
                      cx={ring.cx}
                      cy={ring.cy}
                      r="0"
                      fill="none"
                      stroke={isHovering ? 'rgba(140, 240, 255, 0.55)' : 'rgba(100, 220, 255, 0.4)'}
                      strokeWidth={isHovering ? 8 : 5}
                      filter="url(#pulse-blur)"
                      style={{ transition: 'stroke 0.3s, stroke-width 0.3s' }}
                      ref={ringRefCallback}
                    >
                      <animate
                        attributeName="r"
                        from="0"
                        to={PULSE_MAX_R}
                        dur={`${RING_DUR}s`}
                        begin="indefinite"
                        fill="freeze"
                        calcMode="spline"
                        keySplines="0.25 0.1 0.25 1"
                        keyTimes="0;1"
                      />
                      <animate
                        attributeName="opacity"
                        values="0;0.8;0.4;0.1;0;0"
                        keyTimes="0;0.02;0.08;0.18;0.3;1"
                        dur={`${RING_DUR}s`}
                        begin="indefinite"
                        fill="freeze"
                      />
                    </circle>
                  ))}
                </svg>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default AnimatedLogo
