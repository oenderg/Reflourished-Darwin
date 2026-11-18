"""Read and write the typed RTON configuration streams used by Reflourished 1.5.0."""
import re
import struct


class Reader:
    def __init__(self, data):
        if data[:8] != b'RTON\x01\0\0\0':
            raise ValueError('Unsupported RTON header')
        self.data = data
        self.pos = 8
        self.ascii = []
        self.utf8 = []

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError('Truncated RTON')
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def varint(self):
        value = 0
        for shift in range(0, 70, 7):
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('Invalid RTON varint')

    def string(self, unicode=False):
        if unicode:
            self.varint()
        return self.take(self.varint()).decode('utf-8')

    def value(self, tag=None):
        tag = self.take(1)[0] if tag is None else tag
        if tag in (0, 1):
            return bool(tag)
        if tag in (0x09, 0x0b, 0x11, 0x13, 0x21, 0x23, 0x27, 0x41, 0x43, 0x47):
            return 0
        fixed = {0x08: 'b', 0x0a: 'B', 0x10: 'h', 0x12: 'H', 0x20: 'i',
                 0x22: 'f', 0x26: 'I', 0x40: 'q', 0x42: 'd', 0x46: 'Q'}
        if tag in fixed:
            fmt = '<' + fixed[tag]
            return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]
        if tag in (0x24, 0x28, 0x44, 0x48):
            return self.varint()
        if tag in (0x25, 0x29, 0x45, 0x49):
            value = self.varint()
            return (value >> 1) ^ -(value & 1)
        if tag in (0x81, 0x82):
            return self.string(tag == 0x82)
        if tag in (0x90, 0x92):
            value = self.string(tag == 0x92)
            (self.ascii if tag == 0x90 else self.utf8).append(value)
            return value
        if tag in (0x91, 0x93):
            return (self.ascii if tag == 0x91 else self.utf8)[self.varint()]
        if tag == 0x84:
            return 'RTID(0)'
        if tag == 0x83:
            kind = self.take(1)[0]
            if kind == 0:
                return 'RTID(0)'
            if kind == 3:
                sheet = self.string(True)
                name = self.string(True)
                return f'RTID({name}@{sheet})'
            if kind == 2:
                sheet = self.string(True)
                low, high = self.varint(), self.varint()
                checksum = struct.unpack('<I', self.take(4))[0]
                return f'RTID({high}.{low}.{checksum:08x}@{sheet})'
            raise ValueError('Unsupported RTON reference')
        if tag == 0x85:
            return self.object()
        if tag == 0x86:
            if self.take(1) != b'\xfd':
                raise ValueError('Invalid RTON array')
            count = self.varint()
            if count > len(self.data) - self.pos:
                raise ValueError('Oversized RTON array')
            result = [self.value() for _ in range(count)]
            if self.take(1) != b'\xfe':
                raise ValueError('Unterminated RTON array')
            return result
        raise ValueError(f'Unknown RTON tag {tag:02x}')

    def object(self):
        result = {}
        while True:
            tag = self.take(1)[0]
            if tag == 255:
                return result
            key = self.value(tag)
            if not isinstance(key, str) or key in result:
                raise ValueError('Invalid or duplicate RTON object key')
            result[key] = self.value()


def decode(data):
    reader = Reader(data)
    result = reader.object()
    if reader.take(4) != b'DONE' or reader.pos != len(data):
        raise ValueError('Invalid RTON end marker')
    return result


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def raw_string(value, unicode=False):
    data = value.encode('utf-8')
    return (varint(len(value)) if unicode else b'') + varint(len(data)) + data


class Writer:
    def __init__(self):
        self.ascii = {}
        self.utf8 = {}

    def string(self, value):
        # References retain their native type so the engine resolves them as objects.
        if value == 'RTID(0)':
            return b'\x84'
        match = re.fullmatch(r'RTID\((.+)@([^@]+)\)', value)
        if match:
            name, sheet = match.groups()
            numeric = re.fullmatch(r'(\d+)\.(\d+)\.([0-9a-fA-F]{8})', name)
            if numeric:
                high, low, checksum = numeric.groups()
                return (b'\x83\x02' + raw_string(sheet, True) + varint(int(low))
                        + varint(int(high)) + struct.pack('<I', int(checksum, 16)))
            return b'\x83\x03' + raw_string(sheet, True) + raw_string(name, True)
        unicode = not value.isascii()
        cache = self.utf8 if unicode else self.ascii
        if value in cache:
            return bytes([0x93 if unicode else 0x91]) + varint(cache[value])
        cache[value] = len(cache)
        return bytes([0x92 if unicode else 0x90]) + raw_string(value, unicode)

    def value(self, value):
        if isinstance(value, bool):
            return bytes([int(value)])
        if isinstance(value, int):
            if value < 0:
                return b'\x45' + varint((-value * 2) - 1)
            return b'\x44' + varint(value)
        if isinstance(value, float):
            return b'\x42' + struct.pack('<d', value)
        if isinstance(value, str):
            return self.string(value)
        if isinstance(value, list):
            return b'\x86\xfd' + varint(len(value)) + b''.join(self.value(x) for x in value) + b'\xfe'
        if isinstance(value, dict):
            return b'\x85' + self.object(value)
        raise ValueError('Unsupported RTON value: ' + type(value).__name__)

    def object(self, value):
        return b''.join(self.string(k) + self.value(v) for k, v in value.items()) + b'\xff'


def encode(value):
    return b'RTON\x01\0\0\0' + Writer().object(value) + b'DONE'
