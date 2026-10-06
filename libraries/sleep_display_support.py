"""USB, filesystem and nRF sleep lifecycle for the Sleep_Display sketch.

All imports and native SYSTEMOFF preparation happen before /flash is unmounted.
Newer firmware exposes ``machine.usb_active()`` so TinyUSB can be shut down
cleanly.  Older T-Echo firmware has no Python-callable USB teardown; in that
case the example can still run, but it leaves USB active and reports that
degraded mode to the caller.
"""

import os
import time
import machine
import t_echo_config as board


class SleepResources:
    def __init__(self, require_usb=True):
        """Create the resource manager.

        ``require_usb`` keeps the exact low-power behaviour as the default.
        Sleep_Display explicitly opts into permissive mode so it remains
        usable with an existing UF2.
        """
        self._require_usb = bool(require_usb)
        self._prepared = False
        self._flash = None
        self._firmware_flash = None
        self._mounted = False
        self._unmounted = False
        self._flash_asleep = False
        self._spi_released = False
        self._usb_closed = False
        self._cwd = None

    def prepare(self):
        """Check capabilities and load everything needed while flash is awake."""
        if self._prepared:
            return self
        if machine.mem32[0x10000100] != 0x52840:
            raise RuntimeError("Sleep_Display requires the nRF52840 target")
        self._usb_active = getattr(machine, "usb_active", None)
        if not callable(self._usb_active):
            self._usb_active = None
        if self._usb_active is None and self._require_usb:
            raise RuntimeError(
                "USB shutdown API unavailable; use require_usb=False "
                "to keep USB active on this firmware")
        self._usb_was_active = (self._usb_active()
                                if self._usb_active is not None else None)
        self._wait_for_event = machine.lightsleep

        try:
            import vfs
        except ImportError:
            vfs = os
        self._vfs = vfs
        self._sync = os.sync
        self._cwd = os.getcwd()
        self._mounted = board.external_flash_mounted()
        try:
            import t_echo_spiflash
        except ImportError:
            t_echo_spiflash = None
        self._firmware_flash = getattr(t_echo_spiflash, "_bdev", None)
        # Unmounting a volume does not release its hardware SPI controller.
        self._flash = self._firmware_flash
        if self._mounted:
            owner = self._firmware_flash
            if owner is None:
                raise RuntimeError("Cannot identify the firmware owner of /flash")
            info = os.statvfs(board.EXTERNAL_FLASH_MOUNT)
            if (info[0] != owner.BLOCK_SIZE or info[2] != owner.BLOCK_COUNT):
                raise RuntimeError(
                    "/flash does not match the external flash volume; "
                    "restart with the T-Echo-Plus external filesystem")
        if self._flash is None:
            from spi_nor import SpiNor
            self._raw_flash_class = SpiNor

        self._prepare_system_off()
        self._prepared = True
        return self

    def _prepare_system_off(self):
        native = getattr(machine, "system_off", None)
        if native is not None:
            self._enter_off = native
            return
        import nrf_power
        calls = nrf_power._compile_power_calls()
        self._enter_off = calls["_raw_system_off"]
        try:
            import ubluepy
        except ImportError:
            return
        if not nrf_power._verified_s140():
            raise RuntimeError("Cannot verify S140 v6/v7 for SYSTEMOFF")
        import uctypes
        enabled = bytearray(4)
        status = calls["_sd_is_enabled"](uctypes.addressof(enabled))
        if status:
            raise OSError("SoftDevice state query failed: %d" % status)
        if enabled[0]:
            self._enter_off = calls["_sd_system_off"]

    def flash_identity(self):
        """Read the original chip without replacing an existing SPI owner."""
        if not self._prepared:
            raise RuntimeError("Call SleepResources.prepare() first")
        if self._flash is None:
            self._flash = self._raw_flash_class(board.flash_spi(), board.Flash_Cs)
            self._flash.wake()
        identity = self._flash.jedec_id()
        if isinstance(identity, int):
            identity = identity.to_bytes(3, "big")
        return identity

    def close_usb(self):
        """Close USB when the firmware supports it.

        Returns ``True`` when the firmware actually disabled USB.  Returning
        ``False`` is the compatibility path for the stock UF2: all other
        shutdown steps are still safe to perform, but USB may keep the board
        awake and increase sleep current.
        """
        if self._usb_closed:
            return True
        self._sync()
        if self._usb_active is None:
            return False
        self._usb_active(False)
        self._usb_closed = True
        return True

    def sleep_flash(self):
        """Commit and unmount the volume before sending the NOR B9 command."""
        if self._flash is None:
            raise RuntimeError("Read flash_identity() before sleeping flash")
        if self._flash_asleep:
            return
        self._sync()
        if self._mounted and not self._unmounted:
            os.chdir("/")
            self._vfs.umount(board.EXTERNAL_FLASH_MOUNT)
            self._unmounted = True
        self._flash_asleep = True
        if self._flash is self._firmware_flash:
            self._flash._wait_ready()
            self._flash._command(0xB9)
            time.sleep_us(10)
            self._flash._spi.deinit()
        else:
            self._flash.sleep()
            time.sleep_us(10)
            self._flash.spi.deinit()
        self._spi_released = True

    def light_sleep(self):
        # nRF machine.lightsleep() is __WFE(), not a timed sleep. This mirrors
        # the sketch's 30 waitForEvent() + delay(1000) iterations exactly.
        for _ in range(30):
            self._wait_for_event()
            time.sleep_ms(1000)

    def system_off(self):
        result = self._enter_off()
        raise OSError("SYSTEMOFF unexpectedly returned: %r" % (result,))

    def restore(self):
        """Recover the volume and USB after an exception; never format flash."""
        try:
            if self._flash_asleep:
                machine.Pin(board.VDD3_3_EN, machine.Pin.OUT, value=1)
                time.sleep_ms(12)
                machine.Pin(board.Flash_WP, machine.Pin.OUT, value=1)
                machine.Pin(board.Flash_HOLD, machine.Pin.OUT, value=1)
                machine.Pin(board.Flash_Cs, machine.Pin.OUT, value=1)
                if self._flash is self._firmware_flash:
                    self._flash._spi.init(baudrate=8000000, polarity=0, phase=0)
                    self._flash._command(0xAB)
                    time.sleep_ms(12)
                else:
                    self._flash.spi = board.flash_spi()
                    self._flash.wake()
                self._flash_asleep = False
                self._spi_released = False
            if self._unmounted:
                filesystem = self._vfs.VfsLfs2(self._firmware_flash)
                self._vfs.mount(filesystem, board.EXTERNAL_FLASH_MOUNT)
                self._unmounted = False
            if self._cwd is not None:
                os.chdir(self._cwd)
        finally:
            if self._usb_closed and self._usb_active is not None:
                self._usb_active(self._usb_was_active)
                self._usb_closed = False
