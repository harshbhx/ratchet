import{_ as e,a as t,m as n,p as r}from"./utils-CjFVJpAU.js";import{t as i}from"./use-reduced-motion-DJ6DEiqk.js";var a=e(n(),1),o=r(),s=`#version 300 es
in vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }`,c=`#version 300 es
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
}`;function l(e,t,n){let r=e.createShader(t);return r?(e.shaderSource(r,n),e.compileShader(r),e.getShaderParameter(r,e.COMPILE_STATUS)?r:(e.deleteShader(r),null)):null}function u(e){let t=/^#?([0-9a-f]{6})$/i.exec(e.trim());if(!t)return[.2,.5,.9];let n=parseInt(t[1],16);return[(n>>16&255)/255,(n>>8&255)/255,(n&255)/255]}function d(){let e=getComputedStyle(document.documentElement);return{a:u(e.getPropertyValue(`--owned`)||`#2a78d6`),b:u(e.getPropertyValue(`--rented`)||`#eb6834`),light:!document.documentElement.classList.contains(`dark`)}}function f({className:e}){let n=(0,a.useRef)(null),r=(0,a.useRef)(null),u=i()??!1;return(0,a.useEffect)(()=>{let e=n.current,t=r.current;if(!e||!t)return;let i=t.getContext(`webgl2`,{alpha:!0,premultipliedAlpha:!0,antialias:!1,depth:!1,stencil:!1});if(!i){e.classList.add(`aurora-fallback`);return}let a=l(i,i.VERTEX_SHADER,s),o=l(i,i.FRAGMENT_SHADER,c),f=i.createProgram();if(!a||!o||!f){e.classList.add(`aurora-fallback`);return}if(i.attachShader(f,a),i.attachShader(f,o),i.bindAttribLocation(f,0,`p`),i.linkProgram(f),i.deleteShader(a),i.deleteShader(o),!i.getProgramParameter(f,i.LINK_STATUS)){i.deleteProgram(f),e.classList.add(`aurora-fallback`);return}let p=i.createBuffer();i.bindBuffer(i.ARRAY_BUFFER,p),i.bufferData(i.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),i.STATIC_DRAW),i.enableVertexAttribArray(0),i.vertexAttribPointer(0,2,i.FLOAT,!1,0,0),i.useProgram(f);let m={res:i.getUniformLocation(f,`uRes`),t:i.getUniformLocation(f,`uT`),a:i.getUniformLocation(f,`uA`),b:i.getUniformLocation(f,`uB`),light:i.getUniformLocation(f,`uLight`),fade:i.getUniformLocation(f,`uFade`)},h=d(),g=0,_=0,v=0,y=0,b=!0,x=()=>{i.viewport(0,0,t.width,t.height),i.uniform2f(m.res,t.width,t.height),i.uniform1f(m.t,g),i.uniform3f(m.a,...h.a),i.uniform3f(m.b,...h.b),i.uniform1f(m.light,+!!h.light),i.uniform1f(m.fade,_),i.drawArrays(i.TRIANGLES,0,3)},S=e=>{if(y=0,!b||document.hidden)return;let t=v?Math.min(.05,(e-v)/1e3):1/60;v=e,u?(g=40,_=1,x()):(g+=t,_=Math.min(1,_+t/1.8),x(),y=requestAnimationFrame(S))},C=()=>{!y&&b&&!document.hidden&&(v=0,y=requestAnimationFrame(S))},w=()=>{let n=Math.min(window.devicePixelRatio||1,1.5);t.width=Math.max(1,Math.round(e.clientWidth*n)),t.height=Math.max(1,Math.round(e.clientHeight*n)),x(),C()},T=new ResizeObserver(w);T.observe(e);let E=new IntersectionObserver(e=>{b=e.some(e=>e.isIntersecting),b&&C()});E.observe(e);let D=()=>C();document.addEventListener(`visibilitychange`,D);let O=new MutationObserver(()=>{h=d(),x(),C()});return O.observe(document.documentElement,{attributes:!0,attributeFilter:[`class`]}),w(),()=>{cancelAnimationFrame(y),T.disconnect(),E.disconnect(),O.disconnect(),document.removeEventListener(`visibilitychange`,D),i.deleteProgram(f),i.deleteBuffer(p),i.getExtension(`WEBGL_lose_context`)?.loseContext()}},[u]),(0,o.jsx)(`div`,{ref:n,"aria-hidden":!0,className:t(`pointer-events-none absolute inset-0 overflow-hidden`,e),children:(0,o.jsx)(`canvas`,{ref:r,className:`block size-full`})})}export{f as Aurora,f as default};