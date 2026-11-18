#import "pvz2-cursor-art.h"
#import <CoreGraphics/CoreGraphics.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void pose_prepare(FILE *f, NSString *path);
void pvz2_cursor_decode_etc(const unsigned char *, int, int, unsigned, unsigned char *);

size_t ZSTD_decompress(void *, size_t, const void *, size_t);
size_t ZSTD_findFrameCompressedSize(const void *, size_t);
unsigned ZSTD_isError(size_t);

/* These readers are bounded: a damaged or different OBB must not affect the game. */
static uint32_t u32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static NSData *read_at(FILE *f, uint64_t offset, size_t length, uint64_t size)
{
    if (offset > size || length > size - offset || length > 100000000 || fseeko(f, (off_t)offset, SEEK_SET)) return nil;
    NSMutableData *data = [NSMutableData dataWithLength:length];
    return fread(data.mutableBytes, 1, length, f) == length ? data : nil;
}

static NSData *group_part(FILE *f, uint64_t file_size, const uint8_t *entry, int part)
{
    uint64_t base = u32(entry + 0x80);
    NSData *header = read_at(f, base, 0x50, file_size);
    if (header.length != 0x50 || memcmp(header.bytes, "pgsr", 4)) return nil;
    const uint8_t *h = header.bytes;
    uint32_t off = u32(h + part), compressed = u32(h + part + 4), size = u32(h + part + 8);
    if (!size || size > 100000000 || off > u32(entry + 0x84) || compressed > u32(entry + 0x84) - off) return nil;
    NSData *data = read_at(f, base + off, compressed, file_size);
    if (!data) return nil;
    if (data.length >= 4 && u32(data.bytes) == 0xfd2fb528) {
        size_t frame = ZSTD_findFrameCompressedSize(data.bytes, data.length);
        if (ZSTD_isError(frame)) return nil;
        NSMutableData *out = [NSMutableData dataWithLength:size];
        size_t n = ZSTD_decompress(out.mutableBytes, size, data.bytes, frame);
        return !ZSTD_isError(n) && n == size ? out : nil;
    }
    return data.length == size ? data : nil;
}

/* The game's RTON is a compact typed object stream with two string tables. */
@interface PVZ2RTON : NSObject
@property(nonatomic, strong) NSData *data;
@property NSUInteger pos;
@property(nonatomic, strong) NSMutableArray<NSString *> *strings;
@property(nonatomic, strong) NSMutableArray<NSString *> *utf8strings;
- (id)value:(uint8_t)type depth:(int)depth;
- (NSDictionary *)object:(int)depth;
@end

