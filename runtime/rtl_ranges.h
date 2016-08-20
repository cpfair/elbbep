#pragma once
#include "pebble.h"

bool is_rtl(uint16_t cp);

bool is_neutral(uint16_t cp);

// "Weak" LTR doesn't break an RTL span, but is itself laid out LTR.
// I have no clue what I'm doing, I'm just trying cases and comparing them to my
// PC.
bool is_weak_ltr(uint16_t cp);
