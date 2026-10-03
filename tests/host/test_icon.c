#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "plist.h"
#include "audio_stream.h"
#include "airplay_advertisement.h"
#include "mdns_airplay.h"
#include "mdns.h"
#include "rtsp_handlers.h"

static unsigned mode;
static uint8_t pk[32];
static uint8_t advertised_airplay[AIRPLAY_TXT_DATA_CAPACITY];
static uint8_t advertised_raop[AIRPLAY_TXT_DATA_CAPACITY];
static size_t airplay_size, raop_size;

bool audio_stream_uses_buffer(audio_stream_type_t type) {
  return type == AUDIO_STREAM_BUFFERED;
}
const char *settings_get_airplay_model(void) {
  const char *models[] = {"AudioAccessory5,1", "AppleTV3,2", "AirPlay-ESP32-Speaker"};
  return models[mode];
}
const char *settings_get_airplay_manufacturer(void) { return "Cedric"; }
uint32_t settings_get_airplay_features_lo(void) {
#ifdef CONFIG_AIRPLAY_FORCE_V1
  return 0x5C4A00;
#else
  return mode == 2 ? 0x445C4A00 : 0x405C4A00;
#endif
}
esp_err_t settings_get_device_name(char *name, size_t capacity) {
  snprintf(name, capacity, "Test receiver"); return ESP_OK;
}
void settings_device_name_to_hostname(const char *name, char *out, size_t capacity) {
  (void)name; snprintf(out, capacity, "test-receiver");
}
void wifi_get_mac_str(char *out, size_t capacity) {
  snprintf(out, capacity, "00:11:22:33:44:55");
}
void esp_read_mac(uint8_t *out, int type) {
  (void)type;
  const uint8_t mac[] = {0, 0x11, 0x22, 0x33, 0x44, 0x55};
  memcpy(out, mac, sizeof(mac));
}
const uint8_t *hap_get_public_key(void) { return pk; }
esp_err_t mdns_init(void) { return ESP_OK; }
esp_err_t mdns_hostname_set(const char *name) {
  assert(strcmp(name, "test-receiver") == 0); return ESP_OK;
}
esp_err_t mdns_service_add(const char *name, const char *type, const char *protocol,
                            uint16_t port, mdns_txt_item_t *items, size_t count) {
  (void)name;
  assert(strcmp(protocol, "_tcp") == 0 && port == 7000);
  assert(count <= AIRPLAY_TXT_MAX_ITEMS);
  airplay_txt_item_t snapshot[AIRPLAY_TXT_MAX_ITEMS];
  for (size_t i = 0; i < count; i++) {
    snapshot[i] = (airplay_txt_item_t){items[i].key, items[i].value};
  }
  if (strcmp(type, "_airplay") == 0) {
    airplay_size = airplay_txt_encode(advertised_airplay,
                                    sizeof(advertised_airplay), snapshot, count);
    assert(airplay_size > 0);
  } else {
    assert(strcmp(type, "_raop") == 0);
    raop_size = airplay_txt_encode(advertised_raop, sizeof(advertised_raop),
                                 snapshot, count);
    assert(raop_size > 0);
  }
  return ESP_OK;
}

static void save(const char *path, const uint8_t *data, size_t size) {
  FILE *file = fopen(path, "wb"); assert(file);
  assert(fwrite(data, 1, size, file) == size); fclose(file);
}

static void check_info_capacity(const char *name, uint64_t features,
                                 int64_t protocol, size_t size) {
  uint8_t guarded[2050];
  for (size_t capacity = 0; capacity <= size; capacity++) {
    memset(guarded, 0xA5, sizeof(guarded));
    size_t written = bplist_build_info_response(guarded + 1, capacity,
        "00:11:22:33:44:55", name, pk, sizeof(pk), features, protocol);
    assert(written == (capacity == size ? size : 0));
    assert(guarded[0] == 0xA5);
    for (size_t i = capacity + 1; i < sizeof(guarded); i++) assert(guarded[i] == 0xA5);
  }
}

