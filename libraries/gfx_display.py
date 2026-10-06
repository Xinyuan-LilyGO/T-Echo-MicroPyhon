"""GFX text/cursor and paged drawing API over the SSD1681 hardware driver.

Font layout and text bounds follow the original Adafruit GFX implementation.
Application pages and their update timing belong in the example programs.
"""


class GFXDisplay:
    def __init__(self, panel):
        self.panel = panel
        self._font = None
        self._x = self._y = 0
        self._size = 1
        self._color = 0
        self._wrap = True
        self.setFullWindow()

    def width(self):
        return self.panel.width

    def height(self):
        return self.panel.height

    def setRotation(self, rotation):
        self.panel.rotation = rotation & 3

    def setFont(self, font=None):
        if font is not None and self._font is None:
            self._y += 6
        elif font is None and self._font is not None:
            self._y -= 6
        self._font = font

    def setTextSize(self, size):
        self._size = max(1, int(size))

    def setTextColor(self, color):
        self._color = color

    def setTextWrap(self, wrap):
        self._wrap = bool(wrap)

    def setCursor(self, x, y):
        self._x, self._y = int(x), int(y)

    def getCursorX(self):
        return self._x

    def getCursorY(self):
        return self._y

    def setFullWindow(self):
        self._partial = False
        self._window = (0, 0, self.width(), self.height())

    def setPartialWindow(self, x, y, width, height):
        x = min(max(0, x), self.width())
        y = min(max(0, y), self.height())
        width = min(width, self.width() - x)
        height = min(height, self.height() - y)
        # GxEPD2 expands the controller's X window to whole bytes. For
        # rotations 1/3 this is the logical Y axis.
        if self.panel.rotation & 1:
            end = min(self.height(), (y + height + 7) & ~7)
            y &= ~7
            height = end - y
        else:
            end = min(self.width(), (x + width + 7) & ~7)
            x &= ~7
            width = end - x
        self._partial = True
        self._window = (x, y, width, height)

    def firstPage(self):
        self.fillScreen(1)

    def nextPage(self):
        if self._partial:
            self.panel.show_region(*self._window, sleep=False)
        else:
            self.panel.show(self.panel.FULL_REFRESH, sleep=False)
        return False

    def update(self):
        self.panel.show(self.panel.FULL_REFRESH)

    def fillScreen(self, color):
        self.panel.fill_rect(*self._window, color)

    def drawPixel(self, x, y, color):
        wx, wy, ww, wh = self._window
        if wx <= x < wx + ww and wy <= y < wy + wh:
            self.panel.pixel(x, y, color)

    def fillRect(self, x, y, width, height, color):
        wx, wy, ww, wh = self._window
        right, bottom = min(x + width, wx + ww), min(y + height, wy + wh)
        x, y = max(x, wx), max(y, wy)
        if right > x and bottom > y:
            self.panel.fill_rect(x, y, right - x, bottom - y, color)

    def drawFastHLine(self, x, y, width, color):
        self.fillRect(x, y, width, 1, color)

    def drawBitmap(self, x, y, bitmap, width, height, color=0):
        stride = (width + 7) // 8
        for row in range(height):
            for col in range(width):
                if bitmap[row * stride + col // 8] & (0x80 >> (col & 7)):
                    self.drawPixel(x + col, y + row, color)
            if row & 3 == 3:
                self._idle()

    def _idle(self):
        callback = getattr(self.panel, 'idle_callback', None)
        if callback is not None:
            callback()

    def _glyph(self, code):
        bitmap, glyphs, first, last, y_advance = self._font
        if not first <= code <= last:
            return None
        i = (code - first) * 7
        offset = glyphs[i] | (glyphs[i + 1] << 8)
        width, height, advance, xo, yo = glyphs[i + 2:i + 7]
        return offset, width, height, advance, xo if xo < 128 else xo - 256, yo if yo < 128 else yo - 256

    def getTextBounds(self, text, x, y):
        origin_x, origin_y = x, y
        min_x, min_y = self.width(), self.height()
        max_x = max_y = -1
        size = self._size
        for ch in str(text):
            if ch == '\r':
                continue
            if ch == '\n':
                x = 0
                y += size * (self._font[4] if self._font else 8)
                continue
            glyph = self._glyph(ord(ch)) if self._font else (0, 6, 8, 6, 0, 0)
            if glyph is None:
                continue
            _, width, height, advance, xo, yo = glyph
            if self._wrap and x + size * (xo + width) > self.width():
                x = 0
                y += size * (self._font[4] if self._font else 8)
            x1, y1 = x + xo * size, y + yo * size
            x2, y2 = x1 + width * size - 1, y1 + height * size - 1
            min_x, min_y = min(min_x, x1), min(min_y, y1)
            max_x, max_y = max(max_x, x2), max(max_y, y2)
            x += advance * size
        return (min_x if max_x >= min_x else origin_x,
                min_y if max_y >= min_y else origin_y,
                max_x - min_x + 1 if max_x >= min_x else 0,
                max_y - min_y + 1 if max_y >= min_y else 0)

    def print(self, value='', digits=None):
        if isinstance(value, float):
            text = ('%.' + str(2 if digits is None else digits) + 'f') % value
        elif isinstance(value, bytes):
            text = value.decode()
        else:
            text = str(value)
        size = self._size
        for ch in text:
            if ch == '\r':
                continue
            if ch == '\n':
                self._x = 0
                self._y += size * (self._font[4] if self._font else 8)
                continue
            if self._font is None:
                self._x, self._y = self.panel.gfx_text(ch, self._x, self._y,
                    self._color, size, self._wrap)
                self._idle()
                continue
            glyph = self._glyph(ord(ch))
            if glyph is None:
                continue
            offset, width, height, advance, xo, yo = glyph
            if width and height:
                if self._wrap and self._x + size * (xo + width) > self.width():
                    self._x = 0
                    self._y += size * self._font[4]
                bitmap = self._font[0]
                for row in range(height):
                    for col in range(width):
                        bit = row * width + col
                        if bitmap[offset + bit // 8] & (0x80 >> (bit & 7)):
                            self.fillRect(self._x + (xo + col) * size,
                                          self._y + (yo + row) * size,
                                          size, size, self._color)
                    if row & 3 == 3:
                        self._idle()
            self._x += advance * size
            self._idle()

    def println(self, value='', digits=None):
        self.print(value, digits)
        self.print('\n')
