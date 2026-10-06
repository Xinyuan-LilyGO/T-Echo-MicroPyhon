"""Port of PDMSerialPlotter.ino: 16 kHz mono signed PCM samples."""

import sys
_here = globals().get("__file__", "").replace("\\", "/")
_base = _here.rsplit("/", 3)[0] if _here.count("/") >= 3 else "."
for _path in ("/libraries", "/lib", _base + "/libraries", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

import struct
from nrf_pdm import NrfPDM
from t_echo_config import power_on


def main():
    power_on()
    microphone = NrfPDM(data=6, clock=8, select=15, gain=30, sample_rate=16000)
    buffer = bytearray(512)
    try:
        while True:
            bytes_read = microphone.readinto(buffer)
            print('**************si***********%d' % bytes_read)
            for offset in range(0, bytes_read, 2):
                print(struct.unpack_from("<h", buffer, offset)[0])
    finally:
        microphone.stop()


if __name__ == "__main__":
    main()