int main(int argc, char **argv) {
  assert(argc == 3); mode = (unsigned)atoi(argv[1]); assert(mode <= 2);
  for (size_t i = 0; i < sizeof(pk); i++) pk[i] = (uint8_t)i;
  mdns_airplay_init();
  assert(raop_size > 0);
#ifdef CONFIG_AIRPLAY_FORCE_V1
  assert(airplay_size == 0);
  int64_t protocol = 1;
#else
  assert(airplay_size > 0);
  int64_t protocol = 2;
#endif
  uint64_t features = ((uint64_t)AIRPLAY_FEATURES_HI << 32) | AIRPLAY_FEATURES_LO;
  uint8_t out[2048];
  size_t size = bplist_build_info_response(out, sizeof(out), "00:11:22:33:44:55",
      "Test receiver", pk, sizeof(pk), features, protocol);
  assert(size > 0 && size <= sizeof(out));
  save(argv[2], out, size);
  char path[1024];
  snprintf(path, sizeof(path), "%s.airplay.txt", argv[2]);
  save(path, advertised_airplay, airplay_size);
  snprintf(path, sizeof(path), "%s.raop.txt", argv[2]);
  save(path, advertised_raop, raop_size);

  if (features & (UINT64_C(1) << 26)) {
    /* A longer valid TXT value forces bplist data's two-byte length form. */
    char long_id[65]; memset(long_id, 'd', sizeof(long_id) - 1);
    long_id[sizeof(long_id) - 1] = 0;
    uint8_t extended[2048];
    size_t extended_size = bplist_build_info_response(extended, sizeof(extended),
        long_id, "Test receiver", pk, sizeof(pk), features, protocol);
    assert(extended_size > size);
    snprintf(path, sizeof(path), "%s.extended.plist", argv[2]);
    save(path, extended, extended_size);
  }

  /* Every too-small capacity must fail without writing any byte outside it;
   * this covers extended data lengths, references, offsets and the trailer. */
  uint8_t guarded[2050];
  check_info_capacity("Test receiver", features, protocol, size);
  char long_unicode[561];
  for (size_t i = 0; i < 140; i++) {
    memcpy(long_unicode + i * 4, "\xF0\x9F\x8E\xB5", 4);
  }
  long_unicode[560] = 0;
  const char *unicode_names[] = {
    "Test\xC2\xB4 Speaker", "测试音箱", "Speaker \xF0\x9F\x8E\xB5",
    "测试\xC2\xB4 \xF0\x9F\x8E\xB5 Receiver",
    "Boundary \xC2\x80\xE0\xA0\x80\xF0\x90\x80\x80\xF4\x8F\xBF\xBF",
    long_unicode
  };
  for (size_t i = 0; i < sizeof(unicode_names) / sizeof(unicode_names[0]); i++) {
    size_t unicode_size = bplist_build_info_response(out, sizeof(out),
        "00:11:22:33:44:55", unicode_names[i], pk, sizeof(pk), features, protocol);
    assert(unicode_size > 0 && unicode_size <= sizeof(out));
    snprintf(path, sizeof(path), "%s.unicode%zu.plist", argv[2], i);
    save(path, out, unicode_size);
    check_info_capacity(unicode_names[i], features, protocol, unicode_size);
  }
  const char *invalid_utf8[] = {
    "\xC2", "\xE4\xB8", "\xF0\x9F\x8E", /* truncated */
    "\xC0\xAF", "\xE0\x80\xAF", "\xF0\x80\x80\xAF", /* overlong */
    "\xED\xA0\x80", "\xED\xBF\xBF", /* UTF-16 surrogate scalars */
    "\xF4\x90\x80\x80", "\xF5\x80\x80\x80", /* above U+10FFFF */
    "\x80", "\xFE", "\xC2" "A", "\xE2\x28\xA1", "\xF0\x90\x28\x80"
  };
  for (size_t i = 0; i < sizeof(invalid_utf8) / sizeof(invalid_utf8[0]); i++) {
    memset(guarded, 0xA5, sizeof(guarded));
    assert(bplist_build_info_response(guarded + 1, sizeof(out),
        "00:11:22:33:44:55", invalid_utf8[i], pk, sizeof(pk), features, protocol) == 0);
    assert(guarded[0] == 0xA5 && guarded[sizeof(out) + 1] == 0xA5);
  }
  assert(bplist_build_info_response(NULL, sizeof(out), "id", "name", pk,
                                   sizeof(pk), features, protocol) == 0);
  assert(bplist_build_info_response(out, sizeof(out), "id", "name", pk,
                                   0, features, protocol) == 0);
  const airplay_txt_item_t valid[] = {{"key", "value"}};
  for (size_t capacity = 0; capacity <= 10; capacity++) {
    memset(guarded, 0xA5, sizeof(guarded));
    size_t written = airplay_txt_encode(guarded + 1, capacity, valid, 1);
    assert(written == (capacity == 10 ? 10 : 0));
    assert(guarded[0] == 0xA5 && guarded[capacity + 1] == 0xA5);
  }
  assert(airplay_txt_encode(out, 10, valid, 1) == 10);
  char oversized[256]; memset(oversized, 'a', sizeof(oversized)); oversized[255] = 0;
  const airplay_txt_item_t invalid[] = {{"key", oversized}};
  assert(airplay_txt_encode(out, sizeof(out), invalid, 1) == 0);
  const airplay_txt_item_t invalid_key[] = {{"key=extra", "value"}};
  assert(airplay_txt_encode(out, sizeof(out), invalid_key, 1) == 0);
  return 0;
}
