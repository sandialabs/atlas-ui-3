# Animated Logo Feature

Last updated: 2026-02-27

## Overview

The animated logo replaces the static welcome screen logo with an interactive component featuring 3D tilt tracking, a floating bob animation, ambient glow, and paired energy pulse rings that radiate outward from the thunderbird icon.

## Feature Flag

Controlled by the `VITE_FEATURE_ANIMATED_LOGO` build-time flag.

| Location | Value | Notes |
|---|---|---|
| `.env.example` | `true` | Default for new setups |
| `Dockerfile` | `ARG VITE_FEATURE_ANIMATED_LOGO="true"` | Override with `--build-arg VITE_FEATURE_ANIMATED_LOGO=false` |
| `docker-compose.yml` | N/A | Build-time only, not a runtime env var |
| `test_docker_env_sync.py` | Exclusion list | Exempted since it is a build arg, not runtime |

When the flag is `false` or unset, `WelcomeScreen.jsx` renders the original static `<img>` tag.

## Component: AnimatedLogo.jsx

**Location:** `frontend/src/components/AnimatedLogo.jsx`

### Effects

1. **3D Tilt** - Tracks mouse position globally via `mousemove`. A `requestAnimationFrame` loop lerps the current tilt toward the target for smooth motion. The tilt is applied as `rotateX`/`rotateY` transforms with a CSS `perspective` container.

2. **Floating Bob** - CSS `@keyframes animated-logo-float` applies a gentle 6s vertical oscillation.

3. **Ambient Glow** - A blurred radial gradient div behind the thunderbird icon. Opacity increases on hover.

4. **Hover Scale** - The logo scales to 1.03x on mouse enter with a CSS transition.

5. **Energy Pulse Rings** - SVG circles with SMIL `<animate>` elements for radius expansion and opacity fade. Rings are spawned in pairs by a JavaScript scheduler with randomized timing:
   - **Pair gap:** 150-450ms between the two rings in a pair
   - **Pair interval:** 12-20s between pulse pairs (jittered)
   - **Position jitter:** Each ring's center is offset +/-12px from the thunderbird center
   - Rings use `begin="indefinite"` and are triggered via `beginElement()` when mounted

### Cleanup

All effects clean up properly on unmount:
- `cancelAnimationFrame` for the tilt loop
- `removeEventListener` for mouse tracking
- `clearTimeout` for all pulse scheduling timers
- `ResizeObserver.disconnect()` for dimension tracking

## CSS

Styles are added to `frontend/src/App.css`:
- `.animated-logo-container` - Relative positioning, perspective container
- `.animated-logo-glow` - Blurred radial gradient overlay
- `.animated-logo-float` - Floating keyframe animation
- `.animated-logo-scale` - Hover scale transition
