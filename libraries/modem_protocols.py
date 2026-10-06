"""HTTP/1.1, MQTT 3.1.1 and Blynk protocols over the A7682 TCP transport."""

import struct
import time
from a7682 import BufferedTCP, ModemError


def _bytes(value):
    return value.encode() if isinstance(value, str) else bytes(value)


def _mqtt_string(value):
    value = _bytes(value)
    if len(value) > 65535:
        raise ValueError("MQTT string is too long")
    return struct.pack('!H', len(value)) + value


class MQTTClient:
    """MQTT clean sessions, QoS 0 publish and QoS 0/1 receive."""
    def __init__(self, modem, client_id, server, port=1883, user=None,
                 password=None, keepalive=60, callback=None):
        self.modem = modem
        self.client_id = client_id
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.keepalive = keepalive
        self.callback = callback
        self.socket = None
        self.stream = None
        self._packet_id = 0
        self._last_io = time.ticks_ms()
        self._ping_at = None

    def _send(self, header, payload=b''):
        length = len(payload)
        packet = bytearray((header,))
        while True:
            byte = length & 0x7f
            length >>= 7
            packet.append(byte | (0x80 if length else 0))
            if not length:
                break
        packet.extend(payload)
        self.socket.sendall(packet)
        self._last_io = time.ticks_ms()

    def _read_packet(self):
        header = self.stream.read_exact(1)[0]
        length = 0
        shift = 0
        while True:
            byte = self.stream.read_exact(1)[0]
            length |= (byte & 0x7f) << shift
            if not byte & 0x80:
                break
            shift += 7
            if shift >= 28:
                raise ModemError("Malformed MQTT remaining length")
        if length > 8192:
            raise ModemError("MQTT packet exceeds 8192 byte RAM limit")
        payload = self.stream.read_exact(length)
        return header, payload

    def connect(self):
        self.close()
        self.socket = self.modem.socket(self.server, self.port)
        self.stream = BufferedTCP(self.socket)
        flags = 2
        payload = _mqtt_string(self.client_id)
        if self.user is not None:
            flags |= 0x80
            payload += _mqtt_string(self.user)
        if self.password is not None:
            if self.user is None:
                raise ValueError("MQTT password requires a username")
            flags |= 0x40
            payload += _mqtt_string(self.password)
        header = b'\x00\x04MQTT\x04' + bytes((flags,)) + struct.pack('!H', self.keepalive)
        self._send(0x10, header + payload)
        kind, result = self._read_packet()
        if kind != 0x20 or len(result) != 2 or result[1] != 0:
            raise ModemError("MQTT connection rejected: %s" % result)
        self._ping_at = None

    def publish(self, topic, payload, retain=False):
        self._send(0x30 | bool(retain), _mqtt_string(topic) + _bytes(payload))

    def subscribe(self, topic):
        self._packet_id = self._packet_id % 65535 + 1
        packet_id = struct.pack('!H', self._packet_id)
        self._send(0x82, packet_id + _mqtt_string(topic) + b'\x00')
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < 15000:
            header, payload = self._read_packet()
            if header == 0x90:
                if len(payload) != 3 or payload[:2] != packet_id or payload[2] == 0x80:
                    raise ModemError("MQTT subscription rejected: %s" % payload)
                return
            self._dispatch(header, payload)
        raise ModemError("MQTT subscribe timeout")

    def _dispatch(self, header, payload):
        kind = header >> 4
        if kind == 13:
            self._ping_at = None
        elif kind == 3:
            if len(payload) < 2:
                raise ModemError("Truncated MQTT PUBLISH")
            length = (payload[0] << 8) | payload[1]
            if 2 + length > len(payload):
                raise ModemError("Truncated MQTT topic")
            topic = payload[2:2 + length]
            position = 2 + length
            qos = (header >> 1) & 3
            if qos > 1:
                raise ModemError("Broker sent unsupported MQTT QoS")
            packet_id = None
            if qos:
                packet_id = payload[position:position + 2]
                if len(packet_id) != 2:
                    raise ModemError("Truncated MQTT packet ID")
                position += 2
            if self.callback:
                self.callback(topic, payload[position:])
            if qos:
                self._send(0x40, packet_id)
        elif kind == 14:
            raise ModemError("MQTT broker disconnected")

    def check_msg(self):
        if not self.socket:
            raise ModemError("MQTT not connected")
        if not self.stream.buffer:
            data = self.socket.recv(1024, 0)
            if data == b'':
                raise ModemError("MQTT TCP connection closed")
            if data:
                self.stream.buffer.extend(data)
        if self.stream.buffer:
            header, payload = self._read_packet()
            self._dispatch(header, payload)
        now = time.ticks_ms()
        if self._ping_at is not None:
            if time.ticks_diff(now, self._ping_at) > 15000:
                raise ModemError("MQTT keepalive timeout")
        elif self.keepalive and time.ticks_diff(now, self._last_io) >= self.keepalive * 500:
            self._send(0xc0)
            self._ping_at = now

    def close(self):
        if self.socket:
            self.socket.close()
        self.socket = None
        self.stream = None


