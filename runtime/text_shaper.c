#include "text_shaper.h"
#include "text_shaper_lut.h"
#include "utf8.h"

typedef enum ShaperState {
    STATE_INITIAL,
    STATE_MEDIAL
} ShaperState;

typedef struct ShaperLUTEntry {
    uint16_t true_codept;
    uint16_t isolated_codept;
    uint16_t initial_codept;
    uint16_t medial_codept;
    uint16_t final_codept;
} ShaperLUTEntry;

static const ShaperLUTEntry* find_lut_entry_by_codept(uint16_t codept) {
    if (codept < 0x600 && codept > 0x6ff) {
        return NULL;
    }
    const ShaperLUTEntry * shaper_lut = (ShaperLUTEntry*)ARABIC_SHAPER_LUT;
    for (int i = 0; i < ARABIC_SHAPER_LUT_SIZE / sizeof(ShaperLUTEntry); ++i) {
        if (shaper_lut[i].true_codept == codept) {
            return &shaper_lut[i];
        }
    }
    return NULL;
}

bool shape_text(char* text) {
    ShaperState state = STATE_INITIAL;
    bool did_shape = false;
    char* ptr = text;

    if (!*ptr) {
        return false;
    }

    char* next_codept_ptr = ptr;
    uint16_t next_codept = read_utf8(&ptr);
    const ShaperLUTEntry* next_lut_entry = find_lut_entry_by_codept(next_codept);
    uint16_t this_codept = 0, last_codept = 0;
    char* this_codept_ptr = NULL, *last_codept_ptr = NULL;
    const ShaperLUTEntry* this_lut_entry;
    do {
        // Read forward one.
        last_codept = this_codept;
        last_codept_ptr = this_codept_ptr;
        this_codept = next_codept;
        this_codept_ptr = next_codept_ptr;
        this_lut_entry = next_lut_entry;
        if (*ptr) {
            next_codept_ptr = ptr;
            next_codept = read_utf8(&ptr);
            next_lut_entry = find_lut_entry_by_codept(next_codept);
        } else {
            next_codept = 0;
            next_codept_ptr = 0;
            next_lut_entry = NULL;
        }

        if (this_lut_entry) {
            did_shape = true;
            if (!next_lut_entry || this_lut_entry->medial_codept == this_lut_entry->final_codept) {
                // Final, or isolated form.
                if (state == STATE_INITIAL) {
                    write_utf8(this_codept_ptr, this_lut_entry->isolated_codept);
                } else {
                    write_utf8(this_codept_ptr, this_lut_entry->final_codept);
                }
                state = STATE_INITIAL;
            } else if (state == STATE_INITIAL) {
                state = STATE_MEDIAL;
                write_utf8(this_codept_ptr, this_lut_entry->initial_codept);
            } else {
                write_utf8(this_codept_ptr, this_lut_entry->medial_codept);
            }
        } else {
            // Not a shapable character - reset the state.
            state = STATE_INITIAL;
        }
    } while (next_codept);

    return did_shape;
}

void unshape_text(char* text) {

}