@implementation PVZ2RTON
- (uint8_t)byte
{
    if (_pos >= _data.length) @throw @"truncated RTON";
    return ((const uint8_t *)_data.bytes)[_pos++];
}
- (uint64_t)varint
{
    uint64_t result = 0;
    for (int shift = 0; shift < 64; shift += 7) {
        uint8_t c = [self byte];
        result |= (uint64_t)(c & 127) << shift;
        if (!(c & 128)) return result;
    }
    @throw @"invalid RTON varint";
}
- (uint64_t)fixed:(NSUInteger)n
{
    if (n > _data.length - _pos) @throw @"truncated RTON";
    uint64_t v = 0;
    memcpy(&v, (const uint8_t *)_data.bytes + _pos, n);
    _pos += n;
    return v;
}
- (NSString *)string:(BOOL)utf8
{
    if (utf8) [self varint];
    uint64_t len = [self varint];
    if (len > _data.length - _pos) @throw @"truncated RTON string";
    NSString *s = [[NSString alloc] initWithBytes:(const uint8_t *)_data.bytes + _pos length:(NSUInteger)len encoding:NSUTF8StringEncoding];
    _pos += (NSUInteger)len;
    if (!s) @throw @"invalid RTON string";
    return s;
}
- (id)value:(uint8_t)t depth:(int)depth
{
    if (depth > 64) @throw @"RTON nesting";
    switch (t) {
        case 0x00: return @NO; case 0x01: return @YES;
        case 0x09: case 0x0b: case 0x11: case 0x13: case 0x21: case 0x23: case 0x27: case 0x41: case 0x43: case 0x47: return @0;
        case 0x08: case 0x0a: return @([self fixed:1]);
        case 0x10: case 0x12: return @([self fixed:2]);
        case 0x20: case 0x26: return @([self fixed:4]);
        case 0x22: { uint32_t bits = (uint32_t)[self fixed:4]; float value; memcpy(&value, &bits, 4); return @(value); }
        case 0x40: case 0x46: return @([self fixed:8]);
        case 0x42: { uint64_t bits = [self fixed:8]; double value; memcpy(&value, &bits, 8); return @(value); }
        case 0x24: case 0x28: case 0x44: case 0x48: return @([self varint]);
        case 0x25: case 0x29: case 0x45: case 0x49: { uint64_t n = [self varint]; return @((int64_t)((n >> 1) ^ -(n & 1))); }
        case 0x81: return [self string:NO];
        case 0x82: return [self string:YES];
        case 0x84: return @"RTID(0)";
        case 0x83: {
            uint8_t k = [self byte];
            if (k == 0) return @"RTID(0)";
            if (k == 2) { [self string:YES]; [self varint]; [self varint]; [self fixed:4]; return @"RTID"; }
            if (k == 3) { NSString *sheet = [self string:YES], *name = [self string:YES]; return [NSString stringWithFormat:@"RTID(%@@%@)", name, sheet]; }
            @throw @"unknown RTON reference";
        }
        case 0x85: return [self object:depth + 1];
        case 0x86: {
            if ([self byte] != 0xfd) @throw @"invalid RTON array";
            uint64_t count = [self varint];
            if (count > _data.length - _pos) @throw @"invalid RTON array size";
            NSMutableArray *a = [NSMutableArray arrayWithCapacity:(NSUInteger)count];
            for (uint64_t i = 0; i < count; i++) [a addObject:[self value:[self byte] depth:depth + 1]];
            if ([self byte] != 0xfe) @throw @"invalid RTON array end";
            return a;
        }
        case 0x90: { NSString *s = [self string:NO]; [_strings addObject:s]; return s; }
        case 0x92: { NSString *s = [self string:YES]; [_utf8strings addObject:s]; return s; }
        case 0x91: case 0x93: {
            uint64_t i = [self varint]; NSArray *a = t == 0x91 ? _strings : _utf8strings;
            if (i >= a.count) @throw @"invalid RTON string index";
            return a[(NSUInteger)i];
        }
        default: @throw [NSString stringWithFormat:@"unknown RTON type %02x", t];
    }
}
- (NSDictionary *)object:(int)depth
{
    NSMutableDictionary *d = [NSMutableDictionary dictionary];
    for (;;) {
        uint8_t t = [self byte];
        if (t == 0xff) return d;
        id key = [self value:t depth:depth + 1];
        if (![key isKindOfClass:NSString.class]) @throw @"invalid RTON key";
        d[key] = [self value:[self byte] depth:depth + 1];
    }
}
@end

static CGImageRef crop_image(NSData *atlas, int width, int height, NSDictionary *r)
{
    int x = [r[@"ax"] intValue], y = [r[@"ay"] intValue];
    int w = [r[@"aw"] intValue], h = [r[@"ah"] intValue];
    if (x < 0 || y < 0 || w <= 0 || h <= 0 || x > width - w || y > height - h ||
        atlas.length < (size_t)width * height * 4) return NULL;
    NSMutableData *out = [NSMutableData dataWithLength:(size_t)w * h * 4];
    const uint8_t *source = atlas.bytes;
    uint8_t *dest = out.mutableBytes;
    for (int row = 0; row < h; row++) {
        const uint8_t *p = source + ((size_t)(y + row) * width + x) * 4;
        for (int col = 0; col < w; col++, p += 4, dest += 4) {
            unsigned a = p[3];
            dest[0] = (p[0] * a + 127) / 255;
            dest[1] = (p[1] * a + 127) / 255;
            dest[2] = (p[2] * a + 127) / 255;
            dest[3] = a;
        }
    }
    CGDataProviderRef provider = CGDataProviderCreateWithCFData((__bridge CFDataRef)out);
    CGColorSpaceRef color = CGColorSpaceCreateDeviceRGB();
    CGImageRef image = CGImageCreate(w, h, 8, 32, w * 4, color,
                                     kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big,
                                     provider, NULL, false, kCGRenderingIntentDefault);
    CGColorSpaceRelease(color);
    CGDataProviderRelease(provider);
    return image;
}

