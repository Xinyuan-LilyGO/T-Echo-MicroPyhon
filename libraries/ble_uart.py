"""Nordic UART Service adapter for the nRF port's optional ubluepy module."""


class BleUnavailable(RuntimeError):
    pass


class _UBluepyUart:
    NOTIFY_PAYLOAD_SIZE = 20

    def __init__(self, name="T-Echo", on_receive=None,
                 on_connect=None, on_disconnect=None):
        try:
            from ubluepy import Service, Characteristic, UUID, Peripheral, constants
        except ImportError:
            raise BleUnavailable(
                "This firmware has no ubluepy/bluetooth module. Pure Python cannot "
                "provide a BLE controller; rebuild the nRF port with SoftDevice s140."
            )
        self.constants = constants
        self.on_receive = on_receive
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.connected = False
        self.notifications_enabled = False
        self.connection_handle = None
        service = Service(UUID("6e400001-b5a3-f393-e0a9-e50e24dcca9e"))
        self.rx = Characteristic(
            UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e"),
            props=Characteristic.PROP_WRITE | Characteristic.PROP_WRITE_WO_RESP)
        self.tx = Characteristic(
            UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e"),
            props=Characteristic.PROP_NOTIFY, attrs=Characteristic.ATTR_CCCD)
        service.addCharacteristic(self.rx)
        service.addCharacteristic(self.tx)
        self.peripheral = Peripheral()
        self.peripheral.addService(service)
        self.peripheral.setConnectionHandler(self._event)
        if not hasattr(self.rx, "getHandle"):
            raise BleUnavailable("This ubluepy build lacks Characteristic.getHandle; use the supplied Lite-compatible extension or bluetooth firmware")
        self.rx_handle = self.rx.getHandle()
        # The CCCD is the descriptor immediately following the TX value.
        self.tx_cccd_handle = self.tx.getHandle() + 1
        self.name = name
        self.service = service
        battery_service = Service(UUID(0x180F))
        # Legacy ubluepy routes every NOTIFY characteristic write to HVX,
        # even before a connection exists, and cannot set its readable value.
        # Read-only BAS keeps battery reads valid on that firmware API.
        self.battery = Characteristic(UUID(0x2A19), props=Characteristic.PROP_READ)
        battery_service.addCharacteristic(self.battery)
        self.peripheral.addService(battery_service)
        self.battery_service = battery_service
        self._closing = False
        self._battery_level = 100
        self._started = False

    def _event(self, event, handle, data):
        if event == self.constants.EVT_GAP_CONNECTED:
            self.connected = True
            self.notifications_enabled = False
            self.connection_handle = handle
            if self.on_connect:
                self.on_connect(handle)
        elif event == self.constants.EVT_GAP_DISCONNECTED:
            self.connected = False
            self.notifications_enabled = False
            self.connection_handle = None
            reason = data[0] if data else 0
            if self.on_disconnect:
                self.on_disconnect(handle, reason)
            if not self._closing:
                self.start()
        elif event == self.constants.EVT_GATTS_WRITE:
            if handle == self.tx_cccd_handle:
                self.notifications_enabled = bool(data and (data[0] & 0x01))
            elif self.on_receive and handle == self.rx_handle:
                self.on_receive(bytes(data))

    def start(self):
        # This ubluepy port only supports one 31-byte legacy advertising
        # packet and has no scan-response API. The original 20-byte name,
        # flags and 128-bit NUS UUID need 43 bytes together. Keep the original
        # visible name and omit only the UUID from the advertising packet; the
        # NUS service is still registered and discoverable after connecting.
        if len(self.name.encode("utf-8")) > 8:
            self.peripheral.advertise(device_name=self.name)
        else:
            self.peripheral.advertise(
                device_name=self.name, services=[self.service])
        self._started = True
        self.set_battery_level(self._battery_level)

    def stop(self):
        """Stop advertising, matching Bluefruit.Advertising.stop()."""
        self.peripheral.advertise_stop()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        if not self.connected or not self.notifications_enabled:
            return False

        # ubluepy does not fragment notifications. With the default ATT MTU
        # of 23 bytes, each notification can carry at most 20 data bytes.
        # Splitting here matches the stream behavior of Arduino BLEUart.print().
        for offset in range(0, len(data), self.NOTIFY_PAYLOAD_SIZE):
            if not self.connected or not self.notifications_enabled:
                return False
            chunk = data[offset:offset + self.NOTIFY_PAYLOAD_SIZE]
            self.tx.write(bytearray(chunk))
        return True

    def set_battery_level(self, percent):
        self._battery_level = max(0, min(100, int(percent)))
        if self._started:
            self.battery.write(bytearray((self._battery_level,)))

    def close(self):
        self._closing = True
        self.stop()
        if self.connected:
            self.peripheral.disconnect()


