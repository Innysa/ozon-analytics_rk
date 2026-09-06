import { MouseEvent as ReactMouseEvent, useEffect, useRef, useState } from "react";

// Resizable columns (drag the right edge of a header cell, like a
// spreadsheet) for wide data tables — used by the advertising daily
// statistics table and the "Позиции в поиске" table, where product/campaign
// names and SKUs otherwise get clipped/wrapped awkwardly at a fixed width.
// Session-only state (not persisted) — deliberately simple.
export function useResizableColumns(defaults: number[]) {
  const [widths, setWidths] = useState<number[]>(defaults);
  const drag = useRef<{ index: number; startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!drag.current) return;
      const { index, startX, startWidth } = drag.current;
      const next = Math.max(50, startWidth + (e.clientX - startX));
      setWidths((prev) => {
        if (prev[index] === next) return prev;
        const copy = [...prev];
        copy[index] = next;
        return copy;
      });
    };
    const onUp = () => {
      drag.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startResize = (index: number) => (e: ReactMouseEvent) => {
    e.preventDefault();
    drag.current = { index, startX: e.clientX, startWidth: widths[index] };
  };

  return { widths, startResize };
}
