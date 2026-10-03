#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AIRPLAY_TXT_MAX_ITEMS 16
#define AIRPLAY_TXT_DATA_CAPACITY 512

typedef struct {
  const char *key;
  const char *value;
} airplay_txt_item_t;

/* Strings referenced by items remain valid while this snapshot and the
 * device/model/manufacturer arguments are alive. No credentials are included. */
typedef struct {
  char features[32];
  char public_key_hex[65];
  airplay_txt_item_t airplay[AIRPLAY_TXT_MAX_ITEMS];
  airplay_txt_item_t raop[AIRPLAY_TXT_MAX_ITEMS];
  size_t airplay_count;
  size_t raop_count;
} airplay_advertisement_t;

bool airplay_advertisement_build(airplay_advertisement_t *advertisement,
                                 const char *device_id,
                                 const uint8_t *public_key,
                                 size_t public_key_len, uint64_t features,
                                 const char *model, const char *manufacturer);

/* Generic AP2 identities supply TXT snapshots without changing authentication
 * capabilities. Bit 30 is the existing unified advertiser capability. */
bool airplay_info_has_txt(uint64_t features, const char *model);

/* DNS TXT RDATA: one length byte followed by key=value bytes for each item.
 * Returns zero for invalid items or insufficient output capacity. */
size_t airplay_txt_encode(uint8_t *out, size_t capacity,
                          const airplay_txt_item_t *items, size_t count);
