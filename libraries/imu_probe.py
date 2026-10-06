"""Read-only discovery for the optional MPU9250 and ICM20948 modules."""


def find_imu(i2c, sensor_class, name, accepted_ids):
    """Inspect both AD0 addresses before constructing a sensor driver.

    Read the known hardware ID register directly, so older installed driver
    versions do not need to implement a new diagnostic method.
    """
    try:
        scanned = i2c.scan()
        print("I2C scan: " + (", ".join("0x%02X" % a for a in scanned) or "empty"))
    except (AttributeError, OSError) as error:
        scanned = None
        print("I2C scan unavailable: %s" % error)

    found = None
    details = []
    identity_register = 0x00 if 0xEA in accepted_ids else 0x75
    for address in (0x68, 0x69):
        if scanned is not None and address not in scanned:
            details.append("0x%02X=no ACK (not in scan)" % address)
            continue
        try:
            who = i2c.readfrom_mem(address, identity_register, 1)[0]
        except OSError as error:
            details.append("0x%02X=NACK/read error (%s)" % (address, error))
            continue
        details.append("0x%02X=0x%02X" % (address, who))
        if found is None and who in accepted_ids:
            found = address
    print("%s WHO_AM_I: %s" % (name, ", ".join(details)))
    if found is not None:
        return sensor_class(i2c, found)

    # T-Echo Plus commonly carries a BHI260AP, not either optional IMU.
    # An address alone is insufficient: check Bosch's product ID as well.
    for address in (0x28, 0x29):
        if scanned is not None and address not in scanned:
            continue
        try:
            who = i2c.readfrom_mem(address, 0x1C, 1)[0]
        except OSError:
            continue
        if who == 0x89:
            print("BHI260AP-family detected at 0x%02X (product ID 0x89)." % address)
            print("Use SensorBHI260AP.py for this sensor; it is not %s." % name)
    print("Check the actual sensor, 3.3 V, GND, SDA=P0.26 and SCL=P0.27.")
    raise OSError("%s not found; WHO_AM_I: %s" % (name, ", ".join(details)))