class HTTPResponse:
    def __init__(self, socket):
        self.socket = socket
        self.stream = BufferedTCP(socket)
        # HTTP may send interim 1xx responses before the final status/headers.
        while True:
            status = self.stream.readline().strip().split(b' ', 2)
            if len(status) < 2 or not status[0].startswith(b'HTTP/'):
                raise ModemError("Invalid HTTP status line")
            self.status = int(status[1])
            self.headers = []
            header_bytes = 0
            while True:
                line = self.stream.readline()
                header_bytes += len(line)
                if header_bytes > 16384:
                    raise ModemError("HTTP headers exceed 16384 byte limit")
                if line == b'\r\n' or line == b'\n':
                    break
                name, value = line.split(b':', 1)
                self.headers.append((name.decode(), value.strip().decode()))
            if not 100 <= self.status < 200:
                break
        self.chunked = 'chunked' in (self.header('transfer-encoding') or '').lower()
        length = self.header('content-length')
        self.content_length = int(length) if length is not None else None
        if self.content_length is not None and self.content_length < 0:
            raise ModemError("Invalid HTTP content length")

    def header(self, name):
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None

    def iter_content(self, size=512):
        if self.status in (204, 304):
            return
        if self.chunked:
            while True:
                remaining = int(self.stream.readline().split(b';', 1)[0].strip(), 16)
                if remaining < 0:
                    raise ModemError("Invalid HTTP chunk length")
                if not remaining:
                    trailer_bytes = 0
                    while True:
                        line = self.stream.readline()
                        trailer_bytes += len(line)
                        if trailer_bytes > 16384:
                            raise ModemError("HTTP trailers exceed limit")
                        if line in (b'\r\n', b'\n'):
                            break
                    return
                while remaining:
                    data = self.stream.read_exact(min(remaining, size))
                    remaining -= len(data)
                    yield data
                if self.stream.read_exact(2) != b'\r\n':
                    raise ModemError("Invalid HTTP chunk terminator")
        elif self.content_length is not None:
            remaining = self.content_length
            while remaining:
                data = self.stream.read_exact(min(remaining, size))
                remaining -= len(data)
                yield data
        else:
            if self.stream.buffer:
                yield bytes(self.stream.buffer)
                self.stream.buffer = bytearray()
            while True:
                data = self.socket.recv(size, 30000)
                if data is None:
                    raise ModemError("HTTP body timeout before EOF")
                if not data:
                    break
                yield data

    def close(self):
        self.socket.close()


def http_get(modem, host, resource, port=80):
    if not resource.startswith('/') or any(c in host + resource for c in ('\r', '\n')):
        raise ValueError("Invalid HTTP host or resource")
    socket = modem.socket(host, port)
    try:
        host_header = host if port == 80 else '%s:%d' % (host, port)
        socket.sendall('GET %s HTTP/1.1\r\nHost: %s\r\n'
                       'Connection: close\r\nAccept-Encoding: identity\r\n\r\n' %
                       (resource, host_header))
        return HTTPResponse(socket)
    except Exception:
        socket.close()
        raise


