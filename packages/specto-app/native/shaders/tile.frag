#version 440
// Rounded thumbnail tile with an optional selection / hover ring, anti-aliased by a
// rounded-rect signed distance in device pixels. Output is premultiplied alpha.
// The overlay captions (SPEC-6) read on light and dark images through a scrim blended
// over the thumb's bottom band (scrimPx, then a fade above it, the two within scrimShare
// of the thumb's height), on the tiles that carry a caption only; the current item gets a
// bar along its bottom edge, drawn over the ring so the two marks stay apart.
layout(location = 0) in vec2 v_uv;
layout(location = 1) in vec2 v_local;
layout(location = 2) flat in vec4 v_extra;   // bits, alpha, w, h (units): constant per tile

// a tile's flags: mirror TileBits in gallerynode.h (test_gallery_native checks they agree)
#define TILE_PLACEHOLDER 1
#define TILE_FAILED 2
#define TILE_KIND_MASK 3
#define TILE_SELECTED 4
#define TILE_HOVER 8
#define TILE_CURRENT 16
#define TILE_CAPTION 32

layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float scale;
    float radius;
    float border;
    vec4 selection;
    vec4 hover;
    vec4 placeholder;
    vec4 failed;
    vec4 current;
    vec4 scrim;
    float scrimPx;
    float barPx;
    float scrimShare;
} ubuf;

layout(binding = 1) uniform sampler2D atlas;

// `src` (straight alpha, times `k`) over a premultiplied colour
vec4 over(vec4 dst, vec4 src, float k)
{
    float a = clamp(src.a * k, 0.0, 1.0);
    return vec4(src.rgb * a, a) + dst * (1.0 - a);
}

void main()
{
    vec2 sizePx = v_extra.zw * ubuf.scale;
    vec2 halfPx = sizePx * 0.5;
    vec2 p = v_local * sizePx - halfPx;
    float r = min(ubuf.radius, min(halfPx.x, halfPx.y));
    vec2 q = abs(p) - (halfPx - vec2(r));
    float d = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - r;   // < 0 inside
    float cover = clamp(0.5 - d, 0.0, 1.0);

    int bits = int(v_extra.x + 0.5);
    int kind = bits & TILE_KIND_MASK;
    vec4 base;
    if (kind == TILE_FAILED)
        base = ubuf.failed;
    else if (kind == TILE_PLACEHOLDER)
        base = ubuf.placeholder;
    else
        base = texture(atlas, v_uv);
    vec4 c = vec4(base.rgb * base.a, base.a);   // premultiplied from here on
    float fromBottom = (1.0 - v_local.y) * sizePx.y;   // device px above the bottom edge
    if ((bits & TILE_CAPTION) != 0 && ubuf.scrimPx > 0.0) {
        float fade = clamp(ubuf.scrimShare * sizePx.y - ubuf.scrimPx, 1.0, max(ubuf.scrimPx, 1.0));
        float k = 1.0 - smoothstep(ubuf.scrimPx, ubuf.scrimPx + fade, fromBottom);
        c = over(c, ubuf.scrim, k);
    }
    if ((bits & (TILE_SELECTED | TILE_HOVER)) != 0) {
        vec4 rc = (bits & TILE_HOVER) != 0 ? ubuf.hover : ubuf.selection;
        float bw = max(ubuf.border, 1.0);
        float inside = -d;                                   // px from the edge inwards
        float ringCover = clamp(bw - inside + 0.5, 0.0, 1.0); // 1 within the border band
        c = over(c, rc, ringCover);
    }
    if ((bits & TILE_CURRENT) != 0)
        c = over(c, ubuf.current, clamp(ubuf.barPx - fromBottom + 0.5, 0.0, 1.0));
    fragColor = c * (cover * v_extra.y * ubuf.qt_Opacity);
}
