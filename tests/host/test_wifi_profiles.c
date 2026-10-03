/* Synthetic credentials only; never connect to or read a real network. */
#include "settings.h"
#include "nvs.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct {
  char key[24];
  unsigned char data[1024];
  size_t size;
} entry_t;
static entry_t entries[20];
static unsigned commits;

static entry_t *find(const char *key, int create) {
  entry_t *empty = NULL;
  for (size_t i = 0; i < 20; i++) {
    if (strcmp(entries[i].key, key) == 0) return &entries[i];
    if (!entries[i].key[0] && !empty) empty = &entries[i];
  }
  if (create && empty) {
    strcpy(empty->key, key);
    return empty;
  }
  return NULL;
}

esp_err_t nvs_open(const char *ns, int mode, nvs_handle_t *handle) {
  (void)mode;
  assert(strcmp(ns, "airplay") == 0);
  *handle = 1;
  return ESP_OK;
}
void nvs_close(nvs_handle_t handle) { assert(handle == 1); }
esp_err_t nvs_commit(nvs_handle_t handle) {
  assert(handle == 1);
  commits++;
  return ESP_OK;
}
esp_err_t nvs_get_blob(nvs_handle_t handle, const char *key, void *out, size_t *size) {
  assert(handle == 1);
  entry_t *entry = find(key, 0);
  if (!entry) return ESP_ERR_NVS_NOT_FOUND;
  size_t available = *size;
  *size = entry->size;
  if (!out) return ESP_OK;
  if (available < entry->size) return ESP_ERR_NVS_INVALID_LENGTH;
  memcpy(out, entry->data, entry->size);
  return ESP_OK;
}
esp_err_t nvs_set_blob(nvs_handle_t handle, const char *key, const void *data, size_t size) {
  assert(handle == 1);
  entry_t *entry = find(key, 1);
  if (!entry || size > sizeof(entry->data)) return ESP_ERR_NO_MEM;
  memcpy(entry->data, data, size);
  entry->size = size;
  return ESP_OK;
}
esp_err_t nvs_get_str(nvs_handle_t h, const char *k, char *v, size_t *n) {
  return nvs_get_blob(h, k, v, n);
}
esp_err_t nvs_set_str(nvs_handle_t h, const char *k, const char *v) {
  return nvs_set_blob(h, k, v, strlen(v) + 1);
}
esp_err_t nvs_get_i32(nvs_handle_t h, const char *k, int32_t *v) {
  size_t n = sizeof(*v); return nvs_get_blob(h, k, v, &n);
}
esp_err_t nvs_set_i32(nvs_handle_t h, const char *k, int32_t v) {
  return nvs_set_blob(h, k, &v, sizeof(v));
}
esp_err_t nvs_get_u8(nvs_handle_t h, const char *k, uint8_t *v) {
  size_t n = sizeof(*v); return nvs_get_blob(h, k, v, &n);
}
esp_err_t nvs_set_u8(nvs_handle_t h, const char *k, uint8_t v) {
  return nvs_set_blob(h, k, &v, sizeof(v));
}
esp_err_t nvs_erase_key(nvs_handle_t h, const char *k) {
  assert(h == 1); entry_t *entry = find(k, 0);
  if (!entry) return ESP_ERR_NVS_NOT_FOUND;
  memset(entry, 0, sizeof(*entry)); return ESP_OK;
}
const char *esp_err_to_name(esp_err_t err) { (void)err; return "stub"; }
esp_err_t esp_read_mac(uint8_t *mac, int kind) {
  (void)kind; memset(mac, 0, 6); return ESP_OK;
}
void dac_set_volume(float db) { (void)db; }

static void reset(void) { memset(entries, 0, sizeof(entries)); commits = 0; }
static void active_is(const char *ssid, const char *password) {
  char current[33], secret[65];
  assert(settings_get_wifi_ssid(current, sizeof(current)) == ESP_OK);
  assert(settings_get_wifi_password(secret, sizeof(secret)) == ESP_OK);
  assert(strcmp(current, ssid) == 0);
  assert(strcmp(secret, password) == 0);
  memset(secret, 0, sizeof(secret));
}

static void airplay_boot_is(unsigned icon) {
  assert(strcmp(settings_get_airplay_model(),
                settings_airplay_model_for_icon(icon)) == 0);
#ifdef CONFIG_AIRPLAY_FORCE_V1
  assert(settings_get_airplay_features_lo() == 0x005C4A00);
#else
  assert(settings_get_airplay_features_lo() ==
         0x405C4A00);
#endif
  assert(strcmp(settings_get_airplay_manufacturer(), "Cedric") == 0);
}

