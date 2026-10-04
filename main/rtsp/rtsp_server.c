#include "rtsp_server.h"

#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <unistd.h>

#include "audio_receiver.h"
#include "audio_output.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "rtsp_conn.h"
#include "rtsp_crypto.h"
#include "rtsp_handlers.h"
#include "rtsp_message.h"

#include "ntp_clock.h"
#include "ptp_clock.h"
#include "rtsp_events.h"
#include "dacp_client.h"
#include "settings.h"

static const char *TAG = "rtsp_server";

#define RTSP_PORT           7000
#define RTSP_BUFFER_INITIAL 4096
#define RTSP_BUFFER_LARGE   ((size_t)256 * 1024)

#define CLIENT_STACK_SIZE 8192
#define SERVER_STACK_SIZE 4096

static int server_socket = -1;
static TaskHandle_t server_task_handle = NULL;
static bool server_running = false;

// RTSP tasks are restartable. Use dynamic TCB allocation so
// reconnect/start-stop paths cannot reuse static task memory before FreeRTOS
// idle finishes deletion.

// Client slot for tracking connections
typedef struct {
  rtsp_conn_t *conn;
  TaskHandle_t task;
  int socket;
  volatile bool should_stop;
  volatile bool is_old; // Marked as old client being killed
  int takeover_slot; // Other slot to evict if this connection starts a
                     // session (-1 = none). Deferred takeover: probe
                     // connections must never kill an active stream.
  volatile bool session_started; // This connection started a session
                                 // (SETUP/ANNOUNCE/RECORD) and owns the
                                 // global playback state.
  bool shared_state_retired; // Global resources already stopped at EOF/handoff
} client_slot_t;

static client_slot_t clients[2] = {0}; // Current and old
static int current_slot = 0;
// Serialize takeover/dispatch with shared-state cleanup. A retiring task must
// finish its global cleanup before a new session can establish its clock.
static SemaphoreHandle_t session_state_mutex;

static bool slot_owns_session(const client_slot_t *slot) {
  return slot->session_started && !slot->is_old &&
         slot == &clients[current_slot];
}

static void retire_session_state(client_slot_t *slot) {
  if (slot->shared_state_retired) {
    return;
  }
  audio_receiver_stop();
  audio_output_flush();
  ntp_clock_stop();
  ptp_clock_clear();
  settings_persist_volume();
  slot->shared_state_retired = true;
}

static void disconnect_session_if_owner(client_slot_t *slot) {
  xSemaphoreTake(session_state_mutex, portMAX_DELAY);
  if (slot_owns_session(slot)) {
    dacp_clear_session();
    rtsp_events_emit(RTSP_EVENT_DISCONNECTED, NULL);
  }
  xSemaphoreGive(session_state_mutex);
}

// Forward declaration: used by process_rtsp_buffer for deferred takeover.
static void signal_old_client_stop(int old_slot);

// A request that establishes a real session and therefore replaces any
// client that was active when this connection arrived.
static bool request_takes_over(const char *header) {
  return strncasecmp(header, "SETUP ", 6) == 0 ||
         strncasecmp(header, "ANNOUNCE ", 9) == 0 ||
         strncasecmp(header, "RECORD ", 7) == 0;
}

// A request that means this connection owns playback state (audio, NTP,
// PTP, event port). Probe-only connections (/info) never get here.
static bool request_starts_session(const char *header) {
  return strncasecmp(header, "SETUP ", 6) == 0 ||
         strncasecmp(header, "ANNOUNCE ", 9) == 0 ||
         strncasecmp(header, "RECORD ", 7) == 0;
}

// Flag set by the play/pause button to tell the grace period loop
// to send a DACP resume command and keep waiting for reconnect.
static volatile bool s_resume_requested = false;

// Public API for volume control
void airplay_set_volume(float volume_db) {
  client_slot_t *c = &clients[current_slot];
  if (c->conn && !c->is_old) {
    rtsp_conn_set_volume(c->conn, volume_db);
  }
}

int32_t airplay_get_volume_q15(void) {
  client_slot_t *c = &clients[current_slot];
  if (c->conn && !c->is_old) {
    return rtsp_conn_get_volume_q15(c->conn);
  }
  return 16384; // 50% volume for new clients
}

