"""200x200 GDEH0154D67 SSD1681 driver, using the panel's OTP waveform.

Initialization/update sequence: bundled GxEPD2_154_D67 (Jean-Marc Zingg,
GPL-3.0). Drawing helpers derive from the working T-Echo-Lite port and
Adafruit GFX (BSD); see THIRD_PARTY_NOTICES.md.
"""

from binascii import unhexlify
import framebuf
from machine import Pin
import time


# Adafruit GFX classic 5x7 font, characters 32 through 127 inclusive.
_GFX_FONT_5X7 = unhexlify(
    b"000000000000005f00000007000700147f147f14242a7f2a12231308646236495620500008070300"
    b"001c2241000041221c002a1c7f1c2a08083e08080080703000080808080800006060002010080402"
    b"3e5149453e00427f400072494949462141494d331814127f1027454545393c4a4949314121110907"
    b"3649494936464949291e000014000000403400000008142241141414141400412214080201590906"
    b"3e415d594e7c1211127c7f494949363e414141227f4141413e7f494949417f090909013e41415173"
    b"7f0808087f00417f41002040413f017f081422417f404040407f021c027f7f0408107f3e4141413e"
    b"7f090909063e4151215e7f09192946264949493203017f01033f4040403f1f2040201f3f4038403f"
    b"631408146303047804036159494d43007f4141410204081020004141417f04020102044040404040"
    b"000307080020545478407f284444383844444428384444287f385454541800087e090218a4a49c78"
    b"7f0804047800447d40002040403d007f1028440000417f40007c047804787c080404783844444438"
    b"fc1824241818242418fc7c08040408485454542404043f44243c4040207c1c2040201c3c4030403c"
    b"44281028444c9090907c4464544c4400083641000000770000004136080002010204023c2623263c"
)

# FreeSans9pt7b digits copied from Adafruit GFX. Display.py only prints the
# unsigned counter, so keeping just 0-9 saves about 1.7 KiB on the device.
_FREESANS9_DIGIT_BITMAP = unhexlify(
    b"3c6642c3c3c3c3c3c3c342663c113f33333333303e31b07830181c1c1c1818100807f8"
    b"3c66c3c303061c0703c3c3663c0c187162c9a346fe183060c07f2010080807f38c030180"
    b"f06c63e01e3198780c06f38d83c1e0d06c63e0ff030206040c081818181030303e31b078"
    b"3c1b18f8c6c1e0f06c63e03c66c2c3c3c3673b0303c2663c"
)

# bitmap offset, width, height, xAdvance, xOffset, yOffset
_FREESANS9_DIGIT_GLYPHS = (
    (0, 8, 13, 10, 1, -12),
    (13, 4, 13, 10, 3, -12),
    (20, 9, 13, 10, 1, -12),
    (35, 8, 13, 10, 1, -12),
    (48, 7, 13, 10, 2, -12),
    (60, 9, 13, 10, 1, -12),
    (75, 9, 13, 10, 1, -12),
    (90, 8, 13, 10, 0, -12),
    (103, 9, 13, 10, 1, -12),
    (118, 8, 13, 10, 1, -12),
)


