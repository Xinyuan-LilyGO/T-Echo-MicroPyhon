"""Original LILYGO 200x200 logo bitmap."""


def logo(display, rotation=0):
    """Draw the bitmap with an optional horizontal/180-degree transform."""
    path = __file__.replace("\\", "/")
    directory = path.rsplit("/", 1)[0] if "/" in path else "."
    with open(directory + "/lilygo_logo.bin", "rb") as source:
        display.fill(1)
        row = bytearray(25)
        for y in range(200):
            if source.readinto(row) != 25:
                raise OSError("Truncated lilygo_logo.bin; copy the whole libraries folder")
            for x in range(200):
                if not row[x >> 3] & (0x80 >> (x & 7)):
                    if rotation == 1:
                        display.pixel(199 - x, y, 0)
                    elif rotation & 2:
                        display.pixel(199 - x, 199 - y, 0)
                    else:
                        display.pixel(x, y, 0)
