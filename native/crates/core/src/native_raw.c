/* Layout-only bridge compiled against pinned LibRaw 0.22.2 headers.
 * No LibRaw allocations are passed to Rust's allocator, and no library struct
 * layout is guessed in Rust. C API calls remain dynamically linked in Rust. */
#include <stdint.h>
#include <string.h>
#include "libraw/libraw.h"

_Static_assert(LIBRAW_MAJOR_VERSION == 0 && LIBRAW_MINOR_VERSION == 22 && LIBRAW_PATCH_VERSION == 2,
               "The runtime ABI guard and pinned headers must be updated together");

typedef struct {
    uint32_t raw_width, raw_height, width, height, left, top;
    uint32_t crop_left, crop_top, crop_width, crop_height;
    int32_t flip;
    uint32_t raw_bits;
    double aspect;
    unsigned char model[64];
} z30_raw_info;

typedef struct {
    uint32_t width, height, colors, bits, bitmap;
    uint64_t bytes;
    const unsigned char *pixels;
} z30_raw_view;

size_t z30_raw_info_size(void) { return sizeof(z30_raw_info); }
size_t z30_raw_view_size(void) { return sizeof(z30_raw_view); }

void z30_raw_options(void *context, int half, int wb) {
    libraw_data_t *raw = (libraw_data_t *)context;
    raw->params.output_bps = 16;
    raw->params.gamm[0] = 1.; raw->params.gamm[1] = 1.;
    raw->params.no_auto_bright = 1;
    raw->params.no_auto_scale = 0;
    raw->params.bright = 1.f;
    raw->params.output_color = 1; /* linear sRGB; same baseline decode contract */
    raw->params.user_flip = 0;   /* retain original sizes/flip and orient once in Rust */
    raw->params.user_qual = 3;   /* AHD, rawpy's default */
    raw->params.half_size = half;
    raw->params.use_camera_wb = wb == 0;
    raw->params.use_auto_wb = wb == 1;
    raw->params.four_color_rgb = 0;
    raw->params.highlight = 0;
    raw->rawparams.max_raw_memory_mb = 1024;
}

void z30_get_raw_info(const void *context, z30_raw_info *out) {
    const libraw_data_t *raw = (const libraw_data_t *)context;
    const libraw_image_sizes_t *s = &raw->sizes;
    memset(out, 0, sizeof(*out));
    out->raw_width=s->raw_width; out->raw_height=s->raw_height;
    out->width=s->width; out->height=s->height;
    out->left=s->left_margin; out->top=s->top_margin;
    out->crop_left=s->raw_inset_crops[0].cleft; out->crop_top=s->raw_inset_crops[0].ctop;
    out->crop_width=s->raw_inset_crops[0].cwidth; out->crop_height=s->raw_inset_crops[0].cheight;
    out->flip=s->flip; out->aspect=s->pixel_aspect; out->raw_bits=raw->color.raw_bps;
    memcpy(out->model,raw->idata.model,sizeof(out->model)-1);
}

void z30_get_raw_view(const void *memory, z30_raw_view *out) {
    const libraw_processed_image_t *image = (const libraw_processed_image_t *)memory;
    memset(out,0,sizeof(*out));
    out->width=image->width; out->height=image->height; out->colors=image->colors;
    out->bits=image->bits; out->bytes=image->data_size; out->pixels=image->data;
    out->bitmap=image->type==LIBRAW_IMAGE_BITMAP;
}
