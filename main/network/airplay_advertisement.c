#include "airplay_advertisement.h"
#include "sdkconfig.h"

#include <stdio.h>
#include <string.h>

#ifdef CONFIG_ENABLE_AIRPLAY_ARTWORK
#define METADATA_TYPES "0,1,2"
#else
#define METADATA_TYPES "0,2"
#endif

bool airplay_info_has_txt(uint64_t features, const char *model) {
  return (features & (UINT64_C(1) << 30)) != 0 && model &&
         strcmp(model, "AirPlay-ESP32-Speaker") == 0;
}

bool airplay_advertisement_build(airplay_advertisement_t *ad,
                                 const char *device_id,
                                 const uint8_t *public_key,
                                 size_t public_key_len, uint64_t features,
                                 const char *model, const char *manufacturer) {
  if (!ad || !device_id || !public_key || public_key_len != 32 || !model ||
      !manufacturer) {
    return false;
  }
  memset(ad, 0, sizeof(*ad));
  snprintf(ad->features, sizeof(ad->features), "0x%X,0x%X",
           (unsigned)(uint32_t)features, (unsigned)(uint32_t)(features >> 32));
  for (size_t i = 0; i < public_key_len; i++) {
    snprintf(ad->public_key_hex + i * 2, 3, "%02x", public_key[i]);
  }
#define ADD(list, key_, value_)                                                \
  do {                                                                        \
    if (ad->list##_count >= AIRPLAY_TXT_MAX_ITEMS) { return false; }             \
    ad->list[ad->list##_count++] = (airplay_txt_item_t){key_, value_};            \
  } while (0)

#ifndef CONFIG_AIRPLAY_FORCE_V1
  ADD(airplay, "deviceid", device_id);
  ADD(airplay, "features", ad->features);
  ADD(airplay, "flags", "0x4");
  ADD(airplay, "model", model);
  ADD(airplay, "manufacturer", manufacturer);
  ADD(airplay, "pk", ad->public_key_hex);
  ADD(airplay, "pi", "00000000-0000-0000-0000-000000000000");
  ADD(airplay, "srcvers", "377.40.00");
  ADD(airplay, "vv", "2");
  ADD(airplay, "acl", "0");
#endif
  ADD(raop, "am", model);
  ADD(raop, "manufacturer", manufacturer);
#ifdef CONFIG_AIRPLAY_FORCE_V1
  ADD(raop, "tp", "UDP");
  ADD(raop, "sm", "false");
  ADD(raop, "sv", "false");
  ADD(raop, "ek", "1");
  ADD(raop, "et", "0,1");
  ADD(raop, "md", METADATA_TYPES);
  ADD(raop, "cn", "0,1");
  ADD(raop, "ch", "2");
  ADD(raop, "ss", "16");
  ADD(raop, "sr", "44100");
  ADD(raop, "vn", "3");
  ADD(raop, "txtvers", "1");
#else
  ADD(raop, "cn", "0,1,2,3");
  ADD(raop, "da", "true");
  ADD(raop, "ek", "1");
  ADD(raop, "et", "0,1,3,5");
  ADD(raop, "ft", ad->features);
  ADD(raop, "md", METADATA_TYPES);
  ADD(raop, "pk", ad->public_key_hex);
  ADD(raop, "sf", "0x4");
  ADD(raop, "tp", "UDP");
  ADD(raop, "vn", "65537");
  ADD(raop, "vs", "377.40.00");
  ADD(raop, "vv", "2");
#endif
#undef ADD
  return true;
}

size_t airplay_txt_encode(uint8_t *out, size_t capacity,
                          const airplay_txt_item_t *items, size_t count) {
  if (!out || !items || count == 0) {
    return 0;
  }
  size_t pos = 0;
  for (size_t i = 0; i < count; i++) {
    if (!items[i].key || !items[i].value || strchr(items[i].key, '=')) {
      return 0;
    }
    size_t key_len = strlen(items[i].key);
    size_t value_len = strlen(items[i].value);
    if (key_len == 0 || key_len > 254 || value_len > 254 - key_len) {
      return 0;
    }
    size_t length = key_len + 1 + value_len;
    if (pos > capacity || length + 1 > capacity - pos) {
      return 0;
    }
    out[pos++] = (uint8_t)length;
    memcpy(out + pos, items[i].key, key_len);
    pos += key_len;
    out[pos++] = '=';
    memcpy(out + pos, items[i].value, value_len);
    pos += value_len;
  }
  return pos;
}