NSDictionary<NSString *, id> *pvz2_cursor_art(NSString *path)
{
    FILE *f = fopen(path.fileSystemRepresentation, "rb");
    if (!f) return nil;
    NSDictionary *result = nil;
    @try {
        if (fseeko(f, 0, SEEK_END)) @throw @"OBB seek";
        off_t size = ftello(f);
        if (size < 0x70) @throw @"short OBB";
        NSData *header = read_at(f, 0, 0x70, size);
        const uint8_t *h = header.bytes;
        if (!h || memcmp(h, "1bsr", 4)) @throw @"bad OBB magic";
        uint32_t count = u32(h + 0x28), offset = u32(h + 0x2c), stride = u32(h + 0x30);
        uint32_t texcount = u32(h + 0x54), texoffset = u32(h + 0x58), texstride = u32(h + 0x5c);
        if (stride < 0xcc || stride > 4096 || count > 100000 || texstride < 16 || texstride > 4096 || texcount > 100000) @throw @"bad OBB table";
        NSData *entries = read_at(f, offset, (size_t)count * stride, size);
        NSData *textures = read_at(f, texoffset, (size_t)texcount * texstride, size);
        if (!entries || !textures) @throw @"missing OBB tables";
        const uint8_t *manifest = NULL, *hud = NULL;
        for (uint32_t i = 0; i < count; i++) {
            const uint8_t *e = (const uint8_t *)entries.bytes + (size_t)i * stride;
            if (!memchr(e, 0, 0x80)) continue;
            if (!strcmp((const char *)e, "__MANIFESTGROUP__")) manifest = e;
            else if (!strcmp((const char *)e, "UI_AlwaysLoaded_768")) hud = e;
        }
        if (!manifest || !hud) @throw @"missing OBB groups";
        NSData *rton = group_part(f, size, manifest, 0x18);
        if (rton.length < 8 || memcmp(rton.bytes, "RTON", 4)) @throw @"missing resource metadata";
        PVZ2RTON *reader = [PVZ2RTON new];
        reader.data = rton; reader.pos = 8; reader.strings = [NSMutableArray array]; reader.utf8strings = [NSMutableArray array];
        NSDictionary *root = [reader object:0];
        NSMutableDictionary *images = [NSMutableDictionary dictionary];
        for (NSDictionary *group in root[@"groups"]) {
            NSString *name = group[@"id"];
            const uint8_t *entry = [name isEqualToString:@"UI_AlwaysLoaded_768"] ? hud : NULL;
            if (!entry || u32(entry + 0xc4) != 1) continue;
            uint32_t index = u32(entry + 0xc8);
            if (index >= texcount) continue;
            const uint8_t *t = (const uint8_t *)textures.bytes + (size_t)index * texstride;
            uint32_t width = u32(t), height = u32(t + 4), pitch = u32(t + 8), fmt = u32(t + 12);
            if (!width || !height || width > 8192 || height > 8192 || pitch != width * 4 || fmt != 0) continue;
            NSData *atlas = group_part(f, size, entry, 0x28);
            if (!atlas) continue;
            for (NSDictionary *r in group[@"resources"]) {
                NSString *key = r[@"id"];
                BOOL wanted = [key isEqualToString:@"IMAGE_UI_HUD_INGAME_SHOVEL_ICON"] ||
                    [key isEqualToString:@"IMAGE_UI_HUD_INGAME_PLANTFOOD_BUTTON"];
                if (!wanted || !r[@"aw"] || !r[@"ah"]) continue;
                CGImageRef image = crop_image(atlas, width, height, r);
                if (image) { images[key] = (__bridge id)image; CGImageRelease(image); }
            }
        }
        size_t bytes = 0;
        for (id value in images.allValues) {
            CGImageRef image = (__bridge CGImageRef)value;
            bytes += CGImageGetBytesPerRow(image) * CGImageGetHeight(image);
        }
        NSLog(@"cursor art: %lu decoded image bytes", (unsigned long)bytes);
        result = images;
        pose_prepare(f, path);
    } @catch (id error) {
        NSLog(@"cursor art: %@", error);
    }
    fclose(f);
    return result;
}

/* Placement pictures need the complete pose: packet portraits have already lost
 * the parts outside the card. Read a still idle pose from the player's animation. */
@interface PVZ2PoseReader : NSObject
@property(nonatomic, strong) NSData *data;
@property NSUInteger pos;
- (uint32_t)unsignedValue:(NSUInteger)n;
- (int32_t)signedValue:(NSUInteger)n;
- (NSString *)string;
- (NSUInteger)count;
@end
@implementation PVZ2PoseReader
- (uint32_t)unsignedValue:(NSUInteger)n
{
    if (n > 4 || _pos > _data.length || n > _data.length - _pos) @throw @"truncated plant animation";
    uint32_t value = 0;
    memcpy(&value, (const uint8_t *)_data.bytes + _pos, n);
    _pos += n;
    return value;
}
- (int32_t)signedValue:(NSUInteger)n
{
    uint32_t value = [self unsignedValue:n];
    return n == 2 ? (int16_t)value : (int32_t)value;
}
- (NSString *)string
{
    NSUInteger n = [self unsignedValue:2];
    if (_pos > _data.length || n > _data.length - _pos) @throw @"truncated animation string";
    NSString *value = [[NSString alloc] initWithBytes:(const uint8_t *)_data.bytes + _pos length:n encoding:NSUTF8StringEncoding];
    _pos += n;
    if (!value) @throw @"invalid animation string";
    return value;
}
- (NSUInteger)count
{
    NSUInteger n = [self unsignedValue:1];
    return n == 255 ? [self unsignedValue:2] : n;
}
@end

