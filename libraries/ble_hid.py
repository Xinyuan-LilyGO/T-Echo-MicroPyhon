"""BLE HID keyboard central with firmware-independent startup.

The full path uses MicroPython's ``bluetooth`` API or the bundled SoftDevice
adapter.  The stock T-Echo firmware also exposes the older ``ubluepy`` API;
it can scan for HID advertisements without replacing the firmware.  That API
does not expose SMP pairing or GATTC notifications, so the compatibility path
reports the limitation and keeps the example running instead of asking for a
UF2 update.
"""

import time
import struct

HID_LIBRARY_API = 3


def has_hid_service(payload):
    offset = 0
    while offset < len(payload):
        size = payload[offset]
        if not size or offset + size >= len(payload):
            break
        kind = payload[offset + 1]
        if kind in (2, 3):
            for start in range(offset + 2, offset + size, 2):
                if payload[start:start + 2] == b"\x12\x18":
                    return True
        offset += size + 1
    return False


class KeyboardDecoder:
    def __init__(self):
        self.held = set()
        self.caps = False

    def feed(self, report):
        if len(report) != 8:
            return ""
        keys = set(report[2:]) - {0}
        if keys.intersection({1, 2, 3}):
            return ""  # ErrorRollOver leaves the previous held keys intact.
        shift = bool(report[0] & 0x22)
        chars = []
        for key in report[2:]:
            if not key or key in self.held:
                continue
            if key == 57:
                self.caps = not self.caps
            elif 4 <= key <= 29:
                base = ord("A") if shift != self.caps else ord("a")
                chars.append(chr(base + key - 4))
            elif 30 <= key <= 39:
                chars.append(("!@#$%^&*()" if shift else "1234567890")[key - 30])
            elif key in (40, 42, 43, 44):
                chars.append({40: "\n", 42: "\b", 43: "\t", 44: " "}[key])
            elif 45 <= key <= 56:
                plain = "-=[]\\#;'`,./"
                upper = '_+{}|~:"~<>?'
                chars.append((upper if shift else plain)[key - 45])
        self.held = keys
        return "".join(chars)


class _LegacyUBluepyCentral:
    """Best-effort BLE discovery for the stock ubluepy firmware.

    The nRF ubluepy central can scan and synchronously discover services, but
    its Python API has no pairing, descriptor discovery, or notification
    callback.  It therefore cannot implement a HID keyboard stream.  Keeping
    this small backend lets the example run without replacing the board's
    firmware and gives a useful on-device status message when a keyboard is
    visible.  A ``bluetooth``/``_ble_hid`` backend is selected automatically
    whenever one is available.
    """

    def __init__(self, on_keyboard, on_status, on_passkey=None,
                 protocol_mode=0):
        try:
            from ubluepy import Scanner
        except ImportError:
            raise RuntimeError("This firmware exposes no ubluepy Scanner; BLE discovery is unavailable")
        self.Scanner = Scanner
        self.on_keyboard = on_keyboard
        self.on_status = on_status
        self.on_passkey = on_passkey
        self.protocol_mode = protocol_mode
        self.pending_confirmation = None
        self._scanning = False
        self._scanner = None
        self._next_scan = None
        self._reported = False

    @staticmethod
    def _ticks_now():
        return time.ticks_ms() if hasattr(time, 'ticks_ms') else 0

    @staticmethod
    def _has_hid_service(entry):
        """Return true when a ubluepy ScanEntry advertises UUID 0x1812."""
        try:
            records = entry.getScanData()
        except (AttributeError, OSError):
            return False
        for record in records:
            if len(record) < 3 or record[0] not in (2, 3):
                continue
            value = bytes(record[2])
            for offset in range(0, len(value) - 1, 2):
                if value[offset:offset + 2] == b"\x12\x18":
                    return True
        return False

    def start(self):
        self._scanning = True
        self._scanner = self.Scanner()
        self._next_scan = None
        self._reported = False
        self.on_status("BLE scan only: ubluepy cannot pair or receive keyboard input")

    def confirm(self, accept):
        # Legacy ubluepy has no SMP passkey operation.  There is deliberately
        # no fake confirmation: this backend never claims a secure connection.
        self.pending_confirmation = None

    def poll(self):
        if not self._scanning:
            return
        now = self._ticks_now()
        if self._next_scan is not None and hasattr(time, 'ticks_diff'):
            if time.ticks_diff(now, self._next_scan) < 0:
                return
        try:
            entries = self._scanner.scan(250)
        except OSError as exc:
            self.on_status("BLE scan failed: %s" % exc)
            entries = ()
        self._next_scan = (time.ticks_add(now, 500)
                           if hasattr(time, 'ticks_add') else None)
        for entry in entries:
            if not self._has_hid_service(entry):
                continue
            if not self._reported:
                self._reported = True
                self.on_status("HID keyboard found; stock ubluepy cannot pair or receive notifications")
            break

    def close(self):
        self._scanning = False
        self._scanner = None


