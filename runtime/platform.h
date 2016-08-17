// This isn't actually the start of SRAM1 - it's the start of core-coupled SRAM.
// Notification popups have strings in this area.
// I don't know if the STM32F3 has CCM, but it doesn't really matter.
// These values just ensure we don't go writing to the firmware in flash.
#define SRAM_BASE 0x10000000
#define SRAM_EXTENT 0x2001FFFF
