// The image view's channel display (#171): the decoded image's texture (premultiplied,
// as Qt Quick uploads it) as one channel in greyscale, a mix of colour channels, or over
// a checkerboard by its alpha. Uniforms come from views/channels.py ChannelView.uniforms.
// Compiled to channels.frag.qsb by tools/build_qml_shaders.py (commit both).
#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 mask;      // the colour channels kept (rgb), alpha shown (a)
    vec4 pick;      // the channel shown as greyscale (grey > 0.5)
    float grey;
    float checker;  // composite over the checkerboard by the alpha
    vec2 cells;     // checkerboard cells across the item
    vec4 dark;
    vec4 light;
};
layout(binding = 1) uniform sampler2D source;

void main() {
    vec4 t = texture(source, qt_TexCoord0);
    float a = t.a;
    vec3 c = a > 0.0 ? t.rgb / a : vec3(0.0);
    vec3 col = grey > 0.5 ? vec3(dot(vec4(c, a), pick)) : c * mask.rgb;
    vec2 cell = floor(qt_TexCoord0 * cells);
    vec3 board = mix(dark.rgb, light.rgb, mod(cell.x + cell.y, 2.0));
    vec3 outc = checker > 0.5 ? mix(board, col, a) : col;
    fragColor = vec4(outc, 1.0) * qt_Opacity;
}
