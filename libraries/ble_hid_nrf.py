"""HID-only adapter for the nRF52840 SoftDevice firmware's _ble_hid module.

This is deliberately not installed as 'bluetooth': only the central operations
used by ble_hid.py are provided. All callbacks run when poll() is called.
"""
import _ble_hid as native

HID_LIBRARY_API = 2

if getattr(native, 'API_VERSION', 0) != 1:
    raise RuntimeError('Native HID module API mismatch; upload matching ble_hid_nrf.py or use the stock ubluepy backend')

UUID = int
BOND_TYPE = 100


class BLE:
    passkey_generated = True

    def __init__(self):
        self.handler = None
        self.scanning = False
        self.enabled = False

    def active(self, value):
        if value and not self.enabled:
            native.open()
        elif not value and self.enabled:
            native.close()
        self.enabled = bool(value)

    def config(self, **settings):
        # Firmware uses bonded, authenticated SMP with DisplayYesNo IO.
        if not settings.get('bond') or not settings.get('mitm') or settings.get('io') != 1:
            raise ValueError('Native HID requires bond=True, mitm=True, io=1')

    def irq(self, handler):
        self.handler = handler

    def gap_scan(self, duration, *args):
        if duration is None:
            native.scan(False)
            if self.scanning:
                self.scanning = False
                self.handler(6, ())
            return
        bonds = []
        for index in range(8):
            bond = self.handler(29, (BOND_TYPE, index, None))
            if bond is None:
                break
            bonds.append(bond)
        native.bonds(bonds)
        native.scan(True)
        self.scanning = True

    def gap_connect(self, addr_type, addr):
        native.connect(addr_type, addr)

    def gap_disconnect(self, conn):
        native.disconnect()

    def gap_pair(self, conn):
        native.pair()

    def gap_passkey(self, conn, action, passkey):
        native.passkey(action, passkey)

    def gattc_discover_services(self, conn, uuid):
        if uuid != 0x1812:
            raise ValueError('Native central discovers HID only')
        native.discover(9, 1, 65535)

    def gattc_discover_characteristics(self, conn, start, end):
        native.discover(11, start, end)

    def gattc_discover_descriptors(self, conn, start, end):
        native.discover(13, start, end)

    def gattc_write(self, conn, handle, value, mode=0):
        native.write(handle, value, mode)

    def gattc_read(self, conn, handle):
        native.read(handle)

    def poll(self):
        for _ in range(64):
            event = native.event()
            if event is None:
                break
            kind, conn, a, b, c, payload = event
            if kind == 5:
                data = (a, payload[:6], b, c if c < 32768 else c - 65536, payload[6:])
            elif kind == 7:
                data = (conn, a, payload)
            elif kind == 8:
                data = (conn, a, b'')
            elif kind == 9:
                data = (conn, a, b, c)
            elif kind == 11:
                data = (conn, a, b, c, payload[0] | payload[1] << 8)
            elif kind == 13:
                data = (conn, a, b)
            elif kind in (10, 12, 14):
                data = (conn, a)
            elif kind in (15, 18):
                data = (conn, a, payload)
            elif kind in (16, 17):
                data = (conn, a, b)
            elif kind == 28:
                data = (conn, bool(a), bool(a and b), bool(a), c)
            elif kind == 30:
                data = (BOND_TYPE, native.bond_key(payload), payload)
            elif kind == 31:
                data = (conn, a, int(payload))
            elif kind == 32:
                raise OSError('SoftDevice event error %d' % a)
            else:
                continue
            self.handler(kind, data)
