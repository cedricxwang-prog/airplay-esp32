#include "esp_log.h"
#include "esp_mac.h"
#include "mdns.h"
#include <stdio.h>
#include <string.h>

#include "hap.h"
#include "mdns_airplay.h"
#include "rtsp_handlers.h"
#include "wifi.h"
#include "settings.h"

static const char *TAG = "mdns_airplay";

// Longest single DNS label (RFC 1035). The mDNS component caps names at
// MDNS_NAME_MAX_LEN, which is never smaller than this.
#define MDNS_HOSTNAME_MAX_LEN 63

// Feature flags are defined in rtsp_handlers.h (shared with /info handler)

void mdns_airplay_init(void) {
  char mac_str[18];
  char device_id[18];
  char service_name[80];
  char device_name[65];
  char hostname[MDNS_HOSTNAME_MAX_LEN + 1];

  // Get device name from settings. This is the user-facing name and stays
  // UTF-8 for the service instance names below; only the hostname is
  // restricted to ASCII.
  settings_get_device_name(device_name, sizeof(device_name));
  settings_device_name_to_hostname(device_name, hostname, sizeof(hostname));

  // Get MAC address
  wifi_get_mac_str(mac_str, sizeof(mac_str));
  strncpy(device_id, mac_str, sizeof(device_id));

  // One TXT snapshot is shared with the /info serializers.
  const uint8_t *pk = hap_get_public_key();
  airplay_advertisement_t advertisement;
  uint64_t features = ((uint64_t)AIRPLAY_FEATURES_HI << 32) | AIRPLAY_FEATURES_LO;
  if (!airplay_advertisement_build(&advertisement, device_id, pk, 32, features,
                                   settings_get_airplay_model(),
                                   settings_get_airplay_manufacturer())) {
    ESP_LOGE(TAG, "Failed to build AirPlay advertisement");
    return;
  }

  // Create service name for RAOP: <mac>@<name>
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  snprintf(service_name, sizeof(service_name), "%02X%02X%02X%02X%02X%02X@%s",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], device_name);

  // Initialize mDNS
  ESP_ERROR_CHECK(mdns_init());

  // Set hostname. A bad name must not panic the device, so this is logged
  // like the service registrations below rather than ESP_ERROR_CHECK'd.
  esp_err_t err_host = mdns_hostname_set(hostname);
  if (err_host != ESP_OK) {
    ESP_LOGE(TAG, "Failed to set mDNS hostname '%s': %s", hostname,
             esp_err_to_name(err_host));
  } else {
    ESP_LOGI(TAG, "mDNS hostname: %s.local (device name: %s)", hostname,
             device_name);
  }

#ifndef CONFIG_AIRPLAY_FORCE_V1
  // ========================================
  // _airplay._tcp service (port 7000)
  // Only registered for AirPlay 2 mode
  // ========================================
  mdns_txt_item_t airplay_txt[AIRPLAY_TXT_MAX_ITEMS];
  for (size_t i = 0; i < advertisement.airplay_count; i++) {
    airplay_txt[i].key = advertisement.airplay[i].key;
    airplay_txt[i].value = advertisement.airplay[i].value;
  }

  esp_err_t err =
      mdns_service_add(device_name, "_airplay", "_tcp", 7000, airplay_txt,
                       advertisement.airplay_count);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to add _airplay._tcp service: %s",
             esp_err_to_name(err));
  }
#endif

  // ========================================
  // _raop._tcp service (port 7000)
  // RAOP = Remote Audio Output Protocol
  // Service name format: <MAC>@<DeviceName>
  // ========================================
  mdns_txt_item_t raop_txt[AIRPLAY_TXT_MAX_ITEMS];
  for (size_t i = 0; i < advertisement.raop_count; i++) {
    raop_txt[i].key = advertisement.raop[i].key;
    raop_txt[i].value = advertisement.raop[i].value;
  }

  esp_err_t err_raop =
      mdns_service_add(service_name, "_raop", "_tcp", 7000, raop_txt,
                       advertisement.raop_count);
  if (err_raop != ESP_OK) {
    ESP_LOGE(TAG, "Failed to add _raop._tcp service: %s",
             esp_err_to_name(err_raop));
  }
}
