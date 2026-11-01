// The canvas tile floor (#191, #197; v3 #201, design "08c Tile floor v3"): one pass at
// the area's full resolution (a texel is round(dpr) device px, TileFloor.qml); the cells
// are fixed to the screen. Each texel belongs to one cell; e is taken at the cell's
// centre, so a whole tile changes at once. Square tiles on the texture's grid:
// canvas/floor.py says why. Uniforms come from canvas/tile_floor.py. Compiled to
// floor.frag.qsb by tools/build_qml_shaders.py (commit both).
#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 texSize;   // the layer's texture, texture px
    vec4 cells;     // xy: the first cell's corner, texture px; z: cell; w: area px per texture px
    vec2 count;     // whole cells across and down
    vec4 cam;       // the canvas camera: xy the world origin, texture px; z texture px per
                    // world px (world cues only); w spare
    vec4 rest;      // a resting tile
    float dim;      // the tiles' opacity (0.3 under the wiring layer)
    // up to eight effects: g geometry (a point x, y, x, y or a rect x0, y0, x1, y1);
    // p (R, w, A), screen px; c colour, a: world space
    vec4 g0; vec4 p0; vec4 c0;
    vec4 g1; vec4 p1; vec4 c1;
    vec4 g2; vec4 p2; vec4 c2;
    vec4 g3; vec4 p3; vec4 c3;
    vec4 g4; vec4 p4; vec4 c4;
    vec4 g5; vec4 p5; vec4 c5;
    vec4 g6; vec4 p6; vec4 c6;
    vec4 g7; vec4 p7; vec4 c7;
};

// one effect at a cell centre (texture px): a triangular band A * max(0, 1 - |d - R| / w)
// about the distance d to the point or the rectangle (0 inside: a selected view's
// footprint lights the tiles it covers); the strongest term sets e and the colour
void cue(vec4 g, vec4 p, vec4 c, vec2 cc, inout float e, inout vec3 col) {
    if (p.z <= 0.0) return;
    bool world = c.a > 0.5;
    vec4 r = world ? cam.xyxy + g * cam.z : g / cells.w;
    vec2 lo = r.xy - cc;
    vec2 hi = cc - r.zw;
    float d = cells.w * length(max(max(lo, hi), 0.0));  // area px
    float ev = p.z * max(0.0, 1.0 - abs(d - p.x) / p.y);
    if (ev > e) {
        e = ev;
        col = c.rgb;
    }
}

void main() {
    vec2 t = floor(qt_TexCoord0 * texSize);
    float n = cells.z;
    vec2 cell = floor((t - cells.xy) / n);
    // whole cells only: the texels over the last one stay clear
    if (cell.x < 0.0 || cell.y < 0.0 || cell.x >= count.x || cell.y >= count.y) {
        fragColor = vec4(0.0);
        return;
    }
    vec2 corner = cells.xy + cell * n;
    vec2 cc = corner + 0.5 * n;
    float e = 0.0;
    vec3 col = rest.rgb;
    cue(g0, p0, c0, cc, e, col);
    cue(g1, p1, c1, cc, e, col);
    cue(g2, p2, c2, cc, e, col);
    cue(g3, p3, c3, cc, e, col);
    cue(g4, p4, c4, cc, e, col);
    cue(g5, p5, c5, cc, e, col);
    cue(g6, p6, c6, cc, e, col);
    cue(g7, p7, c7, cc, e, col);
    // the tile: the ladder floor(3.5 + 3 e) of an 8 px cell, 3 at rest to 6 lit
    // (canvas/floor.py tile_side; a selection's e tops out at 0.4, a 4 px tile, #220),
    // half a px up-left of centre at the odd sizes
    float side = clamp(floor(n * (3.5 + 3.0 * e) / 8.0), floor(3.0 * n / 8.0), floor(6.0 * n / 8.0));
    vec2 l = t - corner - floor(0.5 * (n - side));
    if (l.x < 0.0 || l.y < 0.0 || l.x >= side || l.y >= side) {
        fragColor = vec4(0.0);
        return;
    }
    vec3 fill = mix(rest.rgb, col, clamp(e, 0.0, 1.0));
    fragColor = vec4(fill, 1.0) * (dim * qt_Opacity);
}