void rtsp_server_request_resume(void) {
  s_resume_requested = true;
}

// Helper to grow buffer
static uint8_t *grow_buffer(uint8_t *old_buf, size_t old_size, size_t new_size,
                            size_t data_len) {
  (void)old_size;
  uint8_t *new_buf =
      heap_caps_malloc(new_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!new_buf) {
    new_buf = malloc(new_size);
  }
  if (!new_buf) {
    return NULL;
  }
  if (old_buf && data_len > 0) {
    memcpy(new_buf, old_buf, data_len);
  }
  free(old_buf);
  return new_buf;
}

// Process buffered RTSP requests
static void process_rtsp_buffer(client_slot_t *slot, uint8_t *buffer,
                                size_t *buf_len) {
  while (*buf_len > 0 && !slot->should_stop) {
    const uint8_t *header_end = rtsp_find_header_end(buffer, *buf_len);
    if (!header_end) {
      break;
    }

    size_t header_len = (size_t)(header_end - buffer) + 4;
    char *header_str = malloc(header_len + 1);
    if (!header_str) {
      *buf_len = 0;
      break;
    }
    memcpy(header_str, buffer, header_len);
    header_str[header_len] = '\0';

    int content_len = rtsp_parse_content_length(header_str);
    if (content_len < 0) {
      content_len = 0;
    }

    size_t total_len = header_len + (size_t)content_len;
    if (total_len > RTSP_BUFFER_LARGE || *buf_len < total_len) {
      free(header_str);
      if (total_len > RTSP_BUFFER_LARGE) {
        *buf_len = 0;
      }
      break;
    }

    xSemaphoreTake(session_state_mutex, portMAX_DELAY);
    if (slot->should_stop || slot->is_old) {
      xSemaphoreGive(session_state_mutex);
      free(header_str);
      break;
    }

    // Session bookkeeping for a complete request. iOS opens short-lived probe
    // connections (GET /info?txtAirPlay&txtRAOP) while a stream is playing;
    // those must not disturb the active session.  A connection only takes
    // over the other slot — and only claims playback state — once it
    // actually starts a session.
    if (request_takes_over(header_str) && slot->takeover_slot >= 0) {
      client_slot_t *old = &clients[slot->takeover_slot];
      if (slot_owns_session(old)) {
        // Retire the old receiver before new SETUP can select/destroy its
        // decoder or observe a still-running buffered stream. Late old-task
        // cleanup must not repeat this against the new session's resources.
        retire_session_state(old);
        dacp_clear_session();
        rtsp_events_emit(RTSP_EVENT_DISCONNECTED, NULL);
        rtsp_stop_event_port_task();
        ptp_clock_init(); // v1 may have stopped PTP to free socket slots
      }
      signal_old_client_stop(slot->takeover_slot);
      slot->takeover_slot = -1;
    }
    if (request_starts_session(header_str) && !slot->session_started) {
      slot->session_started = true;
      // Volume/control state follows the live session, not the most
      // recently accepted probe/pairing connection.
      current_slot = (int)(slot - clients);
    }

    // Null-terminate so strcasestr in parse_raw_header won't read past
    // the message boundary (buffer capacity > total_len).
    uint8_t saved = buffer[total_len];
    buffer[total_len] = '\0';
    rtsp_dispatch(slot->socket, slot->conn, buffer, total_len);
    buffer[total_len] = saved;
    xSemaphoreGive(session_state_mutex);
    free(header_str);

    if (*buf_len > total_len) {
      memmove(buffer, buffer + total_len, *buf_len - total_len);
    }
    *buf_len -= total_len;
  }
}

