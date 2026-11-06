#version 440
// GalleryItem tile batch: positions are gallery units; the node matrix maps them to px.
layout(location = 0) in vec2 pos;
layout(location = 1) in vec2 uv;
layout(location = 2) in vec2 local;
layout(location = 3) in vec4 extra;   // bits, alpha, w, h (units)

layout(location = 0) out vec2 v_uv;
layout(location = 1) out vec2 v_local;
layout(location = 2) flat out vec4 v_extra;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float scale;      // device px per gallery unit
    float radius;     // device px
    float border;     // device px
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

out gl_PerVertex { vec4 gl_Position; };

void main()
{
    v_uv = uv;
    v_local = local;
    v_extra = extra;
    gl_Position = ubuf.qt_Matrix * vec4(pos, 0.0, 1.0);
}
