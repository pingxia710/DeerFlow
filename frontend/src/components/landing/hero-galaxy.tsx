"use client";

import { useEffect, useState } from "react";

import Galaxy from "@/components/ui/galaxy";

const GALAXY_CONTEXT_ATTRIBUTES: WebGLContextAttributes = {
  alpha: true,
  depth: true,
  stencil: false,
  antialias: false,
  premultipliedAlpha: false,
  preserveDrawingBuffer: false,
  powerPreference: "default",
};

function supportsGalaxyWebGL() {
  const canvas = document.createElement("canvas");
  const preventContextCreationError = (event: Event) => event.preventDefault();
  canvas.addEventListener(
    "webglcontextcreationerror",
    preventContextCreationError,
  );

  let context: WebGLRenderingContext | WebGL2RenderingContext | null = null;
  try {
    context =
      canvas.getContext("webgl2", GALAXY_CONTEXT_ATTRIBUTES) ??
      canvas.getContext("webgl", GALAXY_CONTEXT_ATTRIBUTES);
  } catch {
    return false;
  } finally {
    canvas.removeEventListener(
      "webglcontextcreationerror",
      preventContextCreationError,
    );
  }

  context?.getExtension("WEBGL_lose_context")?.loseContext();
  return context !== null;
}

export function HeroGalaxy() {
  const [supported, setSupported] = useState(false);

  useEffect(() => {
    setSupported(supportsGalaxyWebGL());
  }, []);

  if (!supported) {
    return null;
  }

  return (
    <Galaxy
      mouseRepulsion={false}
      starSpeed={0.2}
      density={0.6}
      glowIntensity={0.35}
      twinkleIntensity={0.3}
      speed={0.5}
    />
  );
}
