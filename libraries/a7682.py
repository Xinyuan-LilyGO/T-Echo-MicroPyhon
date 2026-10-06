"""A7682/SIM7600 AT modem and binary-safe TCP transport for MicroPython.

Command sequence checked against TinyGSM's TinyGsmClientSIM7600.h:
https://github.com/vshymanskyy/TinyGSM/blob/master/src/TinyGsmClientSIM7600.h
The UART and GPIO numbers are supplied by t_echo_config, in MCU direction.
"""

from machine import Pin
import time


class ModemError(OSError):
    pass


def quoted(value):
    value = str(value)
    if any(c in value for c in ('"', '\r', '\n', '\x00')):
        raise ValueError("Invalid character in AT parameter")
    return '"' + value + '"'


class A7682:
    def __init__(self, uart, power_pin, dtr_pin, ri_pin, debug=False):
        self.uart = uart
        self.power = Pin(power_pin, Pin.OUT, value=0)
        self.dtr = Pin(dtr_pin, Pin.OUT, value=0)
        self.ri = Pin(ri_pin, Pin.IN, Pin.PULL_UP)
        self.debug = debug
        self._rx = bytearray()
        self.sockets = {}
        self.urcs = []
        self._data_lost = False

    def _write(self, data):
        view = memoryview(data)
        offset = 0
        started = time.ticks_ms()
        while offset < len(view):
            sent = self.uart.write(view[offset:])
            if sent:
                offset += sent
                started = time.ticks_ms()
            elif time.ticks_diff(time.ticks_ms(), started) > 5000:
                raise ModemError("UART write timeout")
            else:
                time.sleep_ms(2)

    def _fill(self):
        count = self.uart.any()
        if count:
            data = self.uart.read(min(count, 512))
            if data:
                self._rx.extend(data)

    def _line(self, timeout_ms=1000, prompt=False):
        started = time.ticks_ms()
        while True:
            self._fill()
            if prompt and self._rx.lstrip(b'\r\n ').startswith(b'>'):
                index = self._rx.index(62)
                self._rx = self._rx[index + 1:]
                # The optional space after the prompt is harmless whitespace.
                return b'>'
            index = self._rx.find(b'\n')
            if index >= 0:
                line = bytes(self._rx[:index]).strip()
                self._rx = self._rx[index + 1:]
                if line:
                    if self.debug:
                        print("<", line)
                    return line
                continue
            if len(self._rx) > 4096:
                raise ModemError("AT line exceeds 4096 bytes")
            if time.ticks_diff(time.ticks_ms(), started) >= timeout_ms:
                return None
            time.sleep_ms(2)

    def _handle_urc(self, line):
        if line.startswith(b'+IPCLOSE:'):
            fields = line.split(b':', 1)[1].split(b',')
            socket = self.sockets.get(int(fields[0]))
            if socket:
                socket.connected = False
        elif line.startswith(b'+CIPEVENT:') or line == b'NETWORK CLOSED':
            self._data_lost = True
            for socket in self.sockets.values():
                socket.connected = False
        elif line.startswith((b'+CIPRXGET: 1,', b'+RECEIVE:')):
            return
        else:
            if len(self.urcs) >= 16:
                self.urcs.pop(0)
            self.urcs.append(line)

    def poll(self):
        while True:
            line = self._line(0)
            if line is None:
                break
            if line not in (b'OK', b'ERROR'):
                self._handle_urc(line)

    def _start(self, command):
        self.poll()
        if '\r' in command or '\n' in command:
            raise ValueError("AT command must be one line")
        if self.debug:
            print(">", command)
        self._write((command + '\r\n').encode())

    def _wait(self, timeout_ms=1000, result_prefix=None, prompt=False,
              require_ok=True):
        started = time.ticks_ms()
        lines = []
        result = None
        got_ok = False
        while time.ticks_diff(time.ticks_ms(), started) < timeout_ms:
            remaining = timeout_ms - time.ticks_diff(time.ticks_ms(), started)
            line = self._line(max(0, remaining), prompt)
            if line is None:
                break
            if prompt and line == b'>':
                return lines
            if line == b'ERROR' or line.startswith((b'+CME ERROR:', b'+CMS ERROR:')):
                raise ModemError(line.decode())
            if line == b'OK':
                got_ok = True
            elif result_prefix and line.startswith(result_prefix):
                result = line
                lines.append(line)
            elif line.startswith((b'+IPCLOSE:', b'+CIPEVENT:', b'+RECEIVE:',
                                  b'+CIPRXGET: 1,')) or line == b'RING':
                self._handle_urc(line)
            elif not line.startswith(b'AT'):
                lines.append(line)
            if not prompt and ((result is not None and (got_ok or not require_ok))
                               or (result_prefix is None and got_ok)):
                return lines
        raise ModemError("AT response timeout: %s" % (result_prefix or b'OK'))

    def command(self, command, timeout_ms=3000, result_prefix=None,
                require_ok=True):
        self._start(command)
        return self._wait(timeout_ms, result_prefix, require_ok=require_ok)

    def power_on(self, force_cycle=False):
        # Reuse a running modem. A PWRKEY pulse can otherwise turn it off.
        if not force_cycle:
            try:
                self.command('AT', 700)
                return
            except ModemError:
                pass
        else:
            self.power.value(1)
            time.sleep_ms(4000)
            self.power.value(0)
            time.sleep_ms(4000)
        self.power.value(0)
        time.sleep_ms(100)
        self.power.value(1)
        time.sleep_ms(1000)
        self.power.value(0)
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < 30000:
            try:
                self.command('AT', 1000)
                return
            except ModemError:
                time.sleep_ms(500)
        raise ModemError("No modem response; check UART, supply and A7682 board")

    def init(self, sim_pin=''):
        self.power_on()
        self.command('ATE0')
        self.command('AT+CMEE=2')
        self.command('AT+CFUN=1', 10000)
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < 15000:
            response = self.command('AT+CPIN?')
            if any(b'READY' in line for line in response):
                return
            if any(b'SIM PIN' in line for line in response):
                if not sim_pin:
                    raise ModemError("SIM is locked; set GSM_PIN in this example")
                self.command('AT+CPIN=' + quoted(sim_pin), 10000)
                sim_pin = ''
            time.sleep_ms(500)
        raise ModemError("SIM not ready: %s" % response)

    def network_registered(self):
        for name in ('CEREG', 'CGREG', 'CREG'):
            try:
                lines = self.command('AT+' + name + '?')
            except ModemError:
                continue
            prefix = ('+' + name + ':').encode()
            for line in lines:
                if line.startswith(prefix):
                    fields = line.split(b':', 1)[1].strip().split(b',')
                    stat = fields[1] if len(fields) > 1 else fields[0]
                    if stat.strip() in (b'1', b'5'):
                        return True
        return False

    def wait_for_network(self, timeout_ms=180000):
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < timeout_ms:
            if self.network_registered():
                return
            time.sleep_ms(1000)
        raise ModemError("Network registration timeout; check SIM, antenna and coverage")

    def data_connected(self):
        lines = self.command('AT+NETOPEN?')
        return any(line.startswith(b'+NETOPEN:') and
                   line.split(b':', 1)[1].strip().split(b',')[0] == b'1'
                   for line in lines)

    def connect_data(self, apn, username='', password=''):
        if not apn or apn == 'YourAPN':
            raise ValueError("Set APN to the SIM carrier's APN in this example")
        self.wait_for_network()
        if self.data_connected():
            if self._data_lost:
                self.disconnect_data()
            else:
                return
        self.command('AT+CGDCONT=1,"IP",' + quoted(apn) + ',"0.0.0.0",0,0')
        if username:
            # SIMCom takes password before username; 1 selects PAP.
            self.command('AT+CGAUTH=1,1,' + quoted(password) + ',' + quoted(username))
        for command in ('AT+CIPMODE=0', 'AT+CIPSENDMODE=0',
                        'AT+CIPCCFG=10,0,0,0,1,0,75000',
                        'AT+CIPTIMEOUT=75000,15000,15000', 'AT+CIPRXGET=1'):
            self.command(command)
        result = self.command('AT+NETOPEN', 80000, b'+NETOPEN:')
        self._check_result(result, b'+NETOPEN:', (0,))
        self._data_lost = False

    @staticmethod
    def _check_result(lines, prefix, expected):
        for line in lines:
            if line.startswith(prefix):
                fields = tuple(int(part.strip()) for part in
                               line.split(b':', 1)[1].split(b','))
                if fields == expected:
                    return
                raise ModemError("Modem operation failed: %s" % line)
        raise ModemError("Missing modem result: %s" % prefix)

    def disconnect_data(self):
        if self.data_connected():
            lines = self.command('AT+NETCLOSE', 65000, b'+NETCLOSE:')
            self._check_result(lines, b'+NETCLOSE:', (0,))
        for socket in self.sockets.values():
            socket.connected = False

    def socket(self, host, port, channel=0):
        socket = ATTCPSocket(self, channel)
        socket.connect(host, port)
        return socket

    def dial(self, number):
        if not number or any(c not in '+0123456789*#' for c in number):
            raise ValueError("Set NUMBER to the destination phone number")
        self.command('ATD' + number + ';', 15000)

    def answer(self):
        self.command('ATA', 10000)

    def hangup(self):
        self.command('AT+CHUP')

    def calls(self):
        calls = []
        for line in self.command('AT+CLCC'):
            if line.startswith(b'+CLCC:'):
                fields = line.split(b':', 1)[1].strip().split(b',')
                calls.append({'id': int(fields[0]), 'direction': int(fields[1]),
                              'state': int(fields[2]),
                              'number': fields[5].strip(b'"').decode()
                              if len(fields) > 5 else ''})
        return calls