static NSValue *pose_transform(CGAffineTransform t) { return [NSValue valueWithBytes:&t objCType:@encode(CGAffineTransform)]; }
static CGAffineTransform pose_matrix(NSValue *value)
{
    CGAffineTransform t = CGAffineTransformIdentity;
    if (value) [value getValue:&t size:sizeof t];
    return t;
}

/* Mint introductions and burrowed plants do not begin in their resting pose.
 * Keep the first frame of the resting label, with the initial display as a last resort. */
static NSDictionary *pose_sprite(PVZ2PoseReader *r)
{
    NSString *name = [[r string] lastPathComponent]; [r string]; [r unsignedValue:4];
    NSUInteger frames = [r unsignedValue:2];
    [r unsignedValue:2]; [r unsignedValue:2];
    NSMutableDictionary *layers = [NSMutableDictionary dictionary];
    NSArray *first = nil, *idle = nil;
    NSInteger poseRank = NSIntegerMax;
    NSArray *restLabels = @[@"idle", @"idle_above", @"plant_idle", @"loop", @"attack1"];
    for (NSUInteger frame = 0; frame < frames; frame++) {
        unsigned flags = [r unsignedValue:1];
        if (flags & ~63u) @throw @"unknown animation frame flags";
        if (flags & 1) {
            NSUInteger count = [r count];
            for (NSUInteger i = 0; i < count; i++) {
                unsigned index = [r unsignedValue:2] & 2047;
                if (index == 2047) index = [r unsignedValue:4];
                [layers removeObjectForKey:@(index)];
            }
        }
        if (flags & 2) {
            NSUInteger count = [r count];
            for (NSUInteger i = 0; i < count; i++) {
                unsigned bits = [r unsignedValue:2], index = bits & 2047;
                if (index == 2047) index = [r unsignedValue:4];
                unsigned resource = [r unsignedValue:1];
                if (resource == 255) resource = [r unsignedValue:2];
                if (bits & 8192) [r unsignedValue:2];
                NSString *name = bits & 4096 ? [r string] : @"";
                if (bits & 2048) [r unsignedValue:4];
                layers[@(index)] = [@{@"name": name, @"resource": @(resource), @"sprite": @((bits & 32768) != 0),
                                     @"matrix": pose_transform(CGAffineTransformIdentity), @"alpha": @1,
                                     @"additive": @((bits & 16384) != 0)} mutableCopy];
            }
        }
        if (flags & 4) {
            NSUInteger count = [r count];
            for (NSUInteger i = 0; i < count; i++) {
                unsigned bits = [r unsignedValue:2], index = bits & 1023;
                if (index == 1023) index = [r unsignedValue:4];
                CGAffineTransform t = CGAffineTransformIdentity;
                if (bits & 4096) {
                    t.a = [r signedValue:4] / 65536.0; t.c = [r signedValue:4] / 65536.0;
                    t.b = [r signedValue:4] / 65536.0; t.d = [r signedValue:4] / 65536.0;
                } else if (bits & 16384) {
                    t = CGAffineTransformMakeRotation([r signedValue:2] / 1000.0);
                }
                t.tx = [r signedValue:(bits & 2048) ? 4 : 2] / 20.0;
                t.ty = [r signedValue:(bits & 2048) ? 4 : 2] / 20.0;
                NSMutableDictionary *layer = layers[@(index)];
                layer[@"matrix"] = pose_transform(t);
                if (bits & 32768) {
                    for (int j = 0; j < 4; j++) [r unsignedValue:2];
                    /* Source rectangles need a different pose, not an incomplete picture. */
                    layer[@"unsupported"] = @YES;
                }
                if (bits & 8192) {
                    for (int j = 0; j < 3; j++) [r unsignedValue:1];
                    layer[@"alpha"] = @([r unsignedValue:1] / 255.0);
                }
                if (bits & 1024) [r unsignedValue:2];
            }
        }
        NSString *label = flags & 8 ? [r string] : nil;
        BOOL visible = NO;
        for (NSDictionary *layer in layers.allValues)
            if ([layer[@"alpha"] doubleValue] > 0) { visible = YES; break; }
        NSUInteger rank = label ? [restLabels indexOfObject:label.lowercaseString] : NSNotFound;
        if (visible && (!first || (rank != NSNotFound && (NSInteger)rank < poseRank))) {
            NSMutableArray *snapshot = [NSMutableArray array];
            for (NSNumber *index in [layers.allKeys sortedArrayUsingSelector:@selector(compare:)])
                [snapshot addObject:[layers[index] copy]];
            if (!first) first = snapshot;
            if (rank != NSNotFound && (NSInteger)rank < poseRank) { idle = snapshot; poseRank = (NSInteger)rank; }
        }
        if (flags & 32) {
            NSUInteger count = [r unsignedValue:1];
            for (NSUInteger i = 0; i < count; i++) { [r string]; [r string]; }
        }
    }
    return @{@"name": name, @"layers": idle ?: first ?: @[]};
}