// Client task
static void client_task(void *pvParameters) {
  int slot_idx = (int)(intptr_t)pvParameters;
  client_slot_t *slot = &clients[slot_idx];

  // Create connection state
  rtsp_conn_t *conn = rtsp_conn_create();
  if (!conn) {
    ESP_LOGE(TAG, "Failed to create connection state");
    close(slot->socket);
    slot->socket = -1;
    slot->task = NULL;
    vTaskDelete(NULL);
    return;
  }
  slot->conn = conn;

  // Get client IP address for timing requests
  struct sockaddr_in peer_addr;
  socklen_t peer_len = sizeof(peer_addr);
  if (getpeername(slot->socket, (struct sockaddr *)&peer_addr, &peer_len) ==
      0) {
    conn->client_ip = peer_addr.sin_addr.s_addr;
    ESP_LOGI(TAG, "Client IP: %u.%u.%u.%u",
             (unsigned int)(conn->client_ip & 0xFF),
             (unsigned int)((conn->client_ip >> 8) & 0xFF),
             (unsigned int)((conn->client_ip >> 16) & 0xFF),
             (unsigned int)((conn->client_ip >> 24) & 0xFF));
  }

  // Allocate buffer
  size_t buf_capacity = RTSP_BUFFER_INITIAL;
  uint8_t *buffer = malloc(buf_capacity);
  if (!buffer) {
    ESP_LOGE(TAG, "Failed to allocate buffer");
    rtsp_conn_free(conn);
    slot->conn = NULL;
    close(slot->socket);
    slot->socket = -1;
    slot->task = NULL;
    vTaskDelete(NULL);
    return;
  }

  size_t buf_len = 0;

  // Socket timeout for stop signal responsiveness
  struct timeval tv = {.tv_sec = 1, .tv_usec = 0};
  setsockopt(slot->socket, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  // Dispatch holds the session-state mutex. Bound writes so a stalled control
  // client cannot keep a successor or owner cleanup waiting indefinitely.
  struct timeval send_tv = {.tv_sec = 5, .tv_usec = 0};
  if (setsockopt(slot->socket, SOL_SOCKET, SO_SNDTIMEO, &send_tv,
                 sizeof(send_tv)) != 0) {
    ESP_LOGE(TAG, "Failed to bound RTSP send timeout: %d", errno);
    goto cleanup;
  }

  // Disable Nagle: RTSP control commands (volume, pause) are tiny and must
  // not wait for coalescing/delayed-ACK, which adds tens to hundreds of ms of
  // latency to every command on this connection.
  int nodelay = 1;
  setsockopt(slot->socket, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));

  while (server_running && !slot->should_stop) {
    if (conn->encrypted_mode) {
      // Encrypted mode
      while (server_running && conn->encrypted_mode && !slot->should_stop) {
        if (buf_len >= buf_capacity - 1024) {
          size_t new_cap = buf_capacity < RTSP_BUFFER_LARGE ? RTSP_BUFFER_LARGE
                                                            : buf_capacity * 2;
          if (new_cap > RTSP_BUFFER_LARGE) {
            goto cleanup;
          }
          uint8_t *new_buf =
              grow_buffer(buffer, buf_capacity, new_cap, buf_len);
          if (!new_buf) {
            goto cleanup;
          }
          buffer = new_buf;
          buf_capacity = new_cap;
        }

        int block_len = rtsp_crypto_read_block(
            slot->socket, conn, buffer + buf_len, buf_capacity - buf_len);
        if (block_len <= 0) {
          if (slot->should_stop || (errno != EAGAIN && errno != EWOULDBLOCK)) {
            ESP_LOGI(TAG, "Encrypted control ended: slot=%d result=%d errno=%d stop=%d",
                     slot_idx, block_len, errno, slot->should_stop);
            goto cleanup;
          }
          continue;
        }

        buf_len += (size_t)block_len;
        process_rtsp_buffer(slot, buffer, &buf_len);
      }
      goto cleanup;
    }

    // Plain-text mode
    if (buf_len >= buf_capacity - 1024) {
      size_t new_cap = buf_capacity < RTSP_BUFFER_LARGE ? RTSP_BUFFER_LARGE
                                                        : buf_capacity * 2;
      if (new_cap > RTSP_BUFFER_LARGE) {
        break;
      }
      uint8_t *new_buf = grow_buffer(buffer, buf_capacity, new_cap, buf_len);
      if (!new_buf) {
        break;
      }
      buffer = new_buf;
      buf_capacity = new_cap;
    }

    ssize_t recv_len =
        recv(slot->socket, buffer + buf_len, buf_capacity - buf_len, 0);
    if (recv_len <= 0) {
      if (recv_len < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
        continue;
      }
      ESP_LOGI(TAG, "Plain control ended: slot=%d result=%d errno=%d stop=%d",
               slot_idx, (int)recv_len, errno, slot->should_stop);
      break;
    }
    buf_len += (size_t)recv_len;
    process_rtsp_buffer(slot, buffer, &buf_len);
  }

cleanup:
  ESP_LOGI(TAG, "Client slot %d disconnected", slot_idx);
  free(buffer);
  close(slot->socket);
  slot->socket = -1;

  // A connection that never started a session (an iOS /info probe, a port
  // scan, ...) must not touch global playback state — an active stream in
  // the other slot keeps running untouched.
  xSemaphoreTake(session_state_mutex, portMAX_DELAY);
  if (!slot_owns_session(slot)) {
    xSemaphoreGive(session_state_mutex);
    goto session_done;
  }

  // Immediate: stop shared resources once, before a successor can take over.
  retire_session_state(slot);

  bool has_dacp_remote = conn && conn->protocol_version == 1 &&
                         conn->dacp_id[0] != '\0' &&
                         conn->active_remote[0] != '\0';
  if (has_dacp_remote && !slot->should_stop) {
    s_resume_requested = false;
    rtsp_events_emit(RTSP_EVENT_PAUSED, NULL);
  }
  xSemaphoreGive(session_state_mutex);

  // iOS v1 pause handling needs DACP to distinguish pause from disconnect.
  // Third-party RAOP clients often have no DACP remote; disconnect them
  // immediately instead of delaying slot cleanup with an iOS-only grace path.
  if (has_dacp_remote) {
    if (!slot->should_stop) {
      // Phase 1: let mDNS settle (3 s), but exit early on resume or reconnect
      for (int i = 0; i < 6 && !slot->should_stop; i++) {
        vTaskDelay(pdMS_TO_TICKS(500));
        if (s_resume_requested) {
          ESP_LOGI(TAG,
                   "Resume requested during Phase 1 — skipping to Phase 2");
          break;
        }
      }

      // Phase 2: wait for reconnect as long as DACP service is advertised.
      // Re-probe every ~5 s. The phone unadvertises the service when the
      // user switches away, so disappearance = genuine disconnect.
      if (!slot->should_stop) {
        bool stay = dacp_probe_service() || s_resume_requested;
        if (s_resume_requested) {
          s_resume_requested = false;
          ESP_LOGI(TAG, "Resume requested via button — waiting for reconnect");
          stay = true;
        }
        if (stay) {
          ESP_LOGI(TAG, "DACP still advertised — waiting for reconnect");
        }
        while (stay && !slot->should_stop) {
          // Wait 5 s between probes (10 × 500 ms), checking flags each tick
          for (int i = 0; i < 10 && !slot->should_stop; i++) {
            vTaskDelay(pdMS_TO_TICKS(500));
            if (s_resume_requested) {
              s_resume_requested = false;
              ESP_LOGI(TAG,
                       "Resume requested via button — extending grace period");
            }
          }
          if (slot->should_stop) {
            break;
          }
          // Re-probe: still advertised?
          stay = dacp_probe_service();
          if (stay) {
            ESP_LOGD(TAG, "DACP still advertised — continuing wait");
          } else {
            ESP_LOGI(TAG, "DACP service gone — genuine disconnect");
          }
        }
      }

      if (slot->is_old) {
        // New client connected during grace period — treat as reconnect
        ESP_LOGI(TAG, "Client reconnected during grace period");
      } else {
        ESP_LOGI(TAG, "Grace period expired — full disconnect");
        disconnect_session_if_owner(slot);
      }
    } else {
      // Forcefully stopped (server shutdown or replaced by new client)
      disconnect_session_if_owner(slot);
    }
  } else {
    // v2 / unknown — no grace period, clear immediately.
    disconnect_session_if_owner(slot);
  }

  // When being replaced by a new client (is_old), skip global state changes —
  // the new session's SETUP already manages PTP and the event port task.
  xSemaphoreTake(session_state_mutex, portMAX_DELAY);
  if (slot_owns_session(slot)) {
    ptp_clock_init(); // Restart PTP (stopped during v1 SETUP to free sockets)
    rtsp_stop_event_port_task();
  }
  xSemaphoreGive(session_state_mutex);

session_done:
  // The event task holds this listener descriptor, not the connection. Stop a
  // matching old task before closing the descriptor, but never a new listener.
  xSemaphoreTake(session_state_mutex, portMAX_DELAY);
  if (conn->event_socket >= 0 &&
      rtsp_event_port_listen_socket() == conn->event_socket) {
    rtsp_stop_event_port_task();
  }
  if (conn->event_socket >= 0) {
    close(conn->event_socket);
    conn->event_socket = -1;
  }
  xSemaphoreGive(session_state_mutex);

  rtsp_conn_free(conn);

  slot->conn = NULL;
  slot->socket = -1;
  slot->should_stop = false;
  slot->is_old = false;
  slot->session_started = false;
  slot->shared_state_retired = false;
  slot->takeover_slot = -1;
  // Publish the free slot only after all state from this task is retired.
  slot->task = NULL;

  vTaskDelete(NULL);
}