class ATTCPSocket:
    """Single modem TCP socket. recv() returns None on timeout and b'' on EOF."""
    def __init__(self, modem, channel=0):
        if not 0 <= channel < 10:
            raise ValueError("TCP channel must be 0..9")
        self.modem = modem
        self.channel = channel
        self.connected = False
        modem.sockets[channel] = self

    def connect(self, host, port):
        if not 1 <= port <= 65535:
            raise ValueError("Invalid TCP port")
        try:
            self.modem.command('AT+CIPCLOSE=%d' % self.channel, 3000)
        except ModemError:
            pass
        self.modem.command('AT+CIPRXGET=1')
        lines = self.modem.command('AT+CIPOPEN=%d,"TCP",%s,%d' %
                                   (self.channel, quoted(host), port),
                                   20000, b'+CIPOPEN:')
        self.modem._check_result(lines, b'+CIPOPEN:', (self.channel, 0))
        self.connected = True

    def sendall(self, data):
        if isinstance(data, str):
            data = data.encode()
        position = 0
        while position < len(data):
            if not self.connected:
                raise ModemError("TCP connection closed")
            part = data[position:position + 1024]
            self.modem._start('AT+CIPSEND=%d,%d' % (self.channel, len(part)))
            self.modem._wait(5000, prompt=True)
            self.modem._write(part)
            lines = self.modem._wait(20000, b'+CIPSEND:', require_ok=False)
            for line in lines:
                if line.startswith(b'+CIPSEND:'):
                    fields = [int(x.strip()) for x in line.split(b':', 1)[1].split(b',')]
                    if len(fields) != 3 or fields[0] != self.channel or fields[1] != len(part):
                        raise ModemError("Invalid TCP send result: %s" % line)
                    sent = fields[2]
                    if sent <= 0 or sent > len(part):
                        raise ModemError("TCP send failed: %s" % line)
                    position += sent
                    break

    def _available(self):
        lines = self.modem.command('AT+CIPRXGET=4,%d' % self.channel)
        for line in lines:
            if line.startswith(b'+CIPRXGET:'):
                fields = [int(x.strip()) for x in line.split(b':', 1)[1].split(b',')]
                if len(fields) == 3 and fields[:2] == [4, self.channel]:
                    return fields[2]
        raise ModemError("Missing TCP receive count")

    def _is_connected(self):
        lines = self.modem.command('AT+CIPCLOSE?')
        for line in lines:
            if line.startswith(b'+CIPCLOSE:'):
                fields = line.split(b':', 1)[1].strip().split(b',')
                if len(fields) > self.channel:
                    self.connected = fields[self.channel].strip() == b'1'
        return self.connected

    def recv(self, size=1024, timeout_ms=10000):
        if size <= 0:
            return b''
        started = time.ticks_ms()
        while True:
            available = self._available()
            if available:
                break
            if not self.connected or not self._is_connected():
                return b''
            if time.ticks_diff(time.ticks_ms(), started) >= timeout_ms:
                return None
            time.sleep_ms(20)
        size = min(size, available, 1024)
        self.modem._start('AT+CIPRXGET=2,%d,%d' % (self.channel, size))
        header = None
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < 5000:
            line = self.modem._line(1000)
            if line is None:
                continue
            if line.startswith(b'+CIPRXGET:'):
                fields = [int(x.strip()) for x in line.split(b':', 1)[1].split(b',')]
                if fields[:2] == [2, self.channel]:
                    header = fields
                    break
            if line == b'ERROR' or line.startswith(b'+CME ERROR:'):
                raise ModemError("TCP read failed: %s" % line)
            self.modem._handle_urc(line)
        if header is None or len(header) != 4 or not 0 <= header[2] <= size:
            raise ModemError("Invalid TCP receive header")
        count = header[2]
        started = time.ticks_ms()
        while len(self.modem._rx) < count:
            self.modem._fill()
            if time.ticks_diff(time.ticks_ms(), started) > 5000:
                raise ModemError("Truncated TCP payload")
            time.sleep_ms(2)
        # Consume exactly the declared binary length, including embedded CR/LF/OK.
        data = bytes(self.modem._rx[:count])
        self.modem._rx = self.modem._rx[count:]
        self.modem._wait(5000)
        return data

    def close(self):
        try:
            self.modem.command('AT+CIPCLOSE=%d' % self.channel, 5000)
        except ModemError:
            pass
        self.connected = False


