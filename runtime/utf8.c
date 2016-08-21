#include "utf8.h"

uint16_t read_utf8(char **ptr) {
  uint16_t codept = **ptr;
  int ct = 0;
  switch (codept >> 4) {
  case 0xF: // 0b1111
      // 4 bytes.
      ct = 3;
      break;
  case 0xE: // 0b1110
      // 3 bytes.
      ct = 2;
      break;
  case 0xC: // 0b1100
  case 0xD: // 0b1101
      // 2 bytes.
      ct = 1;
  }
  codept &= (~0b11110000000) >> ct;
  while (ct--) {
    codept = (codept << 6) | (*(++(*ptr)) & ~0b10000000);
  }
  (*ptr)++;
  return codept;
}

// NB this cannot write runes larger than 2 bytes!
void write_utf8(char *ptr, uint16_t codept) {
  if (codept <= 0x7f) {
    *ptr = codept;
  } else if (codept <= 0x7ff) {
    *(ptr++) = 0b11000000 | (codept >> 6);
    *(ptr) = 0b10000000 | (codept & 0b111111);
  }
}
