#!/usr/bin/env python3
"""Dependency-free AirPlay 2 integration diagnostic for an explicit receiver.

No WiFi credentials, NVS settings, audio files or OTA images are accessed.
The network run creates a temporary playback-control session and may replace
an existing player, so run it only when playback is stopped. The default run
tests transient SRP, encrypted /info, initial SETUP and an idle event socket.
It prints stage summaries, never SRP/session secrets or raw binary requests.
--expect-ptp-peer requires the initial SETUP's timingPeerInfo to identify the
receiver's actual peer IP. Without it, a legacy response with missing peer
metadata is reported as unverified, even if the synthetic control flow works.
This is not a Mac/Spotify playback, audio rendering or synchronization test.
--self-test performs only offline RFC 8439, SRP and protocol validation checks.
The compact pure-Python crypto is diagnostic code, not a production library.
"""
import argparse
import hashlib
import hmac
import ipaddress
import json
import plistlib
import re
import secrets
import socket
import struct
import sys
import time
from pathlib import Path


def digest(*parts):
    return hashlib.sha512(b''.join(parts)).digest()


def hkdf(key, salt, info, length=32):
    prk = hmac.new(salt, key, hashlib.sha512).digest()
    result, last, counter = b'', b'', 1
    while len(result) < length:
        last = hmac.new(prk, last + info + bytes([counter]), hashlib.sha512).digest()
        result += last
        counter += 1
    return result[:length]


def rotate(value, bits):
    return ((value << bits) | (value >> (32 - bits))) & 0xffffffff


def chacha_block(key, nonce, counter):
    assert len(key) == 32 and len(nonce) == 12
    initial = list(struct.unpack('<4I', b'expand 32-byte k') +
                   struct.unpack('<8I', key) + (counter,) +
                   struct.unpack('<3I', nonce))
    words = initial.copy()

    def quarter(a, b, c, d):
        words[a] = (words[a] + words[b]) & 0xffffffff
        words[d] = rotate(words[d] ^ words[a], 16)
        words[c] = (words[c] + words[d]) & 0xffffffff
        words[b] = rotate(words[b] ^ words[c], 12)
        words[a] = (words[a] + words[b]) & 0xffffffff
        words[d] = rotate(words[d] ^ words[a], 8)
        words[c] = (words[c] + words[d]) & 0xffffffff
        words[b] = rotate(words[b] ^ words[c], 7)

    for _ in range(10):
        quarter(0, 4, 8, 12)
        quarter(1, 5, 9, 13)
        quarter(2, 6, 10, 14)
        quarter(3, 7, 11, 15)
        quarter(0, 5, 10, 15)
        quarter(1, 6, 11, 12)
        quarter(2, 7, 8, 13)
        quarter(3, 4, 9, 14)
    return struct.pack('<16I', *((a + b) & 0xffffffff for a, b in zip(words, initial)))


