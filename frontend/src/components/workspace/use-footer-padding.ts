"use client";

import { useEffect, useRef, useState } from "react";

const MIN_PADDING_BOTTOM = 160;
const EXTRA_CLEARANCE = 32;

export function useFooterPadding() {
  const footerContainerRef = useRef<HTMLDivElement | null>(null);
  const footerOverlayRef = useRef<HTMLDivElement | null>(null);
  const inputShellRef = useRef<HTMLDivElement | null>(null);
  const [paddingBottom, setPaddingBottom] = useState(MIN_PADDING_BOTTOM);

  useEffect(() => {
    const measure = () => {
      const footerContainerRect =
        footerContainerRef.current?.getBoundingClientRect();
      const inputShellRect = inputShellRef.current?.getBoundingClientRect();

      if (!footerContainerRect || !inputShellRect) {
        setPaddingBottom(MIN_PADDING_BOTTOM);
        return;
      }

      const overlayElements = footerOverlayRef.current
        ? [
            footerOverlayRef.current,
            ...Array.from(footerOverlayRef.current.querySelectorAll<HTMLElement>("*")),
          ]
        : [];
      const visibleOverlayTop = overlayElements.reduce((top, element) => {
        const rect = element.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) {
          return top;
        }
        return Math.min(top, rect.top);
      }, footerContainerRect.top);
      const overlayLift = Math.max(0, footerContainerRect.top - visibleOverlayTop);
      const nextPadding = Math.max(
        MIN_PADDING_BOTTOM,
        Math.ceil(inputShellRect.height + overlayLift + EXTRA_CLEARANCE),
      );

      setPaddingBottom((current) =>
        current === nextPadding ? current : nextPadding,
      );
    };

    measure();

    const resizeObserver = new ResizeObserver(measure);

    if (footerContainerRef.current) {
      resizeObserver.observe(footerContainerRef.current);
    }
    if (footerOverlayRef.current) {
      resizeObserver.observe(footerOverlayRef.current);
    }
    if (inputShellRef.current) {
      resizeObserver.observe(inputShellRef.current);
    }

    window.addEventListener("resize", measure);
    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  return {
    footerContainerRef,
    footerOverlayRef,
    inputShellRef,
    paddingBottom,
  };
}
