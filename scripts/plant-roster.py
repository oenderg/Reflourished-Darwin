#!/usr/bin/env python3
"""Apply the personal plant roster to a copy of the pinned Reflourished OBB."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from pvz2_rton import decode, encode

ROOT = Path(__file__).resolve().parents[1]
ALIGN = 4096


def aligned(n):
    return (n + ALIGN - 1) // ALIGN * ALIGN


def padded(b):
    return b + bytes(aligned(len(b)) - len(b))


def file_table(group):
    # Siblings share prefixes, and the first branch at an offset owns that prefix.
    size, start = struct.unpack_from('<II', group, 72)
    table = group[start:start + size]
    pos, name, branches, files = 0, '', {}, {}
    while pos < len(table):
        name = branches.pop(pos // 4, name)
        word = struct.unpack_from('<I', table, pos)[0]
        pos += 4
        char, sibling = word & 255, word >> 8
        if sibling:
            branches.setdefault(sibling, name)
        if char:
            name += chr(char)
            continue
        kind, offset, length = struct.unpack_from('<III', table, pos)
        if kind:
            raise ValueError('Packages must contain no texture records')
        files[name] = (offset, length, start + pos)
        pos += 12
        name = ''
    return files


def unpack(source):
    with source.open('rb') as f:
        header = f.read(112)
        words = struct.unpack('<28I', header)
        if words[:2] != (0x72736231, 4):
            raise ValueError('Expected a version 4 RSB')
        count, table, stride = words[10:13]
        if stride != 204:
            raise ValueError('Unexpected subgroup record size')
        f.seek(table)
        entries = f.read(count * stride)
        matches = [i for i in range(count)
                   if entries[i * stride:i * stride + 128].split(b'\0')[0] == b'Packages']
        if len(matches) != 1:
            raise ValueError('Expected exactly one Packages group')
        index = matches[0]
        entry_pos = table + index * stride
        entry = bytearray(entries[index * stride:(index + 1) * stride])
        offset, length = struct.unpack_from('<II', entry, 128)
        f.seek(offset)
        group = f.read(length)
        pool_index = struct.unpack_from('<I', entry, 136)[0]
        if words[20] != 152 or pool_index >= words[18]:
            raise ValueError('Unexpected RSB pool table')
        f.seek(words[19] + pool_index * words[20] + 128)
        pool_capacity = struct.unpack('<I', f.read(4))[0]
    h = struct.unpack_from('<23I', group)
    if h[:2] != (0x72736770, 4) or h[4] != 2 or h[11] or h[12]:
        raise ValueError('Unexpected Packages compression or texture layout')
    if pool_capacity < h[6] + h[8]:
        raise ValueError('Packages exceeds its loader memory pool capacity')
    compressed = group[h[6]:h[6] + h[7]]
    # zstd ignores the RSG alignment padding after the single compressed frame.
    result = subprocess.run(['zstd', '-d', '-q', '--stdout'], input=compressed,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode and len(result.stdout) != h[8]:
        raise ValueError('Cannot decompress Packages: ' + result.stderr.decode())
    if len(result.stdout) != h[8]:
        raise ValueError('Packages decompressed size differs from its header')
    return entry_pos, entry, group, result.stdout, file_table(group)


def patch_documents(documents, policy):
    plants, market, props = (documents[n] for n in
                            ['PLANTTYPES', 'MARKET_SCHEDULE', 'PROPERTYSHEETS'])
    keep = policy['keep']
    shop_hide = policy['hide_shop']
    other_hide = policy['hide_other']
    hidden = set(shop_hide + other_hide)
    expected = policy['expected']
    if (len(keep) != expected['kept'] or len(shop_hide) != expected['shop_removed']
            or len(hidden) != expected['hidden']):
        raise ValueError('Roster counts do not match the declared selection')
    if (len(set(keep) | hidden) != 192 or set(keep) & hidden
            or len(hidden) != len(shop_hide) + len(other_hide)):
        raise ValueError('Roster contains duplicate or overlapping IDs')
    by_id = {o['objdata']['TypeName']: o['objdata'] for o in plants['objects']
             if 'TypeName' in o.get('objdata', {})}
    order = next(o['objdata']['PlantTypeOrder'] for o in props['objects']
                 if 'PlantTypeOrder' in o.get('objdata', {}))
    regular = {n for n in order if not by_id[n].get('HideInPlantViewers')
               and not n.startswith('zomboidspawner_') and n != 'marigold'}
    if regular != set(keep) | hidden:
        raise ValueError('Roster does not match this OBB\'s 192 regular plants')
    before = copy.deepcopy(plants)
    for n in hidden:
        by_id[n]['HideInPlantViewers'] = True
    entries = market['objects'][0]['objdata']['MarketScheduleEntries']
    removed = []
    remaining = set()
    for entry in entries:
        updated = []
        for product in entry.get('ScheduledProducts', []):
            skus = product.get('Sku', [])
            if isinstance(skus, str):
                skus = [skus]
            # Match product IDs through PRODUCTS, never by an English display name.
            ids = {documents['sku_to_plant'].get(s) for s in skus} - {None}
            if ids & hidden:
                if not ids <= hidden:
                    raise ValueError('A store bundle mixes kept and hidden plants')
                removed.extend(ids)
            else:
                updated.append(product)
                if entry.get('Name') == 'Plants for sale, always!':
                    remaining.update(ids)
        entry['ScheduledProducts'] = updated
    if set(removed) != set(shop_hide) or len(removed) != expected['shop_removed']:
        raise ValueError('Scheduled plant removals differ from the requested roster')
    if len(remaining) != expected['shop_remaining'] or not remaining <= set(keep):
        raise ValueError('The permanent shop does not match the kept roster')
    prices = {o['objdata']['ObjectItem']: int(o['objdata']['PriceGems'])
              for o in documents['PRODUCTS']['objects']
              if o.get('objdata', {}).get('ObjectType') == 'plant'}
    total = sum(prices[n] for n in remaining)
    if total != expected['shop_total_gems']:
        raise ValueError('The remaining shop total differs from the roster')
    # The untouched objects include garden plants, existing hidden types and Roman cards.
    old_by_id = {o['objdata']['TypeName']: o['objdata'] for o in before['objects']
                 if 'TypeName' in o.get('objdata', {})}
    for n, old in old_by_id.items():
        expected = dict(old)
        if n in hidden:
            expected['HideInPlantViewers'] = True
        if by_id[n] != expected:
            raise ValueError('Unexpected plant change: ' + n)
    return {'kept': len(keep), 'hidden': len(hidden), 'shop_removed': len(removed),
            'shop_remaining': len(remaining), 'shop_total_gems': total,
            'hidden_ids': sorted(hidden)}


def build(source, output, policy_path):
    game = json.loads((ROOT / 'data/game-1.5.0.json').read_text())
    policy = json.loads(policy_path.read_text())
    if policy['game_version'] != game['version']:
        raise ValueError('Roster is for another game version')
    if source.resolve() == output.resolve():
        raise ValueError('The original OBB must not be overwritten')
    with source.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    if digest != game['obb_sha256']:
        raise ValueError('Expected the unmodified pinned 1.5.0 OBB')
    entry_pos, entry, group, data, files = unpack(source)
    names = ['PLANTTYPES', 'MARKET_SCHEDULE', 'PROPERTYSHEETS', 'PRODUCTS']
    docs = {}
    for n in names:
        off, length, _ = files['PACKAGES\\' + n + '.RTON']
        docs[n] = decode(data[off:off + length])
    docs['sku_to_plant'] = {o['objdata']['Sku']: o['objdata']['ObjectItem']
                            for o in docs['PRODUCTS']['objects']
                            if o.get('objdata', {}).get('ObjectType') == 'plant'}
    report = patch_documents(docs, policy)
    h = list(struct.unpack_from('<23I', group))
    prefix = bytearray(group[:h[6]])
    changed = bytearray(data)
    for n in ['PLANTTYPES', 'MARKET_SCHEDULE']:
        blob = encode(docs[n])
        if decode(blob) != docs[n]:
            raise ValueError('RTON round trip failed: ' + n)
        off = aligned(len(changed))
        changed.extend(bytes(off - len(changed)))
        changed.extend(blob)
        _, _, record = files['PACKAGES\\' + n + '.RTON']
        struct.pack_into('<II', prefix, record + 4, off, len(blob))
    changed = padded(changed)
    compressed = subprocess.run(['zstd', '-q', '-10', '--stdout'], input=changed,
                                stdout=subprocess.PIPE, check=True).stdout
    compressed = padded(compressed)
    h[7], h[8], h[10] = len(compressed), len(changed), h[6] + len(compressed)
    struct.pack_into('<23I', prefix, 0, *h)
    new_group = bytes(prefix) + compressed
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        staged = Path(temp) / output.name
        # APFS cloning keeps the unchanged art and original container inexpensive.
        cp = subprocess.run(['cp', '-c', str(source), str(staged)], stderr=subprocess.DEVNULL)
        if cp.returncode:
            shutil.copyfile(source, staged)
        offset = aligned(staged.stat().st_size)
        struct.pack_into('<II', entry, 128, offset, len(new_group))
        struct.pack_into('<III', entry, 128 + 6 * 4, h[7], h[8], h[8])
        struct.pack_into('<I', entry, 128 + 9 * 4, h[10])
        with staged.open('r+b') as f:
            f.seek(offset)
            f.write(new_group)
            f.seek(entry_pos)
            f.write(entry)
            # The loader allocates the group's buffer from the separate pool table.
            # Its capacity includes the uncompressed data and the RSG header.
            f.seek(0)
            rsb = struct.unpack('<28I', f.read(112))
            pool_index = struct.unpack_from('<I', entry, 136)[0]
            if rsb[20] != 152 or pool_index >= rsb[18]:
                raise ValueError('Unexpected RSB pool table')
            f.seek(rsb[19] + pool_index * rsb[20] + 128)
            f.write(struct.pack('<I', h[6] + h[8]))
        # Reopen the final container, rather than trusting only the in-memory edits.
        _, _, _, verified, table = unpack(staged)
        for name, (off, length, _) in table.items():
            before_off, before_length, _ = files[name]
            target = name.removeprefix('PACKAGES\\').removesuffix('.RTON')
            if target in ['PLANTTYPES', 'MARKET_SCHEDULE']:
                if decode(verified[off:off + length]) != docs[target]:
                    raise ValueError('Reopened container differs: ' + name)
            elif verified[off:off + length] != data[before_off:before_off + before_length]:
                raise ValueError('An unrelated package changed: ' + name)
        staged.replace(output)
    with output.open('rb') as f:
        report['obb_sha256'] = hashlib.file_digest(f, 'sha256').hexdigest()
    report['obb_size'] = output.stat().st_size
    report['source_sha256'] = digest
    report['policy_sha256'] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
    output.with_suffix('.roster.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--roster', type=Path, default=ROOT / 'data/plant-roster-98.json')
    args = parser.parse_args()
    build(args.source, args.output, args.roster)
