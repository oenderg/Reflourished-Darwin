/* ETC decoders from texture2ddecoder (MIT); see vendor/texture2ddecoder/LICENSE. */
#include "vendor/texture2ddecoder/etc.cpp"

extern "C" void pvz2_cursor_decode_etc(const unsigned char *data, int width, int height,
                                      unsigned format, unsigned char *rgba)
{
    if (format == 150) decode_etc2a8(data, width, height, (uint32_t *)rgba);
    else decode_etc1(data, width, height, (uint32_t *)rgba);
    /* The decoder emits BGRA; the atlas reader and CoreGraphics use RGBA. */
    for (size_t i = 0; i < (size_t)width * height; i++) {
        unsigned char red = rgba[i * 4 + 2];
        rgba[i * 4 + 2] = rgba[i * 4];
        rgba[i * 4] = red;
        if (format == 147) rgba[i * 4 + 3] = data[(size_t)width * height / 2 + i];
    }
}