class HIDCentral:
    def __init__(self, on_keyboard, on_status=print, on_passkey=None,
                 secrets_path="/flash/ble_hid_bonds.json", protocol_mode=0):
        if protocol_mode not in (0, 1):
            raise ValueError('HID protocol mode must be 0 or 1')
        self._legacy = None

        def use_legacy():
            try:
                legacy = _LegacyUBluepyCentral(
                    on_keyboard, on_status, on_passkey, protocol_mode)
            except RuntimeError:
                return False
            self._legacy = legacy
            self.bt = None
            self.ble = None
            self.pending_confirmation = None
            return True

        try:
            import bluetooth
        except ImportError:
            try:
                import ble_hid_nrf as bluetooth
                if getattr(bluetooth, 'HID_LIBRARY_API', 0) != 2:
                    raise RuntimeError('Native HID Python adapter API mismatch')
            except (ImportError, RuntimeError):
                # The board's stock firmware exposes ubluepy.  It cannot
                # provide secure HID notifications, but selecting this path
                # lets the Python example run without replacing that firmware.
                if use_legacy():
                    return
                raise RuntimeError("No usable BLE central API found (bluetooth, _ble_hid or ubluepy Scanner)")
        self.bt = bluetooth
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        required = ("gap_pair", "gap_passkey", "gattc_discover_services",
                    "gattc_discover_characteristics", "gattc_discover_descriptors")
        if any(not hasattr(self.ble, name) for name in required):
            self.ble.active(False)
            if use_legacy():
                return
            raise RuntimeError("Firmware is missing BLE central or SMP pairing APIs")
        self.ble.config(gap_name="T-Echo HID Central", bond=True, mitm=True, io=1)
        self.on_keyboard = on_keyboard
        self.on_status = on_status
        self.on_passkey = on_passkey
        self.secrets_path = secrets_path
        self.protocol_mode = protocol_mode
        self.secrets = []
        self.secrets_dirty = False
        self._load_secrets()
        self.queue = []
        self.error = None
        self.conn = None
        self.target = None
        self.service = None
        self.characteristics = []
        self.keyboard = None
        self.cccd = None
        self.protocol = None
        self.mouse = self.info = None
        self.mouse_cccd = None
        self._descriptor_target = None
        self._ready = False
        self._deadline = None
        self.pending_confirmation = None
        self._scan_candidate_queued = False
        self.ble.irq(self._irq)
        self.on_status('BLE backend: ' + getattr(bluetooth, '__name__', 'bluetooth'))

    def _load_secrets(self):
        import json
        import binascii
        try:
            with open(self.secrets_path) as stream:
                values = json.load(stream)
        except OSError:
            return
        except ValueError:
            self.on_status('Invalid bond file; pair keyboard again')
            return
        for kind, key, value in values:
            self.secrets.append((kind, binascii.unhexlify(key), binascii.unhexlify(value)))

    def _save_secrets(self):
        import json
        import binascii
        import os
        values = [(kind, binascii.hexlify(key).decode(), binascii.hexlify(value).decode())
                  for kind, key, value in self.secrets]
        with open(self.secrets_path + ".tmp", "w") as stream:
            json.dump(values, stream)
        os.rename(self.secrets_path + ".tmp", self.secrets_path)
        self.secrets_dirty = False

    def _irq(self, event, data):
        if event == 5:
            if self.target is not None or self._scan_candidate_queued or not has_hid_service(data[4]):
                return
            self._scan_candidate_queued = True
        if event == 29:  # GET_SECRET must return synchronously.
            kind, index, key = data
            candidates = [(k, v) for t, k, v in self.secrets if t == kind]
            if key is None:
                return candidates[index][1] if index < len(candidates) else None
            for k, value in candidates:
                if k == key:
                    return value
            return None
        if event == 30:  # SET_SECRET
            kind, key, value = data
            key = bytes(key)
            self.secrets = [(t, k, v) for t, k, v in self.secrets if (t, k) != (kind, key)]
            if value:
                self.secrets.append((kind, key, bytes(value)))
                if kind == 100:
                    old = [entry for entry in self.secrets if entry[0] == kind]
                    if len(old) > 8:
                        self.secrets.remove(old[0])
            self.secrets_dirty = True
            return True
        if len(self.queue) >= 64:
            self.error = "BLE event queue overflow"
            return
        # IRQ data memoryviews are only valid during this callback.
        copied = tuple(bytes(item) if isinstance(item, memoryview) else item for item in data)
        if event in (9, 11, 13):
            # UUIDs can also reference the BLE driver's reusable IRQ storage.
            copied = copied[:-1] + (self.bt.UUID(data[-1]),)
        self.queue.append((event, copied))

    def start(self):
        if self._legacy is not None:
            self._legacy.start()
            return
        self.on_status("Scan BLE keyboard")
        self.target = None
        self._scan_candidate_queued = False
        self._deadline = None
        self._ready = False
        self.ble.gap_scan(0, 100000, 50000, True)

    def confirm(self, accept):
        if self._legacy is not None:
            self._legacy.confirm(accept)
            return
        if self.pending_confirmation is not None:
            self.ble.gap_passkey(self.conn, 4, int(bool(accept)))
            self.pending_confirmation = None

    def poll(self):
        if self._legacy is not None:
            self._legacy.poll()
            return
        if hasattr(self.ble, 'poll'):
            self.ble.poll()
        if self.error:
            raise OSError(self.error)
        if self.secrets_dirty:
            self._save_secrets()
        if self.pending_confirmation is not None and time.ticks_diff(
                time.ticks_ms(), self.pending_confirmation) > 30000:
            self.confirm(False)
        while self.queue:
            event, data = self.queue.pop(0)
            self._event(event, data)
        if self._deadline is not None and time.ticks_diff(time.ticks_ms(), self._deadline) >= 0:
            self._fail('Keyboard setup timed out')

    def _fail(self, message):
        self.on_status(message)
        self._deadline = None
        self._ready = False
        if self.conn is not None:
            self.ble.gap_disconnect(self.conn)
        else:
            self.start()

    def _discover_descriptors(self, handle):
        end = self.service[1]
        for boundary, value, props, uuid in self.characteristics:
            if value == handle and boundary >= value:
                end = boundary
                break
            if boundary < value and boundary > handle:
                end = min(end, boundary - 1)
        if end <= handle:
            self._fail('HID notification descriptor missing')
            return
        self._descriptor_target = handle
        self.ble.gattc_discover_descriptors(self.conn, handle + 1, end)

    def _subscribe(self):
        self.ble.gattc_write(self.conn, self.protocol, bytes((self.protocol_mode,)), 0)
        self.ble.gattc_write(self.conn, self.cccd, b'\x01\x00', 1)

    def _connected(self):
        self._deadline = None
        self._ready = True
        self.on_status('Keyboard connected')

    def _event(self, event, data):
        if event not in (5, 6, 7, 8) and data and data[0] != self.conn:
            return
        if event == 5:
            addr_type, addr, adv_type, rssi, payload = data
            if self.target is None and has_hid_service(payload):
                self.target = (addr_type, bytes(addr))
                self.ble.gap_scan(None)
        elif event == 6 and self.target:
            self.ble.gap_connect(*self.target)
        elif event == 7:
            self.conn = data[0]
            self.on_status("Pairing keyboard")
            self._deadline = time.ticks_add(time.ticks_ms(), 90000)
            self.ble.gap_pair(self.conn)
        elif event == 8:
            self.conn = None
            self.keyboard = self.protocol = self.cccd = self.service = None
            self.mouse = self.mouse_cccd = self.info = None
            self._descriptor_target = None
            self.pending_confirmation = None
            self.characteristics = []
            self.start()
        elif event == 31:
            conn, action, passkey = data
            if action == 3 and getattr(self.ble, 'passkey_generated', False) is not True:
                import random
                passkey = random.getrandbits(20) % 1000000
                self.ble.gap_passkey(conn, action, passkey)
            if action == 4:
                self.pending_confirmation = time.ticks_ms()
            if self.on_passkey:
                self.on_passkey(action, passkey)
            else:
                self.on_status("Passkey %06d" % passkey)
            if action == 2:
                raise RuntimeError("Keyboard requests passkey input; use a keyboard supporting display/confirmation pairing")
        elif event == 28:
            conn, encrypted, authenticated, bonded, key_size = data
            if not encrypted:
                self._fail('Pairing failed')
            elif self.service is None:
                self._deadline = time.ticks_add(time.ticks_ms(), 15000)
                self.on_status('Secured; discovering HID')
                self.ble.gattc_discover_services(conn, self.bt.UUID(0x1812))
        elif event == 9:
            conn, start, end, uuid = data
            if uuid == self.bt.UUID(0x1812):
                self.service = (start, end)
        elif event == 10:
            if data[1] or self.service is None:
                self._fail('HID service discovery failed')
                return
            self.ble.gattc_discover_characteristics(self.conn, *self.service)
        elif event == 11:
            conn, boundary, value, props, uuid = data
            self.characteristics.append((boundary, value, props, uuid))
            if uuid == self.bt.UUID(0x2A22):
                self.keyboard = value
            elif uuid == self.bt.UUID(0x2A4E):
                self.protocol = value
            elif uuid == self.bt.UUID(0x2A33):
                self.mouse = value
            elif uuid == self.bt.UUID(0x2A4A):
                self.info = value
        elif event == 12:
            if data[1] or self.keyboard is None or self.protocol is None:
                self._fail('Keyboard needs HID boot input 0x2A22 and protocol mode 0x2A4E')
                return
            if self.info is not None and hasattr(self.ble, 'gattc_read'):
                self.ble.gattc_read(self.conn, self.info)
            else:
                self._discover_descriptors(self.keyboard)
        elif event == 15 and data[1] == self.info:
            if len(data[2]) >= 4:
                value = data[2]
                self.on_status('HID version: %02x.%02x Country: %d Flags: 0x%02x' %
                               (value[1], value[0], value[2], value[3]))
        elif event == 16 and data[1] == self.info:
            self._discover_descriptors(self.keyboard)
        elif event == 13:
            conn, handle, uuid = data
            if uuid == self.bt.UUID(0x2902):
                if self._descriptor_target == self.mouse:
                    self.mouse_cccd = handle
                else:
                    self.cccd = handle
        elif event == 14:
            if self._descriptor_target == self.mouse and self.cccd is not None:
                if data[1]:
                    self.mouse_cccd = None
                    self.on_status('Mouse notifications unavailable')
                self._subscribe()
            elif data[1] or self.cccd is None:
                self._fail('Keyboard notification descriptor not found')
                return
            elif self.mouse is not None and self._descriptor_target == self.keyboard:
                self._discover_descriptors(self.mouse)
            else:
                self._subscribe()
        elif event == 17 and data[1] == self.cccd:
            if data[2]:
                self._fail('Could not subscribe to keyboard notifications')
            elif self.mouse_cccd:
                self.ble.gattc_write(self.conn, self.mouse_cccd, b'\x01\x00', 1)
            else:
                self._connected()
        elif event == 17 and data[1] == self.mouse_cccd:
            if data[2]:
                self.on_status('Mouse notifications unavailable')
            self._connected()
        elif event == 18 and data[1] == self.keyboard and self._ready:
            self.on_keyboard(data[2])

    def close(self):
        if self._legacy is not None:
            self._legacy.close()
            return
        self.ble.gap_scan(None)
        if self.conn is not None:
            self.ble.gap_disconnect(self.conn)
        self.ble.active(False)