static void test_airplay_presets(void) {
  reset();
  assert(settings_init() == ESP_OK);
  assert(settings_get_airplay_icon() == 0);
  airplay_boot_is(0);
  assert(strcmp(settings_airplay_model_for_icon(0), "AudioAccessory5,1") == 0);
  assert(strcmp(settings_airplay_model_for_icon(1), "AppleTV3,2") == 0);
  assert(strcmp(settings_airplay_model_for_icon(2), "AirPlay-ESP32-Speaker") == 0);
  assert(strcmp(settings_airplay_model_for_icon(3), "AudioAccessory5,1") == 0);

  /* Saving a preset must not change live protocol model/features mid-session. */
  assert(settings_set_airplay_icon(2) == ESP_OK);
  assert(settings_get_airplay_icon() == 2);
  airplay_boot_is(0);
  assert(settings_init() == ESP_OK);
  airplay_boot_is(2);
  unsigned before = commits;
  assert(settings_set_airplay_icon(3) == ESP_ERR_INVALID_ARG);
  assert(settings_set_airplay_icon((unsigned)-1) == ESP_ERR_INVALID_ARG);
  assert(commits == before);
  assert(settings_get_airplay_icon() == 2);
  airplay_boot_is(2);

  /* Existing persisted mode values retain their exact meanings. */
  assert(settings_set_airplay_icon(1) == ESP_OK);
  assert(settings_get_airplay_icon() == 1);
  airplay_boot_is(2);
  assert(settings_init() == ESP_OK);
  airplay_boot_is(1);
  assert(settings_set_airplay_icon(0) == ESP_OK);
  assert(settings_get_airplay_icon() == 0);
  airplay_boot_is(1);
  assert(settings_init() == ESP_OK);
  airplay_boot_is(0);

  assert(nvs_set_u8(1, "airplay_icon", 255) == ESP_OK);
  assert(settings_init() == ESP_OK);
  assert(settings_get_airplay_icon() == 0);
  airplay_boot_is(0);
  assert(nvs_erase_key(1, "airplay_icon") == ESP_OK);
  assert(settings_init() == ESP_OK);
  airplay_boot_is(0);
  puts("PASS: AirPlay three presets, pending/reboot semantics, invalid modes, legacy modes and features");
}

int main(void) {
  test_airplay_presets();
  char ssids[SETTINGS_WIFI_PROFILES][33];
  reset();
  assert(settings_list_wifi_profiles(ssids, SETTINGS_WIFI_PROFILES) == 0);
  assert(settings_select_wifi_profile("missing") == ESP_ERR_NOT_FOUND);
  assert(settings_set_wifi_credentials("", "") == ESP_ERR_INVALID_ARG);
  assert(settings_set_wifi_credentials(NULL, "") == ESP_ERR_INVALID_ARG);
  assert(settings_set_wifi_credentials("fixture", NULL) == ESP_ERR_INVALID_ARG);
  assert(commits == 0);

  /* A prior single-network install migrates without losing its password. */
  assert(nvs_set_str(1, "wifi_ssid", "fixture-legacy") == ESP_OK);
  assert(nvs_set_str(1, "wifi_pass", "synthetic-legacy") == ESP_OK);
  assert(settings_list_wifi_profiles(ssids, SETTINGS_WIFI_PROFILES) == 1);
  assert(strcmp(ssids[0], "fixture-legacy") == 0);
  assert(settings_set_wifi_credentials("fixture-new", "synthetic-new") == ESP_OK);
  assert(settings_list_wifi_profiles(ssids, SETTINGS_WIFI_PROFILES) == 2);
  active_is("fixture-new", "synthetic-new");
  assert(settings_select_wifi_profile("fixture-legacy") == ESP_OK);
  active_is("fixture-legacy", "synthetic-legacy");
  assert(settings_select_wifi_profile("missing") == ESP_ERR_NOT_FOUND);
  active_is("fixture-legacy", "synthetic-legacy");

  /* Empty credentials are valid for an open AP. Boundary lengths round-trip. */
  assert(settings_set_wifi_credentials("fixture-open", "") == ESP_OK);
  active_is("fixture-open", "");
  char ssid32[33], password64[65];
  memset(ssid32, 'x', 32); ssid32[32] = 0;
  memset(password64, 'a', 64); password64[64] = 0;
  assert(settings_set_wifi_credentials(ssid32, password64) == ESP_OK);
  active_is(ssid32, password64);
  assert(settings_select_wifi_profile("fixture-open") == ESP_OK);
  active_is("fixture-open", "");
  assert(settings_set_wifi_credentials("fixture-open", "synthetic-updated") == ESP_OK);
  assert(settings_list_wifi_profiles(ssids, SETTINGS_WIFI_PROFILES) == 4);
  assert(settings_select_wifi_profile("fixture-open") == ESP_OK);
  active_is("fixture-open", "synthetic-updated");
  assert(settings_list_wifi_profiles(ssids, 1) == 1);

  /* A full list must preserve all previous credentials and the active target. */
  for (int i = 0; i < 4; i++) {
    char name[33]; snprintf(name, sizeof(name), "fixture-extra-%d", i);
    assert(settings_set_wifi_credentials(name, "synthetic-extra") == ESP_OK);
  }
  assert(settings_list_wifi_profiles(ssids, SETTINGS_WIFI_PROFILES) == 8);
  unsigned before = commits;
  assert(settings_set_wifi_credentials("fixture-overflow", "synthetic-overflow") == ESP_ERR_NO_MEM);
  assert(commits == before);
  active_is("fixture-extra-3", "synthetic-extra");
  assert(settings_set_wifi_credentials("fixture-open", "") == ESP_OK);
  active_is("fixture-open", "");
  assert(settings_list_wifi_profiles(ssids, SETTINGS_WIFI_PROFILES) == 8);

  /* Bad persisted lengths must never be treated as C strings or overwritten. */
  reset();
  unsigned char bad[SETTINGS_WIFI_PROFILES * (33 + 65)];
  memset(bad, 'x', sizeof(bad));
  assert(nvs_set_blob(1, "wifi_profiles", bad, sizeof(bad)) == ESP_OK);
  assert(settings_list_wifi_profiles(ssids, SETTINGS_WIFI_PROFILES) == 0);
  assert(settings_set_wifi_credentials("fixture", "") == ESP_ERR_INVALID_SIZE);
  assert(commits == 0);
  assert(nvs_set_blob(1, "wifi_profiles", bad, sizeof(bad) - 1) == ESP_OK);
  assert(settings_set_wifi_credentials("fixture", "") == ESP_ERR_INVALID_SIZE);
  assert(commits == 0);

  puts("PASS: WiFi profile migration, selection, open AP, maximum lengths, full-list preservation, corrupt blob rejection");
  return 0;
}
