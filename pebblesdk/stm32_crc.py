import array
import sys

CRC_POLY = 0x04C11DB7

def process_buffer(buf, c = 0xffffffff):
    word_count = len(buf) // 4
    if (len(buf) % 4 != 0):
        buf = buf[:word_count * 4] + buf[word_count * 4:][::-1] + '\0' * (4 - len(buf) % 4)
        word_count += 1

    crc = c
    words = array.array('I', buf)
    for i in xrange(0, word_count):
        crc = crc ^ words[i]
        for i in xrange(0, 32):
            if (crc & 0x80000000) != 0:
                crc = (crc << 1) ^ CRC_POLY
            else:
                crc = (crc << 1)
        crc = crc & 0xffffffff
    return crc

def crc32(data):
    return process_buffer(data)
