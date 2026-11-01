#version 440
// Rounded thumbnail tile with an optional selection / hover ring, anti-aliased by a
// rounded-rect signed distance in device pixels. Output is premultiplied alpha.
layout(location = 0) in vec2 v_uv;
layout(location = 1) in vec2 v_local;
layout(location = 2) in vec4 v_extra;   // state, alpha, w, h (units)

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
} ubuf;

layout(binding = 1) uniform sampler2D atlas;

void main()
{
    vec2 sizePx = v_extra.zw * ubuf.scale;
    vec2 halfPx = sizePx * 0.5;
    vec2 p = v_local * sizePx - halfPx;
    float r = min(ubuf.radius, min(halfPx.x, halfPx.y));
    vec2 q = abs(p) - (halfPx - vec2(r));
    float d = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - r;   // < 0 inside
    float cover = clamp(0.5 - d, 0.0, 1.0);

    float kind = floor(v_extra.x + 1e-3);
    float ring;
    vec4 base;
    if (kind >= 3.0) {
        base = kind >= 4.0 ? ubuf.failed : ubuf.placeholder;
        ring = floor(fract(v_extra.x) * 4.0 + 0.5);
    } else {
        base = texture(atlas, v_uv);
        ring = kind;
    }
    vec3 rgb = base.rgb;
    if (ring > 0.5) {
        vec4 rc = ring > 1.5 ? ubuf.hover : ubuf.selection;
        float bw = max(ubuf.border, 1.0);
        float inside = -d;                                   // px from the edge inwards
        float ringCover = clamp(bw - inside + 0.5, 0.0, 1.0); // 1 within the border band
        rgb = mix(rgb, rc.rgb, ringCover * rc.a);
    }
    float a = base.a * cover * v_extra.y * ubuf.qt_Opacity;
    fragColor = vec4(rgb * a, a);
}