/* RSG's prefix tree indexes both package metadata and animation files. */
static NSData *pose_file(FILE *f, uint64_t size, const uint8_t *entry, NSString *wanted)
{
    NSData *header = read_at(f, u32(entry + 0x80), 80, size);
    if (header.length < 80 || memcmp(header.bytes, "pgsr", 4)) return nil;
    uint32_t length = u32((const uint8_t *)header.bytes + 72), start = u32((const uint8_t *)header.bytes + 76);
    NSData *table = read_at(f, (uint64_t)u32(entry + 0x80) + start, length, size);
    NSData *pool = group_part(f, size, entry, 0x18);
    if (!table || !pool || (length & 3)) return nil;
    NSMutableDictionary *branches = [NSMutableDictionary dictionary];
    NSMutableString *name = [NSMutableString string];
    const uint8_t *bytes = table.bytes;
    for (NSUInteger pos = 0; pos + 4 <= table.length;) {
        NSString *prefix = branches[@(pos / 4)];
        if (prefix) name = [prefix mutableCopy];
        uint32_t word = u32(bytes + pos); pos += 4;
        if (word >> 8 && !branches[@(word >> 8)]) branches[@(word >> 8)] = [name copy];
        if (word & 255) { [name appendFormat:@"%c", word & 255]; continue; }
        if (pos + 12 > table.length) return nil;
        uint32_t kind = u32(bytes + pos), offset = u32(bytes + pos + 4), n = u32(bytes + pos + 8);
        pos += kind ? 32 : 12;
        if ([name isEqualToString:wanted] && !kind && offset <= pool.length && n <= pool.length - offset)
            return [pool subdataWithRange:NSMakeRange(offset, n)];
        [name setString:@""];
    }
    return nil;
}

static NSDictionary *pose_rton(NSData *data)
{
    if (data.length < 8 || memcmp(data.bytes, "RTON", 4)) return nil;
    PVZ2RTON *reader = [PVZ2RTON new];
    reader.data = data; reader.pos = 8; reader.strings = [NSMutableArray array]; reader.utf8strings = [NSMutableArray array];
    return [reader object:0];
}

/* The index is reused on the serial art queue; decoded atlases are released after
 * each still is made, so inspecting many plants does not retain their textures. */
@interface PVZ2PoseArchive : NSObject
@property(nonatomic, copy) NSString *path;
@property(nonatomic, strong) NSData *entries, *textures;
@property uint64_t size;
@property uint32_t stride, texstride;
@property(nonatomic, strong) NSDictionary *groups, *resources, *plants;
@end
@implementation PVZ2PoseArchive
@end

