"""Port of SensorICM29048.ino; original filename spelling is preserved."""

import sys
_here = globals().get("__file__", "").replace("\\", "/")
_base = _here.rsplit("/", 3)[0] if _here.count("/") >= 3 else "."
for _path in ("/flash/libraries", "/libraries", "/lib", _base + "/libraries", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

import math
import time
from t_echo_config import power_on, shared_i2c
from icm20948 import ICM20948
from imu_probe import find_imu


def _make_imu(i2c):
    """Find ICM20948 at either legal AD0-selected I2C address."""
    return find_imu(i2c, ICM20948, "ICM20948", (0xEA,))


def main():
    power_on()
    time.sleep_ms(100)
    print("ICM20948 example v2: checking the actual sensor")
    imu = None
    try:
        i2c = shared_i2c(freq=100000)
        imu = _make_imu(i2c)
        imu.begin(calibrate=False)
        print("ICM20948 is connected at 0x%02X" % imu.address)
        print("Magnetometer is connected")
        while True:
            accel, gyro, temperature, mag = imu.read()
            print("Acceleration in g (x,y,z):")
            print("%.2f   %.2f   %.2f" % accel)
            print("Resultant g: %.2f" % math.sqrt(sum(v * v for v in accel)))
            print("Gyroscope data in degrees/s:")
            print("%.2f   %.2f   %.2f" % gyro)
            print("Magnetometer Data in uTesla:")
            print("%.2f   %.2f   %.2f" % mag)
            print("Temperature in C: %.2f" % temperature)
            print("********************************************")
            time.sleep_ms(1000)
    except KeyboardInterrupt:
        print("ICM20948 stopped.")
    except OSError as error:
        print("ICM20948 stopped: %s" % error)
    finally:
        if imu is not None:
            try:
                imu.sleep()
            except OSError as error:
                print("ICM20948 cleanup: %s" % error)


if __name__ == "__main__":
    main()
