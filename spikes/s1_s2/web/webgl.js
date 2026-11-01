// Instanced cube field for the WebGL2 load test (no deps).
function makeCubeField(gl, count) {
  const vs = `#version 300 es
  layout(location=0) in vec3 aPos;
  layout(location=1) in vec3 aNrm;
  layout(location=2) in vec4 aInst;   // xyz offset, w phase
  uniform mat4 uVp; uniform float uT;
  out vec3 vN; out float vP;
  mat3 rot(float a){ float c=cos(a), s=sin(a); return mat3(c,0.,-s, 0.,1.,0., s,0.,c) * mat3(1.,0.,0., 0.,c,s, 0.,-s,c); }
  void main(){
    mat3 r = rot(uT * 0.9 + aInst.w);
    vec3 p = r * (aPos * 0.35) + aInst.xyz;
    p.y += sin(uT * 1.3 + aInst.w) * 0.6;
    vN = r * aNrm; vP = aInst.w;
    gl_Position = uVp * vec4(p, 1.0);
  }`;
  const fs = `#version 300 es
  precision mediump float;
  in vec3 vN; in float vP; out vec4 o;
  void main(){
    vec3 n = normalize(vN);
    float d = max(dot(n, normalize(vec3(0.4,0.8,0.6))), 0.0);
    vec3 base = mix(vec3(0.93,0.56,0.25), vec3(0.18,0.44,0.67), fract(vP * 0.1591));
    o = vec4(base * (0.2 + 0.8 * d), 1.0);
  }`;
  function sh(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));

  // Cube: 6 faces x 2 tris, with normals.
  const P = [], N = [];
  const faces = [[0,0,1],[0,0,-1],[1,0,0],[-1,0,0],[0,1,0],[0,-1,0]];
  for (const f of faces) {
    const [nx, ny, nz] = f;
    const u = nx ? [0,1,0] : [1,0,0];
    const v = [ny*nz - nz*ny, nz*nx - nx*nz, nx*ny - ny*nx];
    // cross(n, u)
    const w = [ny*u[2]-nz*u[1], nz*u[0]-nx*u[2], nx*u[1]-ny*u[0]];
    const corners = [[-1,-1],[1,-1],[1,1],[-1,-1],[1,1],[-1,1]];
    for (const [a, b] of corners) {
      P.push(nx + a*u[0] + b*w[0], ny + a*u[1] + b*w[1], nz + a*u[2] + b*w[2]);
      N.push(nx, ny, nz);
    }
  }
  const inst = new Float32Array(count * 4);
  const side = Math.ceil(Math.cbrt(count));
  for (let i = 0; i < count; i++) {
    inst[i*4+0] = (i % side - side / 2) * 1.2;
    inst[i*4+1] = (Math.floor(i / side) % side - side / 2) * 1.2;
    inst[i*4+2] = (Math.floor(i / (side * side)) - side / 2) * 1.2;
    inst[i*4+3] = i * 0.37;
  }
  const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
  const bp = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bp); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(P), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
  const bn = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bn); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(N), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
  const bi = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bi); gl.bufferData(gl.ARRAY_BUFFER, inst, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 4, gl.FLOAT, false, 0, 0); gl.vertexAttribDivisor(2, 1);
  gl.bindVertexArray(null);
  const uVp = gl.getUniformLocation(prog, "uVp"), uT = gl.getUniformLocation(prog, "uT");

  function perspective(fov, aspect, near, far) {
    const f = 1 / Math.tan(fov / 2);
    return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, 2 * far * near / (near - far), 0];
  }
  function mul(a, b) {
    const o = new Array(16).fill(0);
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) for (let k = 0; k < 4; k++) o[c*4+r] += a[k*4+r] * b[c*4+k];
    return o;
  }
  function lookZ(dist, yaw) {
    const c = Math.cos(yaw), s = Math.sin(yaw);
    // rotate around Y then translate back
    return [c,0,-s,0, 0,1,0,0, s,0,c,0, 0,0,-dist,1];
  }
  return {
    draw(t, w, h) {
      gl.viewport(0, 0, w, h);
      gl.enable(gl.DEPTH_TEST);
      gl.clearColor(0.97, 0.95, 0.91, 1);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.useProgram(prog);
      const vp = mul(perspective(0.9, w / h, 0.1, 200), lookZ(side * 1.6, t * 0.2));
      gl.uniformMatrix4fv(uVp, false, new Float32Array(vp));
      gl.uniform1f(uT, t);
      gl.bindVertexArray(vao);
      gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, count);
      gl.bindVertexArray(null);
    },
  };
}
