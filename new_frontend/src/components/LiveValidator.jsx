import { useEffect, useRef } from 'react';
import { api } from '../utils/api';
import { useLogs } from '../context/LogsContext';

/**
 * LiveValidator — background poller that calls /api/config/validate-live
 * every 30s and surfaces warnings/errors as toast notifications.
 *
 * Renders nothing — it only produces side-effects via LogsContext.
 */
export default function LiveValidator() {
    const { addLog } = useLogs();
    const prevIssueCount = useRef(0);

    useEffect(() => {
        let active = true;

        const poll = async () => {
            try {
                const result = await api.validateLive();
                if (!active) return;

                const totalIssues = (result.errors?.length || 0) + (result.warnings?.length || 0);

                // Only toast when *new* issues appear (avoid spamming)
                if (totalIssues > prevIssueCount.current) {
                    // Surface new warnings as toasts
                    for (const w of (result.warnings || [])) {
                        addLog('warning', `Validation: ${w.model}`, w.message);
                    }
                    // Surface new errors as toasts
                    for (const e of (result.errors || [])) {
                        addLog('error', `Validation: ${e.model}`, e.message);
                    }
                }

                prevIssueCount.current = totalIssues;
            } catch {
                // Silently retry — API might be temporarily unavailable
            }
        };

        // Initial check after 3s delay (give API time to start)
        const initialTimeout = setTimeout(poll, 3000);

        // Then poll every 30 seconds
        const interval = setInterval(poll, 30000);

        return () => {
            active = false;
            clearTimeout(initialTimeout);
            clearInterval(interval);
        };
    }, [addLog]);

    return null;
}