class BlynkClient:
    """Blynk hardware protocol used by BlynkSimpleTinyGSM (plain TCP)."""
    def __init__(self, modem, token, server='blynk.cloud', port=80,
                 template_id='', device_name=''):
        self.modem = modem
        self.token = token
        self.server = server
        self.port = port
        self.template_id = template_id
        self.device_name = device_name
        self.socket = None
        self.stream = None
        self._id = 0
        self._ping_id = None
        self._ping_at = 0
        self._last_ping = time.ticks_ms()

    def _send(self, command, payload=b'', message_id=None):
        payload = _bytes(payload)
        if message_id is None:
            self._id = self._id % 65535 + 1
            message_id = self._id
        self.socket.sendall(struct.pack('!BHH', command, message_id, len(payload)) + payload)
        return message_id

    def _packet(self):
        command, message_id, length = struct.unpack('!BHH', self.stream.read_exact(5))
        if command == 0:
            return command, message_id, length
        if length > 8192:
            raise ModemError("Blynk packet exceeds RAM limit")
        return command, message_id, self.stream.read_exact(length)

    def connect(self):
        if not self.token:
            raise ValueError("Set BLYNK_AUTH_TOKEN in this example")
        self.close()
        for attempt in range(3):
            self.socket = self.modem.socket(self.server, self.port)
            self.stream = BufferedTCP(self.socket)
            self._id = 0
            login_id = self._send(29, self.token)
            command, message_id, result = self._packet()
            if command == 41:
                fields = result.split(b'\x00')
                if len(fields) < 2:
                    raise ModemError("Invalid Blynk redirect")
                self.server = fields[0].decode()
                self.port = int(fields[1])
                self.close()
                continue
            if command != 0 or message_id != login_id or result not in (200, 4):
                raise ModemError("Blynk login rejected: %s" % result)
            metadata = ['ver', '1.3.2', 'h-beat', '30', 'buff-in', '8192',
                        'dev', self.device_name or 'T-Echo-MicroPython', 'con', 'SIM7600']
            if self.template_id:
                metadata += ['tmpl', self.template_id, 'fw-type', self.template_id]
            self._send(17, '\x00'.join(metadata))
            self._ping_id = None
            self._last_ping = time.ticks_ms()
            return
        raise ModemError("Too many Blynk server redirects")

    def virtual_write(self, pin, value):
        self._send(20, 'vw\x00%d\x00%s' % (pin, value))

    def run(self):
        if not self.socket:
            raise ModemError("Blynk not connected")
        if not self.stream.buffer:
            data = self.socket.recv(1024, 0)
            if data == b'':
                raise ModemError("Blynk TCP connection closed")
            if data:
                self.stream.buffer.extend(data)
        if self.stream.buffer:
            command, message_id, value = self._packet()
            if command == 6:
                self.socket.sendall(struct.pack('!BHH', 0, message_id, 200))
            elif command == 0:
                if value != 200:
                    raise ModemError("Blynk server error: %d" % value)
                if message_id == self._ping_id:
                    self._ping_id = None
            elif command == 41:
                fields = value.split(b'\x00')
                if len(fields) >= 2:
                    self.server, self.port = fields[0].decode(), int(fields[1])
                raise ModemError("Blynk requested reconnection")
        now = time.ticks_ms()
        if self._ping_id is not None:
            if time.ticks_diff(now, self._ping_at) > 10000:
                raise ModemError("Blynk heartbeat timeout")
        elif time.ticks_diff(now, self._last_ping) > 15000:
            self._ping_id = self._send(6)
            self._ping_at = self._last_ping = now

    def close(self):
        if self.socket:
            self.socket.close()
        self.socket = None
        self.stream = None
