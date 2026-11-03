"""Minimal Android binary XML/resource edits with stable resource IDs.

Only package identity, versionCode, and the existing app label are changed.
No apktool round trip, DEX changes, or Java class renaming is involved.
"""
import struct


def chunks(data):
    offset = struct.unpack_from('<H', data, 2)[0]
    while offset < len(data):
        kind, header, size = struct.unpack_from('<HHI', data, offset)
        if size < header or size == 0 or offset + size > len(data):
            raise ValueError('Invalid Android binary chunk')
        yield offset, kind, header, size
        offset += size
    if offset != len(data):
        raise ValueError('Android binary chunk length mismatch')


def read_length(data, offset, utf8):
    if utf8:
        first = data[offset]
        return (((first & 127) << 8) | data[offset + 1], offset + 2) if first & 128 else (first, offset + 1)
    first = struct.unpack_from('<H', data, offset)[0]
    return (((first & 32767) << 16) | struct.unpack_from('<H', data, offset + 2)[0], offset + 4) if first & 32768 else (first, offset + 2)


def strings(data):
    _, header, _ = struct.unpack_from('<HHI', data)
    count, _, flags, start, _ = struct.unpack_from('<5I', data, 8)
    utf8 = bool(flags & 256)
    offsets = struct.unpack_from('<' + 'I' * count, data, header)
    result = []
    for offset in offsets:
        pos = start + offset
        length, pos = read_length(data, pos, utf8)
        if utf8:
            length, pos = read_length(data, pos, True)
            result.append(bytes(data[pos:pos + length]).decode('utf-8'))
        else:
            result.append(bytes(data[pos:pos + 2 * length]).decode('utf-16le'))
    return result


def encode_length(length, utf8):
    if utf8:
        if length > 32767:
            raise ValueError('String too long for an Android UTF-8 string pool')
        return bytes([length]) if length < 128 else bytes([128 | (length >> 8), length & 255])
    return struct.pack('<H', length) if length < 32768 else struct.pack('<HH', 32768 | (length >> 16), length & 65535)


def replace_pool(data, replacements):
    values = strings(data)
    _, header, size = struct.unpack_from('<HHI', data)
    count, _, flags, start, style_start = struct.unpack_from('<5I', data, 8)
    utf8 = bool(flags & 256)
    for index, value in sorted(replacements.items()):
        if index == len(values):
            values.append(value)
        else:
            values[index] = value
    blob = bytearray()
    offsets = []
    for value in values:
        offsets.append(len(blob))
        chars = len(value.encode('utf-16le')) // 2
        if utf8:
            encoded = value.encode('utf-8')
            blob += encode_length(chars, True) + encode_length(len(encoded), True) + encoded + b'\0'
        else:
            blob += encode_length(chars, False) + value.encode('utf-16le') + b'\0\0'
    blob += b'\0' * (-len(blob) % 4)
    prefix = bytearray(data[:start])
    added = len(values) - count
    if added:
        prefix[header + count * 4:header + count * 4] = bytes(added * 4)
    struct.pack_into('<I', prefix, 8, len(values))
    struct.pack_into('<I', prefix, 20, start + added * 4)
    struct.pack_into('<' + 'I' * len(values), prefix, header, *offsets)
    struct.pack_into('<I', prefix, 16, flags & ~1)  # A changed pool need not remain lexically sorted.
    styles = data[style_start:size] if style_start else b''
    struct.pack_into('<I', prefix, 24, len(prefix) + len(blob) if style_start else 0)
    struct.pack_into('<I', prefix, 4, len(prefix) + len(blob) + len(styles))
    return bytes(prefix + blob + styles)