static PVZ2PoseArchive *pose_archive(FILE *f, NSString *path)
{
    static PVZ2PoseArchive *cached;
    if ([cached.path isEqualToString:path]) return cached;
    if (fseeko(f, 0, SEEK_END)) return nil;
    off_t size = ftello(f);
    NSData *header = read_at(f, 0, 112, size);
    if (header.length != 112 || memcmp(header.bytes, "1bsr", 4)) return nil;
    const uint8_t *h = header.bytes;
    uint32_t stride = u32(h + 48), count = u32(h + 40), texstride = u32(h + 92), texcount = u32(h + 84);
    if (stride < 204 || stride > 4096 || count > 100000 || texstride < 16 || texstride > 4096 || texcount > 100000) return nil;
    PVZ2PoseArchive *a = [PVZ2PoseArchive new];
    a.path = path; a.size = size; a.stride = stride; a.texstride = texstride;
    a.entries = read_at(f, u32(h + 44), (size_t)count * stride, size);
    a.textures = read_at(f, u32(h + 88), (size_t)texcount * texstride, size);
    if (!a.entries || !a.textures) return nil;
    NSMutableDictionary *groups = [NSMutableDictionary dictionary], *resources = [NSMutableDictionary dictionary], *plants = [NSMutableDictionary dictionary];
    for (uint32_t i = 0; i < count; i++) {
        const uint8_t *e = (const uint8_t *)a.entries.bytes + (size_t)i * stride;
        if (memchr(e, 0, 128)) groups[@((const char *)e)] = @(i);
    }
    NSNumber *manifest = groups[@"__MANIFESTGROUP__"], *packages = groups[@"Packages"];
    if (!manifest || !packages) return nil;
    NSDictionary *root = pose_rton(group_part(f, size, (const uint8_t *)a.entries.bytes + manifest.unsignedIntegerValue * stride, 0x18));
    for (NSDictionary *group in root[@"groups"]) {
        /* The 1536 images supply crisp art; Common groups hold the animations. */
        if (group[@"res"] && ![group[@"res"] isEqual:@"1536"]) continue;
        NSUInteger atlasIndex = 0;
        for (NSDictionary *resource in group[@"resources"]) {
            resources[resource[@"id"]] = @{@"group": group[@"id"], @"data": resource, @"atlasIndex": @(atlasIndex)};
            if ([resource[@"atlas"] boolValue]) atlasIndex++;
        }
    }
    root = pose_rton(pose_file(f, size, (const uint8_t *)a.entries.bytes + packages.unsignedIntegerValue * stride, @"PACKAGES\\PLANTTYPES.RTON"));
    for (NSDictionary *object in root[@"objects"]) {
        NSDictionary *data = object[@"objdata"];
        if (data[@"TypeName"] && data[@"PopAnim"]) plants[data[@"TypeName"]] = data;
    }
    a.groups = groups; a.resources = resources; a.plants = plants;
    if (!plants.count || !resources.count) return nil;
    cached = a;
    return a;
}

static void pose_prepare(FILE *f, NSString *path)
{
    (void)pose_archive(f, path);
}

static void pose_flatten(NSArray *layers, NSArray *sprites, NSArray *images, CGAffineTransform parent,
                         CGFloat alpha, NSMutableArray *draws, unsigned depth)
{
    if (depth > 32 || draws.count > 4096) @throw @"plant pose nesting";
    for (NSDictionary *layer in layers) {
        CGFloat opacity = alpha * [layer[@"alpha"] doubleValue];
        if (opacity <= 0) continue;
        if ([layer[@"unsupported"] boolValue]) @throw @"unsupported plant source rectangle";
        CGAffineTransform t = CGAffineTransformConcat(pose_matrix(layer[@"matrix"]), parent);
        NSUInteger index = [layer[@"resource"] unsignedIntegerValue];
        if ([layer[@"sprite"] boolValue]) {
            if (index >= sprites.count) @throw @"invalid plant sprite index";
            NSDictionary *sprite = sprites[index];
            NSString *name = sprite[@"name"];
            /* The game rig hides costume and plant-food armour tracks for the
             * ordinary plant. They coexist in the animation's display list. */
            if ([name hasPrefix:@"custom_"] || [name isEqualToString:@"_custom"] ||
                [name containsString:@"plantfood_armor"] || [name hasSuffix:@"_armor_states"] || [name isEqualToString:@"Magnet_Item"]) continue;
            pose_flatten(sprite[@"layers"], sprites, images, t, opacity, draws, depth + 1);
        } else {
            if (index >= images.count) @throw @"invalid plant image index";
            NSDictionary *image = images[index];
            t = CGAffineTransformConcat(pose_matrix(image[@"matrix"]), t);
            [draws addObject:@{@"key": image[@"key"], @"matrix": pose_transform(t), @"alpha": @(opacity),
                              @"additive": layer[@"additive"]}];
        }
    }
}

@implementation PVZ2CursorPose
@end

NSArray<NSString *> *pvz2_cursor_plant_names(NSString *path)
{
    FILE *f = fopen(path.fileSystemRepresentation, "rb");
    if (!f) return @[];
    NSArray *names = @[];
    @try { names = [pose_archive(f, path).plants.allKeys sortedArrayUsingSelector:@selector(compare:)]; }
    @catch (id error) { NSLog(@"cursor plant index: %@", error); }
    fclose(f);
    return names;
}