class _BluetoothUart:
    def __init__(self, name="T-Echo", on_receive=None, on_connect=None,
                 on_disconnect=None):
        import bluetooth
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.on_receive = on_receive
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.connected = False
        self.connection_handle = None
        self._closing = False
        self.name = name
        uuid = bluetooth.UUID
        nus = (uuid("6e400001-b5a3-f393-e0a9-e50e24dcca9e"), (
            (uuid("6e400002-b5a3-f393-e0a9-e50e24dcca9e"),
             bluetooth.FLAG_WRITE | bluetooth.FLAG_WRITE_NO_RESPONSE),
            (uuid("6e400003-b5a3-f393-e0a9-e50e24dcca9e"), bluetooth.FLAG_NOTIFY)))
        bas = (uuid(0x180F), ((uuid(0x2A19), bluetooth.FLAG_READ | bluetooth.FLAG_NOTIFY),))
        ((self.rx, self.tx), (self.battery,)) = self.ble.gatts_register_services((nus, bas))
        self.ble.gatts_set_buffer(self.rx, 256, True)
        self.ble.irq(self._event)
        self.set_battery_level(100)

    def _event(self, event, data):
        if event == 1:
            self.connection_handle = data[0]
            self.connected = True
            if self.on_connect:
                self.on_connect(data[0])
        elif event == 2:
            self.connection_handle = None
            self.connected = False
            if self.on_disconnect:
                self.on_disconnect(data[0], 0)
            if not self._closing:
                self.start()
        elif event == 3 and data[1] == self.rx:
            message = self.ble.gatts_read(self.rx)
            if self.on_receive:
                self.on_receive(message)

    def start(self):
        import bluetooth
        name = self.name.encode()[:29]
        service = bytes(bluetooth.UUID("6e400001-b5a3-f393-e0a9-e50e24dcca9e"))
        advert = b"\x02\x01\x06\x11\x07" + service
        scan = bytes((len(name) + 1, 9)) + name
        self.ble.gap_advertise(100000, adv_data=advert, resp_data=scan)

    def stop(self):
        self.ble.gap_advertise(None)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        if not self.connected:
            return False
        for offset in range(0, len(data), 20):
            try:
                self.ble.gatts_notify(self.connection_handle, self.tx, data[offset:offset + 20])
            except OSError:
                return False
        return True

    def set_battery_level(self, percent):
        value = bytes((max(0, min(100, int(percent))),))
        self.ble.gatts_write(self.battery, value)
        if self.connected:
            try:
                self.ble.gatts_notify(self.connection_handle, self.battery, value)
            except OSError:
                pass  # A client may connect without subscribing to BAS.

    def close(self):
        self._closing = True
        self.stop()
        if self.connected:
            self.ble.gap_disconnect(self.connection_handle)
        self.ble.active(False)


def BleUart(*args, **kwargs):
    try:
        import bluetooth
    except ImportError:
        return _UBluepyUart(*args, **kwargs)
    return _BluetoothUart(*args, **kwargs)
