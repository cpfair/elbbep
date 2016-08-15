#include "pebble.h"

#define RTL_STATE_COOKIE_VAL 0b10110111011110110110101110110110
typedef struct RTLState {
  uint32_t cookie;
  char* last_line_end;
} RTLState;

typedef struct TextAttr {
    // The first member of this struct is a djb2 hash of the text.
    uint32_t text_hash;
    // The next members are font, sizing, etc. I think.
    uint32_t very_important_stuff;
    uint32_t same_here;
    // This is the GTextAlignment setting.
    // We commandeer it during text layout calculations because it doesn't matter there.
    RTLState* state;
    // And some other things here presumably.
} TextAttr;

typedef struct LineLayoutData LineLayoutData;

void rtl_apply(TextAttr* attr, LineLayoutData* layout);
