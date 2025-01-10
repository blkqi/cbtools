#!/usr/bin/env python

import sys
import os
import struct

class MobiReader(object):
    def __init__(self, filename):
        self._data = self._read_file(filename)
        self._self_check()

    def _read_file(self, filename):
        with open(filename, 'rb') as fp:
            return fp.read()

    def data(self):
        return memoryview(self._data)

    def palm_header(self):
        return memoryview(self._data[0:78])

    def header(self):
        beg, = struct.unpack_from('>L', self._data, 78)
        end, = struct.unpack_from('>L', self._data, 78+8)
        self.header_offset = beg
        return memoryview(self._data[beg:end])

    def length(self):
        length, = struct.unpack_from('>L', self.header(), 0x14)
        return length

    def version(self):
        version, = struct.unpack_from('>L', self.header(), 0x24)
        return version

    def code_page(self):
        code_page, = struct.unpack_from('>L', self.header(), 0x1c)
        return code_page

    def codec(self):
        codec_map = {1252 : 'windows-1252', 65001: 'utf-8'}
        codec = codec_map.get(self.code_page(), 'windows-1252')
        return codec

    def exth_flags(self):
        exth_flags, = struct.unpack_from('>L', self.header(), 0x80)
        return exth_flags

    def exth(self):
        assert(self.exth_flags() & 0x40)
        self.exth_offset = self.length() + 16
        exth = memoryview(self.header()[self.exth_offset:])
        #extra = memoryview(self.header()[self.length() + 16: self.exth_offset])
        _, count = struct.unpack('>LL', exth[4:12])
        return exth, count

    def iter_exth(self):
        codec = self.codec()
        exth, count = self.exth()
        extheader = memoryview(exth[12:])
        offset = 0
        for _ in range(count):
            key, size = struct.unpack('>LL', extheader[offset:offset+8])
            content = bytes(extheader[offset+8: offset+size])
            value = content.decode(codec)
            yield (key, offset, value)
            offset += size

    def _self_check(self):
        ident = bytes(self.palm_header()[0x3C:0x3C+8]).decode()
        assert(ident == 'BOOKMOBI')

        # only support mobi6
        assert(self.version() == 6)

def find_exth_cdetype(mobi):
    # find position of 501: cdetype
    for key, offset, value in mobi.iter_exth():
        #print(key, value)
        if key == 501:
            break

    offset = mobi.header_offset + mobi.exth_offset + offset + 20
    return offset

def clobber(filename, offset, buffer):
    with open(filename, 'rb+') as fp:
        fp.seek(offset)
        fp.write(buffer)

def main(argv=sys.argv):
    filename = argv[1]
    mobi = MobiReader(filename)
    offset = find_exth_cdetype(mobi)
    buffer = 'PDOC'.encode(mobi.codec())
    clobber(filename, offset, buffer)

if __name__ == '__main__':
    sys.exit(main())