// Signal old client to stop (non-blocking)
static void signal_old_client_stop(int old_slot) {
  client_slot_t *old = &clients[old_slot];
  if (old->task == NULL) {
    return;
  }

  ESP_LOGI(TAG, "Signaling old client to stop");
  old->is_old = true;
  old->should_stop = true;

  // Shutdown socket to unblock recv
  if (old->socket >= 0) {
    shutdown(old->socket, SHUT_RDWR);
  }
  // Task will clean itself up
}

static void server_task(void *pvParameters) {
  (void)pvParameters;

  struct sockaddr_in server_addr, client_addr;
  socklen_t client_addr_len = sizeof(client_addr);

  // Initialize slots
  for (int i = 0; i < 2; i++) {
    clients[i].socket = -1;
    clients[i].conn = NULL;
    clients[i].task = NULL;
    clients[i].should_stop = false;
    clients[i].is_old = false;
    clients[i].session_started = false;
    clients[i].shared_state_retired = false;
    clients[i].takeover_slot = -1;
  }

  server_socket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (server_socket < 0) {
    ESP_LOGE(TAG, "Failed to create socket: %d", errno);
    server_task_handle = NULL;
    vTaskDelete(NULL);
    return;
  }

  int opt = 1;
  setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

  memset(&server_addr, 0, sizeof(server_addr));
  server_addr.sin_family = AF_INET;
  server_addr.sin_addr.s_addr = htonl(INADDR_ANY);
  server_addr.sin_port = htons(RTSP_PORT);

  if (bind(server_socket, (struct sockaddr *)&server_addr,
           sizeof(server_addr)) < 0) {
    ESP_LOGE(TAG, "Failed to bind: %d", errno);
    close(server_socket);
    server_socket = -1;
    server_task_handle = NULL;
    vTaskDelete(NULL);
    return;
  }

  if (listen(server_socket, 5) < 0) {
    ESP_LOGE(TAG, "Failed to listen: %d", errno);
    close(server_socket);
    server_socket = -1;
    server_task_handle = NULL;
    vTaskDelete(NULL);
    return;
  }

  ESP_LOGI(TAG, "RTSP server listening on port %d", RTSP_PORT);
  server_running = true;

  while (server_running) {
    int new_socket = accept(server_socket, (struct sockaddr *)&client_addr,
                            &client_addr_len);
    if (new_socket < 0) {
      if (server_running) {
        ESP_LOGE(TAG, "Failed to accept: %d", errno);
      }
      continue;
    }

    ESP_LOGI(TAG, "New client connected");

    // Pick a free slot.  Do NOT tear down the other client here: iOS opens
    // short-lived probe connections (GET /info?txtAirPlay&txtRAOP) while a
    // stream is playing, and killing the active session on every probe was
    // what caused random dropouts mid-playback.  A real session only takes
    // over once it actually starts — see process_rtsp_buffer().
    int new_slot = -1;
    for (int attempt = 0; attempt < 20; attempt++) {
      if (clients[1 - current_slot].task == NULL) {
        new_slot = 1 - current_slot;
        break;
      }
      if (clients[current_slot].task == NULL) {
        new_slot = current_slot;
        break;
      }
      // Both slots busy (active stream + lingering probe/grace task) — wait
      // briefly for one to free up, then refuse instead of killing a session.
      vTaskDelay(pdMS_TO_TICKS(50));
    }
    if (new_slot < 0) {
      ESP_LOGW(TAG, "Both client slots busy — refusing new connection");
      close(new_socket);
      continue;
    }

    // Setup new slot
    clients[new_slot].socket = new_socket;
    clients[new_slot].should_stop = false;
    clients[new_slot].is_old = false;
    clients[new_slot].session_started = false;
    clients[new_slot].shared_state_retired = false;
    // If the other slot is occupied, remember it so this connection can
    // evict it when (and only when) it establishes a session.
    clients[new_slot].takeover_slot =
        (clients[1 - new_slot].task != NULL) ? (1 - new_slot) : -1;

    // Start new client task immediately.
    clients[new_slot].task = NULL;
    BaseType_t task_ret =
        xTaskCreate(client_task, "rtsp_client", CLIENT_STACK_SIZE,
                    (void *)(intptr_t)new_slot, 5, &clients[new_slot].task);
    if (task_ret != pdPASS || clients[new_slot].task == NULL) {
      ESP_LOGE(TAG, "Failed to create client task");
      close(new_socket);
      clients[new_slot].socket = -1;
      clients[new_slot].takeover_slot = -1;
    }
    // Note: current_slot deliberately does NOT change here.  It tracks the
    // slot that most recently established a session (updated in
    // process_rtsp_buffer), so probe connections cannot steal volume /
    // control state from the live player.
  }

  // Stop all clients
  for (int i = 0; i < 2; i++) {
    if (clients[i].task != NULL) {
      clients[i].should_stop = true;
      if (clients[i].socket >= 0) {
        shutdown(clients[i].socket, SHUT_RDWR);
      }
    }
  }

  vTaskDelay(pdMS_TO_TICKS(500));

  if (server_socket >= 0) {
    close(server_socket);
    server_socket = -1;
  }

  server_task_handle = NULL;
  vTaskDelete(NULL);
}

