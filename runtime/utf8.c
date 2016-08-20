#include "utf8.h"

uint16_t read_utf8(char **ptr) {
  uint16_t codept = **ptr;
  if ((codept & 0b11000000) == 0b11000000) {
    codept = ((codept & ~0b11000000) << 6) | (*(++(*ptr)) & ~0b10000000);
  } else if ((codept & 0b11100000) == 0b11100000) {
    codept = ((codept & ~0b11100000) << 6) | (*(++(*ptr)) & ~0b10000000);
    codept = (codept << 6) | (*(++(*ptr)) & ~0b10000000);
  } else if ((codept & 0b11110000) == 0b11110000) {
    codept = ((codept & ~0b11110000) << 6) | (*(++(*ptr)) & ~0b10000000);
    codept = (codept << 6) | (*(++(*ptr)) & ~0b10000000);
    codept = (codept << 6) | (*(++(*ptr)) & ~0b10000000);
  }
  (*ptr)++;
  return codept;
}

void write_utf8(char *ptr, uint16_t codept) {
  if (codept <= 0x7f) {
    *ptr = codept;
  } else if (codept <= 0x7ff) {
    *(ptr++) = 0b11000000 | (codept >> 6);
    *(ptr) = 0b10000000 | (codept & 0b111111);
  } else if (codept <= 0xFFFF) {
    *(ptr++) = 0b11100000 | ((codept >> 12) & 0b111111);
    *(ptr++) = 0b10000000 | ((codept >> 6) & 0b111111);
    *(ptr) = 0b10000000 | (codept & 0b111111);
  } else {
    *(ptr++) = 0b11110000 | ((codept >> 18) & 0b111111);
    *(ptr++) = 0b10000000 | ((codept >> 12) & 0b111111);
    *(ptr++) = 0b10000000 | ((codept >> 6) & 0b111111);
    *(ptr) = 0b10000000 | (codept & 0b111111);
  }
}
