# Runs Page Flicker Fix

## Problem
The Runs page was flickering every 4 seconds due to auto-refresh triggering unnecessary state updates.

## Root Cause
In [ProjectJobsPage.jsx](frontend/src/pages/ProjectJobsPage.jsx#L215-L230):
- `loadPageData()` was called every 4 seconds by the interval
- The `finally` block called `setLoading(false)` on **every refresh cycle**, even when data hadn't changed
- This state update caused a re-render → **visible flicker**

```javascript
// BEFORE: setLoading called on every refresh
finally {
  if (!cancelled) {
    setLoading(false);  // ← Called every 4 seconds = flicker
  }
}
```

## Solution
Changed to only set `loading = false` on the initial load, not on interval refreshes:

```javascript
// AFTER: setLoading only called once on initial load
let isInitialLoad = true;

// ... in finally block:
finally {
  if (!cancelled && isInitialLoad) {
    setLoading(false);
    isInitialLoad = false;  // ← Only happens once
  }
}
```

## Why This Works
1. **Initial load**: User sees loading spinner while data fetches
2. **Interval refresh**: Data updates in background without changing `loading` state
3. **Result**: No flicker, smooth background updates every 4 seconds

## Files Changed
- [frontend/src/pages/ProjectJobsPage.jsx](frontend/src/pages/ProjectJobsPage.jsx#L197-L233)

## Build Status
✅ Frontend build succeeds with no errors

## Next Steps
- Test the page at `/jobs` and verify no flicker on page load or during 4-second refreshes
- Consider if 4-second refresh interval is still desired; could be increased to reduce network load