class SSD1681:
    FULL_REFRESH = 0
    FAST_REFRESH = 1
    PARTIAL_REFRESH = 2

    def __init__(self, spi, cs, dc, reset, busy, rotation=2, low_memory=False):
        self.spi = spi
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.dc = Pin(dc, Pin.OUT, value=1)
        self.reset_pin = Pin(reset, Pin.OUT, value=1)
        self.busy = Pin(busy, Pin.IN)
        self.rotation = rotation & 3
        self.width = 200
        self.height = 200
        self.buffer = bytearray(self.width * self.height // 8)
        self._previous = bytearray(len(self.buffer))
        for index in range(len(self._previous)):
            self._previous[index] = 0xFF
        self.fb = framebuf.FrameBuffer(self.buffer, self.width, self.height,
                                       framebuf.MONO_HLSB)
        self._ram = None if low_memory else bytearray(len(self.buffer))
        self._row = bytearray(25) if low_memory else None
        self._initial_refresh = True
        self._awake = False
        self.idle_callback = None
        self.fill(1)

    def _command(self, command, data=None):
        self.cs.off()
        try:
            self.dc.off()
            self.spi.write(bytes((command,)))
            if data is not None:
                self.dc.on()
                self.spi.write(data)
        finally:
            self.cs.on()

    def _data(self, data):
        self.cs.off()
        try:
            self.dc.on()
            self.spi.write(data)
        finally:
            self.cs.on()

    def wait_ready(self, timeout_ms=15000):
        start = time.ticks_ms()
        while self.busy.value():
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                raise OSError("SSD1681 BUSY timeout")
            if self.idle_callback is not None:
                self.idle_callback()
            time.sleep_ms(10)

    def reset(self):
        self.reset_pin.off()
        time.sleep_ms(10)
        self.reset_pin.on()
        time.sleep_ms(10)

    def begin(self):
        self.reset()
        self._command(0x12)
        time.sleep_ms(10)
        self.wait_ready()
        self._full_init()
        self._initial_refresh = True
        self._awake = True

    def _full_init(self):
        self._command(0x01, b"\xC7\x00\x00")
        self._command(0x11, b"\x03")
        self._command(0x44, b"\x00\x18")
        self._command(0x45, b"\x00\x00\xC7\x00")
        self._command(0x3C, b"\x05")
        self._command(0x18, b"\x80")
        self._set_address()

    def _set_address(self):
        self._command(0x4E, b"\x00")
        self._command(0x4F, b"\x00\x00")

    def _set_ram_window(self, x, y, width, height):
        right, bottom = x + width - 1, y + height - 1
        self._command(0x11, b"\x03")
        self._command(0x44, bytes((x // 8, right // 8)))
        self._command(0x45, bytes((y & 255, y >> 8,
                                   bottom & 255, bottom >> 8)))
        self._command(0x4E, bytes((x // 8,)))
        self._command(0x4F, bytes((y & 255, y >> 8)))

    def _region_to_ram(self, x, y, width, height):
        right, bottom = min(self.width, x + width), min(self.height, y + height)
        x, y = max(0, x), max(0, y)
        width, height = right - x, bottom - y
        if width <= 0 or height <= 0:
            return None
        if self.rotation == 1:
            x, y, width, height = 200 - y - height, x, height, width
        elif self.rotation == 2:
            x, y = 200 - x - width, 200 - y - height
        elif self.rotation == 3:
            x, y, width, height = y, 200 - x - width, height, width
        right = (x + width + 7) & ~7
        x &= ~7
        return x, y, right - x, height

    def _write_ram_region(self, command, data, region):
        self._set_ram_window(*region)
        self._command(command)
        x, y, width, height = region
        view = memoryview(data) if data is not None else None
        row_bytes = width // 8
        for row in range(y, y + height):
            offset = row * 25 + x // 8
            if view is None:
                self._data(self._encode_row(row, x, width))
            else:
                self._data(view[offset:offset + row_bytes])
            if row & 3 == 3 and self.idle_callback is not None:
                self.idle_callback()

    def _encode_row(self, y, x, width):
        """Encode one controller row without allocating a full rotated frame."""
        pixel, rotation, row = self.fb.pixel, self.rotation, self._row
        for index in range(width // 8):
            value = 0
            for bit in range(8):
                px = x + index * 8 + bit
                if rotation == 0:
                    color = pixel(px, y)
                elif rotation == 1:
                    color = pixel(y, 199 - px)
                elif rotation == 2:
                    color = pixel(199 - px, 199 - y)
                else:
                    color = pixel(199 - y, px)
                value = (value << 1) | color
            row[index] = value
        return memoryview(row)[:width // 8]

    def _commit_region(self, current, region):
        x, y, width, height = region
        count = width // 8
        for row in range(y, y + height):
            offset = row * 25 + x // 8
            source = self._encode_row(row, x, width) if current is None else memoryview(current)[offset:offset + count]
            self._previous[offset:offset + count] = source
            if row & 3 == 3 and self.idle_callback is not None:
                self.idle_callback()

    def _ensure_awake(self):
        if not self._awake:
            self.reset()
            self._command(0x12)
            time.sleep_ms(10)
            self.wait_ready()
            self._full_init()
            self._awake = True

    def _ram_bytes(self):
        # Serialize explicitly so framebuf bit order cannot mirror the panel.
        ram = self._ram
        if ram is None:
            return None
        pixel = self.fb.pixel
        rotation = self.rotation
        index = 0
        for y in range(200):
            for x in range(0, 200, 8):
                value = 0
                for bit in range(8):
                    px = x + bit
                    if rotation == 0:
                        color = pixel(px, y)
                    elif rotation == 1:
                        color = pixel(y, 199 - px)
                    elif rotation == 2:
                        color = pixel(199 - px, 199 - y)
                    else:
                        color = pixel(199 - y, px)
                    value = (value << 1) | color
                ram[index] = value
                index += 1
            if y & 3 == 3 and self.idle_callback is not None:
                self.idle_callback()
        return ram

    def show(self, mode=FULL_REFRESH, sleep=True):
        if mode not in (self.FULL_REFRESH, self.FAST_REFRESH, self.PARTIAL_REFRESH):
            raise ValueError("Invalid display refresh mode")
        self._ensure_awake()
        full = mode == self.FULL_REFRESH or self._initial_refresh
        update = 0xF7 if full else 0xFC
        # A preceding partial update may have left a small RAM window active.
        self._set_ram_window(0, 0, 200, 200)
        current = self._ram_bytes()
        if current is None:
            self._write_ram_region(0x26, None if full else self._previous, (0, 0, 200, 200))
            self._write_ram_region(0x24, None, (0, 0, 200, 200))
        else:
            self._command(0x26)
            self._data(current if full else self._previous)
            self._set_address()
            self._command(0x24)
            self._data(current)
        self._command(0x22, bytes((update,)))
        self._command(0x20)
        self.wait_ready()
        if current is None:
            self._commit_region(None, (0, 0, 200, 200))
        else:
            self._previous[:] = current
        self._initial_refresh = False
        if sleep:
            self.sleep()

    def show_region(self, x, y, width, height, sleep=False):
        """Refresh an aligned logical rectangle, preserving the rest of RAM."""
        region = self._region_to_ram(x, y, width, height)
        if region is None:
            return
        was_awake = self._awake
        self._ensure_awake()
        current = self._ram_bytes()
        if not was_awake or self._initial_refresh:
            # Reset/hibernate loses controller RAM. Restore the displayed image
            # before changing only the requested region.
            for command in (0x26, 0x24):
                self._write_ram_region(command, self._previous, (0, 0, 200, 200))
        if self._initial_refresh:
            # GxEPD2's initial writeScreenBuffer clears white with F7 before
            # the requested partial image is written and refreshed with FC.
            self._command(0x22, b"\xF7")
            self._command(0x20)
            self.wait_ready()
            self._initial_refresh = False
        self._write_ram_region(0x26, self._previous, region)
        self._write_ram_region(0x24, current, region)
        self._set_ram_window(*region)
        self._command(0x22, b"\xFC")
        self._command(0x20)
        self.wait_ready()
        # GxEPD2_BW.nextPage calls writeImageAgain after a fast partial update.
        for command in (0x26, 0x24):
            self._write_ram_region(command, current, region)
        self._commit_region(current, region)
        if sleep:
            self.sleep()

    def set_ram_value_base_map(self, mode=FAST_REFRESH):
        """Set both controller RAM planes white, like setRAMValueBaseMap()."""
        self.fill(1)
        self.show(self.FULL_REFRESH, sleep=False)

    def sleep(self):
        if not self._awake:
            return
        self._command(0x22, b"\x83")
        self._command(0x20)
        self.wait_ready()
        self._command(0x10, b"\x01")
        time.sleep_ms(100)
        self._awake = False

    def fill(self, color):
        self.fb.fill(color)

    def pixel(self, x, y, color=None):
        return self.fb.pixel(x, y) if color is None else self.fb.pixel(x, y, color)

    def text(self, text, x, y, color=0):
        self.fb.text(str(text), x, y, color)

    def gfx_text(self, text, x, y, color=0, size=1, wrap=True):
        """Draw text with Adafruit GFX's classic font and cursor rules."""
        cursor_x = x
        cursor_y = y
        for character in str(text):
            if character == "\n":
                cursor_x = 0
                cursor_y += size * 8
                continue
            if character == "\r":
                continue

            if wrap and cursor_x + size * 6 > self.width:
                cursor_x = 0
                cursor_y += size * 8

            code = ord(character)
            if code < 32 or code > 127:
                code = 127
            glyph_offset = (code - 32) * 5
            for glyph_x in range(5):
                column = _GFX_FONT_5X7[glyph_offset + glyph_x]
                for glyph_y in range(8):
                    if column & (1 << glyph_y):
                        if size == 1:
                            self.fb.pixel(cursor_x + glyph_x,
                                          cursor_y + glyph_y, color)
                        else:
                            self.fb.fill_rect(cursor_x + glyph_x * size,
                                              cursor_y + glyph_y * size,
                                              size, size, color)
            cursor_x += size * 6
        return cursor_x, cursor_y

    def freesans9_digits(self, text, x, y, color=0, size=1, wrap=True):
        """Draw 0-9 with FreeSans9pt7b using Adafruit GFX baseline rules."""
        cursor_x = x
        cursor_y = y
        for character in str(text):
            if character == "\n":
                cursor_x = 0
                cursor_y += size * 22
                continue
            if character == "\r" or character < "0" or character > "9":
                continue

            glyph = _FREESANS9_DIGIT_GLYPHS[ord(character) - ord("0")]
            offset, width, height, advance, x_offset, y_offset = glyph
            if wrap and cursor_x + size * (x_offset + width) > self.width:
                cursor_x = 0
                cursor_y += size * 22

            bit_number = 0
            bits = 0
            for glyph_y in range(height):
                for glyph_x in range(width):
                    if bit_number & 7 == 0:
                        bits = _FREESANS9_DIGIT_BITMAP[offset]
                        offset += 1
                    if bits & 0x80:
                        pixel_x = cursor_x + (x_offset + glyph_x) * size
                        pixel_y = cursor_y + (y_offset + glyph_y) * size
                        if size == 1:
                            self.fb.pixel(pixel_x, pixel_y, color)
                        else:
                            self.fb.fill_rect(pixel_x, pixel_y, size, size,
                                              color)
                    bits = (bits << 1) & 0xFF
                    bit_number += 1
            cursor_x += advance * size
        return cursor_x, cursor_y

    def gfx_font_text(self, text, x, y, font, color=0, size=1, wrap=True):
        """Draw an Adafruit GFXfont with the same baseline and wrap rules."""
        bitmap, glyphs, first, last, y_advance = font
        cursor_x = x
        cursor_y = y
        for character in str(text):
            if character == "\n":
                cursor_x = 0
                cursor_y += size * y_advance
                continue
            if character == "\r":
                continue

            code = ord(character)
            if code < first or code > last:
                continue
            glyph_index = (code - first) * 7
            offset = glyphs[glyph_index] | (glyphs[glyph_index + 1] << 8)
            width = glyphs[glyph_index + 2]
            height = glyphs[glyph_index + 3]
            advance = glyphs[glyph_index + 4]
            x_offset = glyphs[glyph_index + 5]
            y_offset = glyphs[glyph_index + 6]
            if x_offset > 127:
                x_offset -= 256
            if y_offset > 127:
                y_offset -= 256

            if width and height:
                if wrap and cursor_x + size * (x_offset + width) > self.width:
                    cursor_x = 0
                    cursor_y += size * y_advance

                bit_number = 0
                bits = 0
                for glyph_y in range(height):
                    for glyph_x in range(width):
                        if bit_number & 7 == 0:
                            bits = bitmap[offset]
                            offset += 1
                        if bits & 0x80:
                            pixel_x = cursor_x + (x_offset + glyph_x) * size
                            pixel_y = cursor_y + (y_offset + glyph_y) * size
                            if size == 1:
                                self.fb.pixel(pixel_x, pixel_y, color)
                            else:
                                self.fb.fill_rect(pixel_x, pixel_y, size,
                                                  size, color)
                        bits = (bits << 1) & 0xFF
                        bit_number += 1
            cursor_x += advance * size
        return cursor_x, cursor_y

    def draw_bitmap(self, x, y, bitmap, width, height, color=0):
        """Draw an Adafruit GFX 1-bit, row-major, MSB-first bitmap."""
        row_bytes = (width + 7) // 8
        for bitmap_y in range(height):
            row = bitmap_y * row_bytes
            for bitmap_x in range(width):
                if bitmap[row + (bitmap_x >> 3)] & (0x80 >> (bitmap_x & 7)):
                    self.fb.pixel(x + bitmap_x, y + bitmap_y, color)

    def line(self, x1, y1, x2, y2, color=0):
        self.fb.line(x1, y1, x2, y2, color)

    def rect(self, x, y, width, height, color=0, fill=False):
        self.fb.rect(x, y, width, height, color, fill)

    def fill_rect(self, x, y, width, height, color=0):
        self.fb.fill_rect(x, y, width, height, color)

    def hline(self, x, y, width, color=0):
        self.fb.hline(x, y, width, color)

    def vline(self, x, y, height, color=0):
        self.fb.vline(x, y, height, color)

    def blit(self, source, x, y, key=-1):
        self.fb.blit(source, x, y, key)