static bool rtsp_server_wait_for_task_stopped(int timeout_ticks) {
  while (server_task_handle != NULL && timeout_ticks-- > 0) {
    vTaskDelay(pdMS_TO_TICKS(50));
  }
  return server_task_handle == NULL;
}

static bool rtsp_server_wait_for_clients_stopped(int timeout_ticks) {
  // Workers release their private resources before publishing task == NULL.
  // Do not hold session_state_mutex here: their cleanup needs that mutex.
  while ((clients[0].task != NULL || clients[1].task != NULL) &&
         timeout_ticks-- > 0) {
    vTaskDelay(pdMS_TO_TICKS(50));
  }
  return clients[0].task == NULL && clients[1].task == NULL;
}

esp_err_t rtsp_server_start(void) {
  if (server_task_handle != NULL) {
    if (server_running) {
      return ESP_ERR_INVALID_STATE;
    }
    ESP_LOGW(TAG, "RTSP server task still stopping, waiting");
    if (!rtsp_server_wait_for_task_stopped(40)) {
      ESP_LOGE(TAG, "Previous RTSP server task did not stop");
      return ESP_ERR_INVALID_STATE;
    }
  }

  if (!rtsp_server_wait_for_clients_stopped(100)) {
    ESP_LOGE(TAG, "Previous RTSP clients did not stop; refusing restart");
    return ESP_ERR_INVALID_STATE;
  }

  if (!session_state_mutex) {
    session_state_mutex = xSemaphoreCreateMutex();
    if (!session_state_mutex) {
      return ESP_ERR_NO_MEM;
    }
  }

  BaseType_t task_ret =
      xTaskCreate(server_task, "rtsp_server", SERVER_STACK_SIZE, NULL, 5,
                  &server_task_handle);
  if (task_ret != pdPASS || server_task_handle == NULL) {
    return ESP_FAIL;
  }

  return ESP_OK;
}

bool rtsp_server_is_running(void) {
  return server_running;
}

void rtsp_server_stop(void) {
  server_running = false;

  if (server_socket >= 0) {
    shutdown(server_socket, SHUT_RDWR);
    close(server_socket);
    server_socket = -1;
  }

  if (server_task_handle != NULL) {
    if (!rtsp_server_wait_for_task_stopped(40)) {
      ESP_LOGW(TAG, "RTSP server task did not exit within timeout");
    }
  }
}
