#include "pebble.h"

#ifdef TEXT_UNSHAPE
bool shape_text(char *text);
void unshape_text(char *text);
#else
void shape_text(char *text);
#endif
