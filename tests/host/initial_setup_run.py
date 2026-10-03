#!/usr/bin/env python3
"""Test the actual initial SETUP builder and parse its output independently."""
import ast
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
# Reuse only platform declarations, without running the broader host suite.
tree = ast.parse((ROOT / "tests/host/run.py").read_text())
HEADERS = next(
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "HEADERS"
            for target in node.targets)
)
PROBE = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "plist.h"
#include "audio_stream.h"

bool audio_stream_uses_buffer(audio_stream_type_t type) {
  return type == AUDIO_STREAM_BUFFERED;
}
const char *settings_get_airplay_model(void) { return "Test receiver"; }
const char *settings_get_airplay_manufacturer(void) { return "Test maker"; }

static void save(const char *prefix, size_t index, const uint8_t *data,
                 size_t length) {
  char path[1024];
  int n = snprintf(path, sizeof(path), "%s.%zu.plist", prefix, index);
  assert(n > 0 && (size_t)n < sizeof(path));
  FILE *file = fopen(path, "wb");
  assert(file && fwrite(data, 1, length, file) == length);
  assert(fclose(file) == 0);
}

static size_t check_capacity(const char *address, uint16_t port,
                             uint8_t *reference) {
  size_t length = bplist_build_initial_setup_with_timing_peer(
      reference, 512, port, address);
  assert(length > 0 && length <= 512);
  for (size_t capacity = 0; capacity <= 512; capacity++) {
    uint8_t guarded[514];
    memset(guarded, 0xA5, sizeof(guarded));
    size_t written = bplist_build_initial_setup_with_timing_peer(
        guarded + 1, capacity, port, address);
    size_t minimum = address ? length : 100; // legacy minimum is preserved
    assert(written == (capacity < minimum ? 0 : length));
    assert(guarded[0] == 0xA5);
    for (size_t i = capacity + 1; i < sizeof(guarded); i++) {
      assert(guarded[i] == 0xA5);
    }
    if (written) assert(memcmp(guarded + 1, reference, written) == 0);
    if (!address) {
      memset(guarded, 0xA5, sizeof(guarded));
      written = bplist_build_initial_setup(guarded + 1, capacity, port);
      assert(written == (capacity < 100 ? 0 : length));
      assert(guarded[0] == 0xA5);
      for (size_t i = capacity + 1; i < sizeof(guarded); i++) {
        assert(guarded[i] == 0xA5);
      }
      if (written) assert(memcmp(guarded + 1, reference, written) == 0);
    }
  }
  return length;
}

int main(int argc, char **argv) {
  assert(argc == 2);
  const uint16_t ports[] = {0, 1, 255, 256, 65535};
  const char *addresses[] = {NULL, "192.0.2.123",
      "2001:db8:ffff:ffff:ffff:ffff:ffff:ffff"};
  uint8_t reference[512];
  size_t index = 0;
  for (size_t i = 0; i < sizeof(addresses) / sizeof(addresses[0]); i++) {
    for (size_t j = 0; j < sizeof(ports) / sizeof(ports[0]); j++) {
      size_t length = check_capacity(addresses[i], ports[j], reference);
      save(argv[1], index++, reference, length);
    }
  }
  const char *invalid[] = {"", "\xC2", "\xED\xA0\x80", "\xC0\xAF"};
  for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); i++) {
    for (size_t capacity = 0; capacity <= 512; capacity++) {
      uint8_t guarded[514];
      memset(guarded, 0xA5, sizeof(guarded));
      assert(bplist_build_initial_setup_with_timing_peer(
          guarded + 1, capacity, 65535, invalid[i]) == 0);
      assert(guarded[0] == 0xA5);
      for (size_t k = capacity + 1; k < sizeof(guarded); k++) {
        assert(guarded[k] == 0xA5);
      }
    }
  }
  assert(bplist_build_initial_setup(NULL, 512, 65535) == 0);
  assert(bplist_build_initial_setup_with_timing_peer(
      NULL, 512, 65535, "192.0.2.123") == 0);
  return 0;
}
'''

# Exact pre-fix two-field output, with eventPort=65535. Only these two value
# bytes vary with the port; this guards the legacy response's wire encoding.
LEGACY_65535 = bytes.fromhex(
    "62706c6973743030596576656e74506f72745a74696d696e67506f727411ffff"
    "1000d20001020308121d202200000000000001010000000000000005000000"
    "00000000040000000000000027"
)

with tempfile.TemporaryDirectory(prefix="cedric-initial-setup-") as directory:
    tmp = Path(directory)
    for name, text in HEADERS.items():
        target = tmp / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    probe = tmp / "probe.c"
    probe.write_text(PROBE)
    binary = tmp / "probe"
    subprocess.run([
        os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
        "-Werror", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        "-I" + str(tmp), "-Imain", "-Imain/audio", "-Imain/plist",
        "-Imain/network", str(probe), "main/plist/bplist_builder.c",
        "main/network/airplay_advertisement.c", "-o", str(binary),
    ], cwd=ROOT, check=True)
    output = tmp / "response"
    subprocess.run([str(binary), str(output)], check=True)
    index = 0
    sizes = {}
    for address in (None, "192.0.2.123",
                    "2001:db8:ffff:ffff:ffff:ffff:ffff:ffff"):
        for port in (0, 1, 255, 256, 65535):
            payload = Path(str(output) + f".{index}.plist").read_bytes()
            index += 1
            expected = {"eventPort": port, "timingPort": 0}
            if address is not None:
                expected["timingPeerInfo"] = {
                    "Addresses": [address], "ID": address}
            else:
                legacy = bytearray(LEGACY_65535)
                legacy[30:32] = port.to_bytes(2, "big")
                assert payload == legacy
            assert plistlib.loads(payload) == expected
            sizes[address or "legacy"] = len(payload)
    print("PASS: actual initial SETUP builder; exact legacy bytes, IPv4/IPv6 PTP;")
    print("      five port boundaries; every capacity 0..512; canaries + ASan/UBSan;")
    print("      independent plistlib decoding; empty/invalid UTF8/null rejection")
    print("Sizes: " + str(sizes))