PVZ2CursorPose *pvz2_cursor_plant_art(NSString *path, NSString *plant)
{
    FILE *f = fopen(path.fileSystemRepresentation, "rb");
    if (!f) return nil;
    PVZ2CursorPose *result = nil;
    @try {
        PVZ2PoseArchive *a = pose_archive(f, path);
        NSDictionary *animation = a.resources[a.plants[plant][@"PopAnim"]];
        NSNumber *group = a.groups[animation[@"group"]];
        if (!group) @throw @"missing plant animation";
        NSString *file = [[animation[@"data"][@"path"] componentsJoinedByString:@"\\"] uppercaseString];
        NSData *pam = pose_file(f, a.size, (const uint8_t *)a.entries.bytes + group.unsignedIntegerValue * a.stride, file);
        PVZ2PoseReader *r = [PVZ2PoseReader new]; r.data = pam;
        if ([r unsignedValue:4] != 0xbaf01954 || [r unsignedValue:4] != 6) @throw @"unsupported plant animation";
        [r unsignedValue:1]; for (int i = 0; i < 4; i++) [r unsignedValue:2];
        NSUInteger count = [r unsignedValue:2];
        NSMutableArray *images = [NSMutableArray array], *sprites = [NSMutableArray array];
        for (NSUInteger i = 0; i < count; i++) {
            NSString *key = [[[r string] componentsSeparatedByString:@"|"] lastObject];
            [r unsignedValue:2]; [r unsignedValue:2];
            CGAffineTransform t;
            t.a = [r signedValue:4] / 1310720.0; t.c = [r signedValue:4] / 1310720.0;
            t.b = [r signedValue:4] / 1310720.0; t.d = [r signedValue:4] / 1310720.0;
            t.tx = [r signedValue:2] / 20.0; t.ty = [r signedValue:2] / 20.0;
            [images addObject:@{@"key": key, @"matrix": pose_transform(t)}];
        }
        count = [r unsignedValue:2];
        for (NSUInteger i = 0; i < count; i++) [sprites addObject:pose_sprite(r)];
        if (![r unsignedValue:1]) @throw @"missing plant main sprite";
        NSArray *main = pose_sprite(r)[@"layers"];
        NSMutableArray *draws = [NSMutableArray array];
        pose_flatten(main, sprites, images, CGAffineTransformIdentity, 1, draws, 0);
        NSMutableDictionary *atlases = [NSMutableDictionary dictionary], *parts = [NSMutableDictionary dictionary];
        CGRect bounds = CGRectNull;
        for (NSDictionary *draw in draws) {
            NSString *key = draw[@"key"];
            NSDictionary *resource = a.resources[key], *metadata = resource[@"data"];
            NSString *groupName = resource[@"group"];
            NSDictionary *part = parts[key];
            if (!part) {
                NSNumber *index = a.groups[groupName];
                if (!index) @throw @"missing plant atlas group";
                const uint8_t *entry = (const uint8_t *)a.entries.bytes + index.unsignedIntegerValue * a.stride;
                NSUInteger atlasIndex = [a.resources[metadata[@"parent"]][@"atlasIndex"] unsignedIntegerValue];
                NSUInteger texture = u32(entry + 200) + atlasIndex;
                if (atlasIndex >= u32(entry + 196) || texture >= a.textures.length / a.texstride) @throw @"invalid plant texture index";
                const uint8_t *tex = (const uint8_t *)a.textures.bytes + texture * a.texstride;
                uint32_t width = u32(tex), height = u32(tex + 4);
                if (!width || !height || width > 8192 || height > 8192 || u32(tex + 8) != width * 4 || (u32(tex + 12) != 0 && u32(tex + 12) != 147 && u32(tex + 12) != 150)) @throw @"unsupported plant texture";
                NSData *atlas = atlases[groupName];
                if (!atlas) { atlas = group_part(f, a.size, entry, 0x28); if (atlas) atlases[groupName] = atlas; }
                /* Every preceding texture occupies its full pitch times height. */
                NSUInteger offset = 0;
                for (NSUInteger j = 0; j < atlasIndex; j++) {
                    const uint8_t *previous = (const uint8_t *)a.textures.bytes + (u32(entry + 200) + j) * a.texstride;
                    offset += (size_t)u32(previous) * u32(previous + 4) * (u32(previous + 12) == 147 ? 3 : u32(previous + 12) == 150 ? 2 : 8) / 2;
                }
                size_t pixels = (size_t)width * height;
                size_t bytes = u32(tex + 12) == 147 ? pixels * 3 / 2 : u32(tex + 12) == 150 ? pixels : pixels * 4;
                if (offset > atlas.length || bytes > atlas.length - offset) @throw @"short plant atlas";
                NSString *atlasKey = [NSString stringWithFormat:@"%@:%lu", groupName, (unsigned long)atlasIndex];
                NSData *rgba = atlases[atlasKey];
                if (!rgba) {
                    const uint8_t *source = (const uint8_t *)atlas.bytes + offset;
                    if (u32(tex + 12) == 147 || u32(tex + 12) == 150) {
                        if ((width & 3) || (height & 3)) @throw @"unaligned plant ETC texture";
                        NSMutableData *decoded = [NSMutableData dataWithLength:pixels * 4];
                        pvz2_cursor_decode_etc(source, width, height, u32(tex + 12), decoded.mutableBytes);
                        rgba = decoded;
                    } else rgba = [atlas subdataWithRange:NSMakeRange(offset, bytes)];
                    atlases[atlasKey] = rgba;
                }
                CGImageRef image = crop_image(rgba, width, height, metadata);
                if (!image) @throw @"missing plant image";
                CGRect rect = CGRectMake([metadata[@"x"] doubleValue] / 1.28, [metadata[@"y"] doubleValue] / 1.28,
                                         CGImageGetWidth(image) / 1.28, CGImageGetHeight(image) / 1.28);
                part = @{@"image": (__bridge id)image, @"rect": [NSValue valueWithBytes:&rect objCType:@encode(CGRect)]};
                CGImageRelease(image); parts[key] = part;
            }
            CGRect rect; [part[@"rect"] getValue:&rect size:sizeof rect];
            bounds = CGRectUnion(bounds, CGRectApplyAffineTransform(rect, pose_matrix(draw[@"matrix"])));
        }
        if (CGRectIsNull(bounds) || !isfinite(bounds.origin.x) || !isfinite(bounds.origin.y) ||
            !isfinite(bounds.size.width) || !isfinite(bounds.size.height) || bounds.size.width <= 0 || bounds.size.height <= 0 ||
            bounds.size.width > 2048 || bounds.size.height > 2048) @throw @"invalid plant pose bounds";
        /* Derive the canvas from every transformed part, with a transparent margin
         * for filtering. A fixed card-sized canvas would clip tall or wide plants. */
        bounds = CGRectInset(CGRectIntegral(bounds), -2, -2);
        size_t width = (size_t)ceil(bounds.size.width * 2), height = (size_t)ceil(bounds.size.height * 2);
        CGColorSpaceRef color = CGColorSpaceCreateDeviceRGB();
        CGContextRef context = CGBitmapContextCreate(NULL, width, height, 8, width * 4, color, (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
        CGColorSpaceRelease(color);
        if (!context) @throw @"plant pose allocation";
        CGContextTranslateCTM(context, 0, height); CGContextScaleCTM(context, 2, -2);
        CGContextTranslateCTM(context, -bounds.origin.x, -bounds.origin.y);
        for (NSDictionary *draw in draws) {
            NSDictionary *part = parts[draw[@"key"]];
            CGRect rect; [part[@"rect"] getValue:&rect size:sizeof rect];
            CGContextSaveGState(context);
            CGContextConcatCTM(context, pose_matrix(draw[@"matrix"]));
            CGContextSetAlpha(context, [draw[@"alpha"] doubleValue]);
            if ([draw[@"additive"] boolValue]) CGContextSetBlendMode(context, kCGBlendModePlusLighter);
            CGContextTranslateCTM(context, rect.origin.x, CGRectGetMaxY(rect));
            CGContextScaleCTM(context, 1, -1);
            CGContextDrawImage(context, CGRectMake(0, 0, rect.size.width, rect.size.height), (__bridge CGImageRef)part[@"image"]);
            CGContextRestoreGState(context);
        }
        CGImageRef image = CGBitmapContextCreateImage(context);
        CGContextRelease(context);
        result = [PVZ2CursorPose new];
        result.image = CFBridgingRelease(image);
        result.bounds = bounds;
        result.anchor = CGPointMake(200, 200);
        if (getenv("TL_PVZ2_POSE_AUDIT")) NSLog(@"pose %@ bounds %.1f,%.1f %.1fx%.1f anchor %.1f,%.1f",plant,bounds.origin.x,bounds.origin.y,bounds.size.width,bounds.size.height,result.anchor.x,result.anchor.y);
    } @catch (id error) {
        NSLog(@"cursor pose %@: %@", plant, error);
    }
    fclose(f);
    return result;
}
