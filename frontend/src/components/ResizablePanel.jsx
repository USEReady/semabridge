import { useState, useCallback, useEffect } from 'react';

export default function ResizablePanel({
    leftPanel: LeftPanel,
    rightPanel: RightPanel,
    initialWidth = 280,
    minWidth = 150,
    maxWidth = 600,
    direction = 'horizontal'
}) {
    const [width, setWidth] = useState(initialWidth);
    const [isResizing, setIsResizing] = useState(false);

    const startResizing = useCallback(() => {
        setIsResizing(true);
    }, []);

    const stopResizing = useCallback(() => {
        setIsResizing(false);
    }, []);

    const resize = useCallback((mouseMoveEvent) => {
        if (isResizing) {
            const newWidth = mouseMoveEvent.clientX; // Simplified for left-side resize
            if (newWidth >= minWidth && newWidth <= maxWidth) {
                setWidth(newWidth);
            }
        }
    }, [isResizing, minWidth, maxWidth]);

    useEffect(() => {
        window.addEventListener("mousemove", resize);
        window.addEventListener("mouseup", stopResizing);
        return () => {
            window.removeEventListener("mousemove", resize);
            window.removeEventListener("mouseup", stopResizing);
        };
    }, [resize, stopResizing]);

    return (
        <div className="flex flex-1 overflow-hidden relative">
            <div style={{ width: `${width}px` }} className="shrink-0 overflow-hidden">
                {LeftPanel}
            </div>

            {/* Splitter Handle */}
            <div
                className={`w-1 cursor-col-resize hover:bg-indigo-500/50 transition-colors z-10 ${isResizing ? 'bg-indigo-500' : 'bg-white/5'
                    }`}
                onMouseDown={startResizing}
            />

            <div className="flex-1 overflow-hidden">
                {RightPanel}
            </div>
        </div>
    );
}
