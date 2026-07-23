"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import { AuroraText } from "./aurora-text";

interface WordRotateProps {
  words: string[];
  duration?: number;
  className?: string;
}

/**
 * Rotating word with a plain CSS blur/fade transition.
 * The previous framer-motion variant could stay frozen at its SSR initial
 * state (opacity: 0) when the animation engine did not pick the element up;
 * this version only needs React state + CSS transitions.
 */
export function WordRotate({
  words,
  duration = 2200,
  className,
}: WordRotateProps) {
  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (words.length <= 1) return;
    const id = setInterval(() => {
      setVisible(false);
    }, duration);
    return () => clearInterval(id);
  }, [duration, words.length]);

  useEffect(() => {
    if (visible) return;
    const id = setTimeout(() => {
      setIndex((prev) => (prev + 1) % words.length);
      setVisible(true);
    }, 300);
    return () => clearTimeout(id);
  }, [visible, words.length]);

  return (
    <div className="overflow-hidden py-2">
      <span
        className={cn(
          "inline-block transition-all duration-300 ease-out",
          visible
            ? "translate-y-0 opacity-100 blur-none"
            : "translate-y-3 opacity-0 blur-md",
          className,
        )}
      >
        <AuroraText
          speed={3}
          colors={[
            "oklch(0.78 0.14 250)",
            "oklch(0.68 0.16 250)",
            "oklch(0.58 0.15 250)",
          ]}
        >
          {words[index]}
        </AuroraText>
      </span>
    </div>
  );
}
