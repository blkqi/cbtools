#!/usr/bin/env python

import sys
import os
import struct

def main(argv=sys.argv):
    infile = args[0]
    infileext = os.path.splitext(infile)[1].upper()
    if infileext not in ['.MOBI', '.PRC', '.AZW', '.AZW4']:
        print("Error: first parameter must be a Kindle/Mobipocket ebook.")
        return 1

    try:
        mobidata = open(infile, 'rb').read()

        palmheader = mobidata[0:78]
        ident = palmheader[0x3C:0x3C+8].decode()

        if ident != 'BOOKMOBI':
            raise Exception('invalid file format')

        beg, = struct.unpack_from('>L', mobidata, 78)
        end, = struct.unpack_from('>L', mobidata, 78+8)

        header = mobidata[beg:end]

        version, = struct.unpack_from('>L', header, 0x24)
        length, = struct.unpack_from('>L', header, 0x14)

        # only support mobi6
        assert(version == 6)

        codepage, = struct.unpack_from('>L', header, 0x1c)
        exth_flags, = struct.unpack_from('>L', header, 0x80)
        codec_map = {1252 : 'windows-1252',
                     65001: 'utf-8'}
        codec = codec_map.get(codepage, 'windows-1252')
        print('codec', codec)

        if exth_flags & 0x40:
            exth_offset = length + 16
            exth = header[exth_offset:]
            extra = header[length + 16: exth_offset]

        _length, num_items = struct.unpack('>LL', exth[4:12])
        extheader = exth[12:]

        pos = 0
        for _ in range(num_items):
            id, size = struct.unpack('>LL', extheader[pos:pos+8])
            content = extheader[pos + 8: pos + size]
            if id == 501:
                name = 'cdetype'
                #print(pos, name, content.decode(codec))
                break
            pos += size

        loc = beg + exth_offset + pos + 20

        with open(infile, 'rb+') as fp:
            fp.seek(loc)
            buffer = fp.read(4).decode(codec)
            if buffer == 'EBOK':
                print(f'Found EBOK, converting to PDOC')
                fp.seek(loc)
                fp.write('PDOC'.encode(codec))
            elif buffer == 'PDOC':
                print('File is already PDOC')

    except Exception as e:
        print("Error: %s" % e)
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())