def chacha_xor(key, nonce, data):
    result = bytearray()
    for offset in range(0, len(data), 64):
        block = chacha_block(key, nonce, 1 + offset // 64)
        result.extend(a ^ b for a, b in zip(data[offset:offset + 64], block))
    return bytes(result)


def poly1305(key, data):
    r = int.from_bytes(key[:16], 'little') & 0x0ffffffc0ffffffc0ffffffc0fffffff
    s = int.from_bytes(key[16:], 'little')
    accumulator = 0
    for offset in range(0, len(data), 16):
        number = int.from_bytes(data[offset:offset + 16] + b'\x01', 'little')
        accumulator = ((accumulator + number) * r) % ((1 << 130) - 5)
    return ((accumulator + s) % (1 << 128)).to_bytes(16, 'little')


def auth_tag(key, nonce, aad, ciphertext):
    pad = lambda data: b'\0' * ((-len(data)) % 16)
    mac = aad + pad(aad) + ciphertext + pad(ciphertext)
    mac += struct.pack('<QQ', len(aad), len(ciphertext))
    return poly1305(chacha_block(key, nonce, 0)[:32], mac)


def aead_encrypt(key, nonce, plaintext, aad):
    ciphertext = chacha_xor(key, nonce, plaintext)
    return ciphertext + auth_tag(key, nonce, aad, ciphertext)


def aead_decrypt(key, nonce, encrypted, aad):
    if len(encrypted) < 16:
        raise ValueError('encrypted frame is shorter than its tag')
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    if not hmac.compare_digest(tag, auth_tag(key, nonce, aad, ciphertext)):
        raise ValueError('encrypted frame authentication failed')
    return chacha_xor(key, nonce, ciphertext)


def tlv_encode(*items):
    out = bytearray()
    for kind, value in items:
        for offset in range(0, max(1, len(value)), 255):
            part = value[offset:offset + 255]
            out.extend(bytes([kind, len(part)]) + part)
    return bytes(out)


def tlv_decode(data):
    result, pos = {}, 0
    while pos < len(data):
        if pos + 2 > len(data):
            raise ValueError('truncated TLV header')
        kind, length = data[pos:pos + 2]
        pos += 2
        if pos + length > len(data):
            raise ValueError('truncated TLV value')
        result[kind] = result.get(kind, b'') + data[pos:pos + length]
        pos += length
    return result


def srp_parameters(root):
    source = (root / 'main/hap/srp.c').read_text()
    prime_source = source.split('static const uint8_t srp_N[] = {', 1)[1].split('};', 1)[0]
    raw = bytes(int(value, 16) for value in re.findall(r'0x([0-9a-fA-F]{2})', prime_source))
    assert len(raw) == 384
    return int.from_bytes(raw, 'big'), 5, raw


def minimal(number):
    return number.to_bytes(max(1, (number.bit_length() + 7) // 8), 'big')


def srp_client(N, g, Nraw, salt, Braw):
    if len(salt) != 16 or len(Braw) != len(Nraw):
        raise ValueError('invalid SRP salt/public-key lengths')
    a = int.from_bytes(secrets.token_bytes(32), 'big')
    A = pow(g, a, N)
    B = int.from_bytes(Braw, 'big')
    if B % N == 0:
        raise ValueError('invalid SRP server public key')
    pad = lambda value: value.to_bytes(len(Nraw), 'big')
    k = int.from_bytes(digest(Nraw, pad(g)), 'big')
    u = int.from_bytes(digest(pad(A), pad(B)), 'big')
    x = int.from_bytes(digest(salt, digest(b'Pair-Setup:3939')), 'big')
    S = pow((B - k * pow(g, x, N)) % N, a + u * x, N)
    K = digest(minimal(S))
    xor = bytes(a ^ b for a, b in zip(digest(Nraw), digest(minimal(g))))
    # The current firmware's proof calculation uses minimal integer forms.
    M1 = digest(xor, digest(b'Pair-Setup'), salt.lstrip(b'\0') or b'\0',
                minimal(A), minimal(B), K)
    M2 = digest(minimal(A), M1, K)
    return pad(A), M1, M2, K


def parse_response_header(head, expected_cseq):
    """Strict response framing shared by real sockets and offline fixtures."""
    lines = head.decode('ascii').split('\r\n')
    status_line = re.fullmatch(r'RTSP/1\.0 ([0-9]{3}) [^\r\n]+', lines[0])
    if not status_line:
        raise ValueError('expected a valid RTSP/1.0 response status line')
    headers = {}
    for line in lines[1:]:
        if ':' not in line:
            raise ValueError('malformed RTSP response header')
        key, value = line.split(':', 1)
        if not re.fullmatch(r'[A-Za-z0-9-]+', key):
            raise ValueError('invalid RTSP header name')
        key = key.lower()
        if key in headers:
            raise ValueError('duplicate RTSP response header: ' + key)
        headers[key] = value.strip()
    cseq = headers.get('cseq', '')
    if not cseq.isdecimal() or int(cseq) != expected_cseq:
        raise ValueError('RTSP response CSeq does not match the request')
    length = headers.get('content-length', '0')
    if not length.isdecimal() or not 0 <= int(length) <= 1024 * 1024:
        raise ValueError('invalid RTSP Content-Length')
    return int(status_line[1]), headers, int(length)


def binary_plist(headers, data):
    content_type = headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type != 'application/x-apple-binary-plist' or not data.startswith(b'bplist00'):
        raise ValueError('expected a binary plist Content-Type and payload')
    info = plistlib.loads(data)
    if not isinstance(info, dict):
        raise ValueError('expected a plist dictionary')
    return info


def validate_ptp_peer(setup, peer_ip, required):
    peer = setup.get('timingPeerInfo')
    if peer is None:
        if required:
            raise ValueError('PTP initial SETUP omitted required timingPeerInfo')
        print('NOT VERIFIED: PTP timingPeerInfo is missing from the legacy response', flush=True)
        return False
    if not isinstance(peer, dict):
        raise ValueError('PTP timingPeerInfo is not a dictionary')
    identifier, addresses = peer.get('ID'), peer.get('Addresses')
    if not isinstance(identifier, str) or not isinstance(addresses, list) or not addresses:
        raise ValueError('PTP timingPeerInfo requires a string ID and nonempty Addresses array')
    try:
        target = ipaddress.ip_address(peer_ip)
        id_matches = ipaddress.ip_address(identifier) == target
        address_matches = target in [ipaddress.ip_address(value) for value in addresses]
    except (ValueError, TypeError):
        raise ValueError('PTP timingPeerInfo contains an invalid IP address') from None
    if not id_matches or not address_matches:
        raise ValueError('PTP timingPeerInfo.ID/Addresses do not match the receiver peer IP')
    print('PASS synthetic PTP response: timingPeerInfo.ID/Addresses match receiver peer IP', flush=True)
    return True


class Control:
    def __init__(self, host, timeout):
        self.socket = socket.create_connection((host, 7000), timeout)
        self.socket.settimeout(timeout)
        self.encrypted = False
        self.cseq = 0
        self.tx_nonce = self.rx_nonce = 0
        self.read_key = self.write_key = None
        self.pending = bytearray()

    def exact(self, count):
        result = bytearray()
        while len(result) < count:
            data = self.socket.recv(count - len(result))
            if not data:
                raise ConnectionError('peer closed the control socket')
            result.extend(data)
        return bytes(result)

    def fill(self):
        if not self.encrypted:
            data = self.socket.recv(4096)
            if not data:
                raise ConnectionError('peer closed before RTSP response completed')
            self.pending.extend(data)
            return
        length_header = self.exact(2)
        count = int.from_bytes(length_header, 'little')
        if not 0 < count <= 1024:
            raise ValueError(f'invalid encrypted RTSP block length {count}')
        data = self.exact(count + 16)
        nonce = b'\0' * 4 + self.rx_nonce.to_bytes(8, 'little')
        self.pending.extend(aead_decrypt(self.read_key, nonce, data, length_header))
        self.rx_nonce += 1

    def request(self, method, path, body=b'', content_type=None, headers=None):
        self.cseq += 1
        fields = {'CSeq': str(self.cseq), 'User-Agent': 'Cedric-stdlib-diagnostic/1',
                  'Content-Length': str(len(body))}
        if content_type:
            fields['Content-Type'] = content_type
        if headers:
            fields.update(headers)
        packet = (method + ' ' + path + ' RTSP/1.0\r\n' +
                  ''.join(key + ': ' + value + '\r\n' for key, value in fields.items()) +
                  '\r\n').encode() + body
        if self.encrypted:
            for offset in range(0, len(packet), 1024):
                part = packet[offset:offset + 1024]
                header = len(part).to_bytes(2, 'little')
                nonce = b'\0' * 4 + self.tx_nonce.to_bytes(8, 'little')
                self.socket.sendall(header + aead_encrypt(self.write_key, nonce, part, header))
                self.tx_nonce += 1
        else:
            self.socket.sendall(packet)
        while b'\r\n\r\n' not in self.pending:
            if len(self.pending) > 16 * 1024:
                raise ValueError('RTSP response headers exceed 16 KiB')
            self.fill()
        end = self.pending.index(b'\r\n\r\n')
        if end > 16 * 1024:
            raise ValueError('RTSP response headers exceed 16 KiB')
        status, received_headers, length = parse_response_header(bytes(self.pending[:end]), self.cseq)
        total = end + 4 + length
        while len(self.pending) < total:
            self.fill()
        response = bytes(self.pending[end + 4:total])
        del self.pending[:total]
        if self.pending:
            raise ValueError('unexpected trailing bytes after a non-pipelined RTSP response')
        stage = {'method': method, 'path': path, 'status': status,
                 'bytes': length, 'encrypted': self.encrypted,
                 'content_type': received_headers.get('content-type', '')}
        print(json.dumps(stage), flush=True)
        if status != 200:
            raise ValueError('non-success RTSP status: ' + str(status))
        return received_headers, response


def report_info(headers, data, allow_invalid=False):
    try:
        info = binary_plist(headers, data)
        if not isinstance(info.get('model'), str) or not info['model']:
            raise ValueError('/info model is missing or invalid')
        if type(info.get('features')) is not int or not 0 <= info['features'] < (1 << 64):
            raise ValueError('/info features are missing or invalid')
        if type(info.get('vv')) is not int or info['vv'] != 2:
            raise ValueError('/info does not advertise AirPlay protocol version 2')
        if not isinstance(info.get('pk'), bytes) or len(info['pk']) != 32:
            raise ValueError('/info requires a 32-byte public key')
    except Exception as error:
        if not allow_invalid:
            raise
        print(json.dumps({'WARNING': '/info failed standard plist parsing; continuing transport-only diagnostic',
                          'exception': type(error).__name__, 'bytes': len(data)}), flush=True)
        return {}
    print(json.dumps({'info_model': info.get('model'),
                      'features': hex(info.get('features', 0)),
                      'vv': info.get('vv'),
                      'audioFormats': info.get('audioFormats'),
                      'audioLatencies': info.get('audioLatencies')}), flush=True)
    return info


def network_run(args):
    N, g, Nraw = srp_parameters(args.repo)
    control = Control(args.host, args.timeout)
    event = None
    try:
        headers, data = control.request('GET', '/info')
        report_info(headers, data, args.allow_invalid_info)
        if args.stop_after == 'plain-info':
            return
        m1 = tlv_encode((6, b'\x01'), (0, b'\x00'), (19, b'\x10'))
        _, data = control.request('POST', '/pair-setup', m1, 'application/octet-stream')
        m2 = tlv_decode(data)
        if m2.get(6) != b'\x02' or 7 in m2:
            raise ValueError('pair-setup M2 returned TLV error/state failure')
        A, proof, expected, K = srp_client(N, g, Nraw, m2[2], m2[3])
        m3 = tlv_encode((6, b'\x03'), (3, A), (4, proof))
        _, data = control.request('POST', '/pair-setup', m3, 'application/octet-stream')
        m4 = tlv_decode(data)
        if m4.get(6) != b'\x04' or 7 in m4 or not hmac.compare_digest(m4.get(4, b''), expected):
            raise ValueError('pair-setup M4 server proof/state verification failed')
        if control.pending:
            raise ValueError('unexpected trailing plaintext bytes at pairing transition')
        control.read_key = hkdf(K, b'Control-Salt', b'Control-Read-Encryption-Key')
        control.write_key = hkdf(K, b'Control-Salt', b'Control-Write-Encryption-Key')
        control.encrypted = True
        print('PASS synthetic transient SRP server proof; M4 received in plaintext', flush=True)
        headers, data = control.request('GET', '/info')
        report_info(headers, data, args.allow_invalid_info)
        if args.stop_after == 'encrypted-info':
            return
        client_ip = control.socket.getsockname()[0]
        initial = {'name': 'Synthetic diagnostic sender', 'deviceID': '00:11:22:33:44:56',
                   'sessionUUID': '00112233-4455-6677-8899-aabbccddeeff',
                   'timingProtocol': args.timing_protocol,
                   'isRemoteControlOnly': args.timing_protocol == 'None',
                   'timingPeerInfo': {'ID': '00:11:22:33:44:56',
                                      'Addresses': [client_ip]},
                   'sourceVersion': '377.40.00'}
        headers, data = control.request('SETUP', '/stream', plistlib.dumps(initial, fmt=plistlib.FMT_BINARY),
                                  'application/x-apple-binary-plist')
        setup = binary_plist(headers, data)
        event_port = setup.get('eventPort')
        if type(event_port) is not int or not 0 < event_port <= 65535:
            raise ValueError('initial SETUP omitted a valid eventPort')
        print(json.dumps({'initial_SETUP': setup}), flush=True)
        ptp_verified = False
        if args.timing_protocol == 'PTP':
            if type(setup.get('timingPort')) is not int or setup['timingPort'] != 0:
                raise ValueError('PTP initial SETUP requires timingPort=0')
            ptp_verified = validate_ptp_peer(setup, control.socket.getpeername()[0], args.expect_ptp_peer)
        if args.timing_protocol == 'NTP':
            port = setup.get('timingPort')
            if type(port) is not int or not 0 < port <= 65535:
                raise ValueError('NTP was requested without a valid initial SETUP timingPort')
        if args.stop_after != 'initial-setup':
            event = socket.create_connection((args.host, event_port), args.timeout)
            event.settimeout(args.timeout)
            print('PASS synthetic event TCP connected; no event messages are exercised', flush=True)
            headers, data = control.request('GET', '/info')
            report_info(headers, data, args.allow_invalid_info)
            deadline = time.monotonic() + args.hold
            while time.monotonic() < deadline:
                control.request('OPTIONS', '*')
                time.sleep(min(.5, max(0, deadline - time.monotonic())))
        control.request('TEARDOWN', '/stream')
        print('PASS synthetic client control flow through transient pairing, encrypted responses and initial SETUP', flush=True)
        if args.timing_protocol == 'PTP' and not ptp_verified:
            print('NOT VERIFIED: complete PTP response metadata; missing timingPeerInfo is not a compatibility pass', flush=True)
        print('NOT TESTED: Mac/Spotify playback, event-message exchange, stream SETUP/audio decode and multi-device synchronization', flush=True)
    finally:
        if event:
            event.close()
        control.socket.close()


def offline_self_test(repo):
    key = bytes(range(0x80, 0xa0))
    nonce = bytes.fromhex('070000004041424344454647')
    aad = bytes.fromhex('50515253c0c1c2c3c4c5c6c7')
    plaintext = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip "
                 b"for the future, sunscreen would be it.")
    expected = bytes.fromhex(
        'd31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6'
        '3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36'
        '92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc3f'
        'f4def08e4b7a9de576d26586cec64b6116'
        '1ae10b594f09e26a7e902ecbd0600691')
    encrypted = aead_encrypt(key, nonce, plaintext, aad)
    assert encrypted == expected
    assert aead_decrypt(key, nonce, expected, aad) == plaintext
    try:
        aead_decrypt(key, nonce, expected[:-1] + bytes([expected[-1] ^ 1]), aad)
        raise AssertionError('modified tag was accepted')
    except ValueError:
        pass
    N, g, Nraw = srp_parameters(repo)
    salt = secrets.token_bytes(16)
    b = int.from_bytes(secrets.token_bytes(32), 'big')
    pad = lambda value: value.to_bytes(len(Nraw), 'big')
    k = int.from_bytes(digest(Nraw, pad(g)), 'big')
    x = int.from_bytes(digest(salt, digest(b'Pair-Setup:3939')), 'big')
    v = pow(g, x, N)
    B = (k * v + pow(g, b, N)) % N
    Araw, proof, expected_proof, K = srp_client(N, g, Nraw, salt, pad(B))
    A = int.from_bytes(Araw, 'big')
    u = int.from_bytes(digest(pad(A), pad(B)), 'big')
    server_K = digest(minimal(pow((A * pow(v, u, N)) % N, b, N)))
    assert hmac.compare_digest(K, server_K)
    assert hmac.compare_digest(expected_proof, digest(minimal(A), proof, server_K))
    joined = tlv_encode((3, pad(A)), (4, proof))
    assert tlv_decode(joined) == {3: pad(A), 4: proof}
    good_header = b'RTSP/1.0 200 OK\r\nCSeq: 7\r\nContent-Length: 4'
    assert parse_response_header(good_header, 7)[2] == 4
    invalid_headers = (
        good_header.replace(b'RTSP/1.0', b'HTTP/1.1'),
        good_header.replace(b'RTSP/1.0', b'RTSP/2.0'),
        good_header.replace(b'CSeq: 7', b'CSeq: 8'),
        good_header.replace(b'CSeq: 7', b'CSeq: x'),
        good_header.replace(b'Content-Length: 4', b'Content-Length: -1'),
        good_header.replace(b'Content-Length: 4', b'Content-Length: 1048577'),
        good_header + b'\r\nContent-Length: 4',
    )
    for header in invalid_headers:
        try:
            parse_response_header(header, 7)
            raise AssertionError('invalid response header was accepted')
        except ValueError:
            pass
    peer = {'timingPeerInfo': {'ID': '192.0.2.1', 'Addresses': ['192.0.2.1', '2001:db8::1']}}
    assert validate_ptp_peer(peer, '192.0.2.1', True)
    bad_peers = ({}, {'timingPeerInfo': {'ID': '192.0.2.2', 'Addresses': ['192.0.2.1']}},
                 {'timingPeerInfo': {'ID': '192.0.2.1', 'Addresses': ['192.0.2.2']}})
    for peer in bad_peers:
        try:
            validate_ptp_peer(peer, '192.0.2.1', True)
            raise AssertionError('invalid/missing peer metadata was accepted')
        except ValueError:
            pass
    print('PASS offline RFC8439 AEAD, tag rejection, SRP proof, TLV fragmentation, strict RTSP headers/CSeq and PTP peer metadata')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host', nargs='?', help='explicit receiver IP; no automatic discovery')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--hold', type=float, default=2)
    parser.add_argument('--timing-protocol', choices=['PTP', 'NTP', 'None'], default='PTP')
    parser.add_argument('--expect-ptp-peer', action='store_true',
                        help='require timingPeerInfo.ID/Addresses to match receiver peer IP')
    parser.add_argument('--allow-invalid-info', action='store_true',
                        help='warn instead of aborting on an invalid /info plist; useful for Unicode names on old firmware')
    parser.add_argument('--stop-after', choices=['plain-info', 'encrypted-info', 'initial-setup', 'event-control'], default='event-control')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.expect_ptp_peer and args.timing_protocol != 'PTP':
        parser.error('--expect-ptp-peer requires --timing-protocol PTP')
    if args.timeout <= 0 or args.hold < 0:
        parser.error('timeout must be positive and hold cannot be negative')
    if args.self_test:
        offline_self_test(args.repo)
        return
    if not args.host:
        parser.error('host is required for network mode; --self-test is offline')
    try:
        network_run(args)
    except Exception as error:
        print('FAIL ' + type(error).__name__ + ': ' + str(error), flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