class BufferedTCP:
    """Small protocol buffer shared by HTTP, MQTT and Blynk."""
    def __init__(self, socket):
        self.socket = socket
        self.buffer = bytearray()

    def read_exact(self, count, timeout_ms=15000):
        started = time.ticks_ms()
        while len(self.buffer) < count:
            remaining = timeout_ms - time.ticks_diff(time.ticks_ms(), started)
            if remaining <= 0:
                raise ModemError("Protocol receive timeout")
            data = self.socket.recv(min(1024, count - len(self.buffer)), remaining)
            if data is None:
                raise ModemError("Protocol receive timeout")
            if not data:
                raise ModemError("Connection closed during protocol packet")
            self.buffer.extend(data)
        result = bytes(self.buffer[:count])
        self.buffer = self.buffer[count:]
        return result

    def readline(self, limit=2048):
        while True:
            index = self.buffer.find(b'\n')
            if index >= 0:
                result = bytes(self.buffer[:index + 1])
                self.buffer = self.buffer[index + 1:]
                return result
            if len(self.buffer) >= limit:
                raise ModemError("Protocol line too long")
            data = self.socket.recv(min(512, limit - len(self.buffer)))
            if not data:
                raise ModemError("Connection ended before line terminator")
            self.buffer.extend(data)