def enable_debuggable(source):
    """Add an explicit android:debuggable=true attribute for a temporary diagnostic build."""
    records = [(kind, header, bytes(source[offset:offset + size])) for offset, kind, header, size in chunks(source)]
    pool_record = next(i for i, (kind, _, _) in enumerate(records) if kind == 1)
    values = strings(records[pool_record][2])
    namespace = values.index('http://schemas.android.com/apk/res/android')
    if 'debuggable' in values:
        name_index = values.index('debuggable')
    else:
        name_index = len(values)
        kind, header, pool = records[pool_record]
        records[pool_record] = (kind, header, replace_pool(pool, {name_index: 'debuggable'}))
        values.append('debuggable')
    map_record = next((i for i, (kind, _, _) in enumerate(records) if kind == 0x180), None)
    if map_record is None:
        raise ValueError('Missing Android XML resource map')
    _, map_header, resource_map = records[map_record]
    ids = list(struct.unpack_from('<' + 'I' * ((len(resource_map) - map_header) // 4), resource_map, map_header))
    ids += [0] * (name_index + 1 - len(ids))
    ids[name_index] = 0x0101000f
    mapping = bytearray(resource_map[:map_header]) + struct.pack('<' + 'I' * len(ids), *ids)
    struct.pack_into('<I', mapping, 4, len(mapping))
    records[map_record] = (0x180, map_header, bytes(mapping))
    found = False
    for index, (kind, header, raw) in enumerate(records):
        if kind != 0x102 or values[struct.unpack_from('<I', raw, 20)[0]] != 'application':
            continue
        chunk = bytearray(raw)
        start, size, count = struct.unpack_from('<HHH', chunk, 24)
        if size != 20:
            raise ValueError('Unexpected XML attribute size')
        first = 16 + start
        attributes = [bytes(chunk[first + i * size:first + (i + 1) * size]) for i in range(count)]
        pointers = [struct.unpack_from('<H', chunk, p)[0] for p in [30, 32, 34]]
        old_attributes = list(attributes)
        debug = struct.pack('<IIIHBBI', namespace, name_index, 0xffffffff, 8, 0, 0x12, 0xffffffff)
        existing = next((i for i, attr in enumerate(attributes) if struct.unpack_from('<I', attr, 4)[0] == name_index), None)
        if existing is None:
            attributes.append(debug)
        else:
            attributes[existing] = debug
            old_attributes[existing] = debug
        attributes.sort(key=lambda attr: ids[struct.unpack_from('<I', attr, 4)[0]] if struct.unpack_from('<I', attr, 4)[0] < len(ids) else 0)
        chunk = chunk[:first] + b''.join(attributes) + chunk[first + count * size:]
        struct.pack_into('<I', chunk, 4, len(chunk))
        struct.pack_into('<H', chunk, 28, len(attributes))
        for pos, pointer in zip([30, 32, 34], pointers):
            struct.pack_into('<H', chunk, pos, attributes.index(old_attributes[pointer - 1]) + 1 if pointer else 0)
        records[index] = (kind, header, bytes(chunk))
        found = True
        break
    if not found:
        raise ValueError('Application element not found')
    header_size = struct.unpack_from('<H', source, 2)[0]
    result = bytearray(source[:header_size] + b''.join(record[2] for record in records))
    struct.pack_into('<I', result, 4, len(result))
    return bytes(result)


def patch_manifest(source, old_package, new_package, version_code):
    data = bytearray(source)
    pool_offset = pool_size = None
    pool_values = []
    package_indices = set()
    found_code = False
    for offset, kind, header, size in chunks(data):
        if kind == 1:
            pool_offset, pool_size = offset, size
            pool_values = strings(data[offset:offset + size])
        elif kind == 0x102:
            element_index = struct.unpack_from('<I', data, offset + 20)[0]
            if pool_values[element_index] != 'manifest':
                continue
            attribute_start, attribute_size, count = struct.unpack_from('<HHH', data, offset + 24)
            for i in range(count):
                pos = offset + 16 + attribute_start + i * attribute_size
                name_index, raw_index = struct.unpack_from('<II', data, pos + 4)
                name = pool_values[name_index]
                if name == 'versionCode':
                    struct.pack_into('<I', data, pos + 16, version_code)
                    found_code = True
                elif name == 'package':
                    if raw_index != 0xffffffff:
                        package_indices.add(raw_index)
                    if data[pos + 15] == 3:
                        package_indices.add(struct.unpack_from('<I', data, pos + 16)[0])
    if not found_code or not package_indices or pool_offset is None:
        raise ValueError('Expected package/versionCode fields not found in AndroidManifest.xml')
    if any(pool_values[index] != old_package for index in package_indices):
        raise ValueError('Manifest package differs from APK metadata')
    pool = replace_pool(data[pool_offset:pool_offset + pool_size], {i: new_package for i in package_indices})
    result = data[:pool_offset] + pool + data[pool_offset + pool_size:]
    struct.pack_into('<I', result, 4, len(result))
    return bytes(result)


def patch_resources(source, old_package, new_package, old_label, new_label):
    parts = []
    label_changes = package_changes = 0
    encoded_package = new_package.encode('utf-16le')
    if len(encoded_package) > 254:
        raise ValueError('Package name exceeds the Android resource header limit')
    for offset, kind, header, size in chunks(source):
        part = source[offset:offset + size]
        if kind == 1:
            values = strings(part)
            changes = {i: new_label for i, value in enumerate(values) if value == old_label}
            if changes:
                label_changes += len(changes)
                part = replace_pool(part, changes)
        elif kind == 0x200:
            part = bytearray(part)
            name = bytes(part[12:268]).decode('utf-16le').split('\0', 1)[0]
            if name == old_package:
                part[12:268] = encoded_package + bytes(256 - len(encoded_package))
                package_changes += 1
            part = bytes(part)
        parts.append(part)
    if not package_changes or not label_changes:
        raiseError = 'Could not locate original resource package/application label'
        raise ValueError(raiseError)
    header_size = struct.unpack_from('<H', source, 2)[0]
    result = bytearray(source[:header_size] + b''.join(parts))
    struct.pack_into('<I', result, 4, len(result))
    return bytes(result), dict(packageHeaders=package_changes, labelStrings=label_changes)
