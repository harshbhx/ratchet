import { useEffect, useRef } from "react";
import { useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * The hero's ambient wash: two soft bands of light drift across the top of the page in the story's two colours, the
 * student's blue and the teacher's orange. An original WebGL2 fragment shader (value noise, no library), after the
 * idea of React Bits' Aurora background. It draws only while the hero is on screen and the tab is visible, stops under
 * reduced motion (one still frame), caps the pixel ratio, and falls back to a CSS gradient without WebGL2. The colours
 * are read from the design tokens, so the theme toggle recolours it.
 */

const VERT = `#version 300 es
in vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }`;

const FRAG = `#version 300 es
precision highp float;
uniform vec2 uRes;
uniform float uT;
uniform vec3 uA;
uniform vec3 uB;
uniform float uLight;
uniform float uFade;
out vec4 o;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int k = 0; k < 4; k++) {
    v += a * noise(p);
    p = p * 2.03 + vec2(7.3, 1.7);
    a *= 0.5;
  }
  return v;
}

void main() {
  float x = (gl_FragCoord.x - uRes.x * 0.5) / uRes.y;
  float y = 1.0 - gl_FragCoord.y / uRes.y;
  float u = gl_FragCoord.x / uRes.x;
  float t = uT * 0.045;
  // Two curtains hanging from the top: a wide slow one and a narrower one that drifts the other way.
  float w1 = fbm(vec2(x * 1.05 + t, t * 0.6));
  float w2 = fbm(vec2(x * 1.9 - t * 0.7 + 3.1, t * 0.45 + 5.2));
  float hem1 = 0.10 + 0.30 * w1;
  float hem2 = 0.04 + 0.34 * w2;
  float body1 = smoothstep(hem1 - 0.12, hem1 + 0.2, y) * (1.0 - smoothstep(hem1 + 0.2, hem1 + 0.85, y));
  float body2 = smoothstep(hem2 - 0.08, hem2 + 0.14, y) * (1.0 - smoothstep(hem2 + 0.14, hem2 + 0.6, y));
  // Vertical folds, so it reads as a curtain of light rather than a cloud.
  float fold1 = 0.55 + 0.45 * fbm(vec2(x * 7.0 + t * 0.35, y * 0.6 + 1.7));
  float fold2 = 0.6 + 0.4 * fbm(vec2(x * 11.0 - t * 0.25 + 4.0, y * 0.8));
  float a = clamp(body1 * fold1 + body2 * fold2 * 0.6, 0.0, 1.0);
  // One colour per pixel: blue on the left, orange on the right, the seam moved by the noise. Never both at once,
  // so the two never muddy where they meet.
  float seam = clamp(0.5 + x * 0.9 + 0.35 * (w2 - 0.5), 0.0, 1.0);
  vec3 col = mix(uA, uB, seam);
  col = mix(col, vec3(1.0), uLight * 0.3);
  // The edges fade out so the hero's grid shows through, and a light surface takes a fainter wash.
  float edge = smoothstep(0.0, 0.16, u) * smoothstep(0.0, 0.16, 1.0 - u);
  float alpha = a * (uLight > 0.5 ? 0.5 : 0.85) * uFade * edge;
  o = vec4(col * alpha, alpha);
}`;

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

/** A `#rrggbb` token as linear-ish RGB in 0..1 (the shader works in sRGB, which is fine for a soft wash). */
function rgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return [0.2, 0.5, 0.9];
  const n = parseInt(m[1], 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function tokens(): { a: [number, number, number]; b: [number, number, number]; light: boolean } {
  const cs = getComputedStyle(document.documentElement);
  return {
    a: rgb(cs.getPropertyValue("--owned") || "#2a78d6"),
    b: rgb(cs.getPropertyValue("--rented") || "#eb6834"),
    light: !document.documentElement.classList.contains("dark"),
  };
}

export function Aurora({ className }: { className?: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reduce = useReducedMotion() ?? false;

  useEffect(() => {
    const host = ref.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;
    const gl = canvas.getContext("webgl2", { alpha: true, premultipliedAlpha: true, antialias: false, depth: false, stencil: false });
    if (!gl) {
      host.classList.add("aurora-fallback");
      return;
    }
    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    const program = gl.createProgram();
    if (!vs || !fs || !program) {
      host.classList.add("aurora-fallback");
      return;
    }
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.bindAttribLocation(program, 0, "p");
    gl.linkProgram(program);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      gl.deleteProgram(program);
      host.classList.add("aurora-fallback");
      return;
    }
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.useProgram(program);
    const u = {
      res: gl.getUniformLocation(program, "uRes"),
      t: gl.getUniformLocation(program, "uT"),
      a: gl.getUniformLocation(program, "uA"),
      b: gl.getUniformLocation(program, "uB"),
      light: gl.getUniformLocation(program, "uLight"),
      fade: gl.getUniformLocation(program, "uFade"),
    };

    let colors = tokens();
    let clock = 0;
    let fade = 0;
    let last = 0;
    let raf = 0;
    let onScreen = true;

    const render = () => {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(u.res, canvas.width, canvas.height);
      gl.uniform1f(u.t, clock);
      gl.uniform3f(u.a, ...colors.a);
      gl.uniform3f(u.b, ...colors.b);
      gl.uniform1f(u.light, colors.light ? 1 : 0);
      gl.uniform1f(u.fade, fade);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    const frame = (now: number) => {
      raf = 0;
      if (!onScreen || document.hidden) return;
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60;
      last = now;
      if (reduce) {
        // One still frame, fully faded in: the wash is there, it just does not move.
        clock = 40;
        fade = 1;
        render();
        return;
      }
      clock += dt;
      fade = Math.min(1, fade + dt / 1.8);
      render();
      raf = requestAnimationFrame(frame);
    };
    const wake = () => {
      if (!raf && onScreen && !document.hidden) {
        last = 0;
        raf = requestAnimationFrame(frame);
      }
    };
    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.max(1, Math.round(host.clientWidth * ratio));
      canvas.height = Math.max(1, Math.round(host.clientHeight * ratio));
      render();
      wake();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(host);
    const io = new IntersectionObserver((entries) => {
      onScreen = entries.some((e) => e.isIntersecting);
      if (onScreen) wake();
    });
    io.observe(host);
    const onVisibility = () => wake();
    document.addEventListener("visibilitychange", onVisibility);
    // The theme toggle flips the `dark` class on <html>; re-read the tokens when it does.
    const mo = new MutationObserver(() => {
      colors = tokens();
      render();
      wake();
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    resize();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      mo.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      gl.deleteProgram(program);
      gl.deleteBuffer(buf);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    };
  }, [reduce]);

  return (
    <div ref={ref} aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)}>
      <canvas ref={canvasRef} className="block size-full" />
    </div>
  );
}

export default Aurora;
