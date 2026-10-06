"""True nRF52840 SYSTEMOFF, including the ubluepy/S140 SoftDevice case.

Wakeup GPIO SENSE and peripheral cleanup must be configured by the caller.
Upstream nRF machine.deepsleep() resets the board, so it is not used here.
SVC numbers are from Nordic S140 6.1.1/7.3.0 nrf_sdm.h and nrf_soc.h:
SD_SOFTDEVICE_IS_ENABLED = 0x12; SD_POWER_SYSTEM_OFF = 0x2C + 7 = 0x33.
"""

import machine


# Compile the fixed native helpers lazily. A build with machine.system_off()
# need not enable MicroPython's optional Thumb inline-assembler compiler.
_ASM_SOURCE = """import micropython
@micropython.asm_thumb
def _sd_is_enabled(r0):
    data(2, 0xDF12)


@micropython.asm_thumb
def _sd_system_off():
    data(2, 0xDF33)


@micropython.asm_thumb
def _raw_system_off():
    movw(r0, 0x0500)
    movt(r0, 0x4000)
    mov(r1, 1)
    str(r1, [r0, 0])
    data(2, 0xF3BF, 0x8F4F)  # DSB SY: commit SYSTEMOFF before the wait.
    label(wait_off)
    data(2, 0xBF20)  # WFE; Nordic's emulated System OFF when debugging.
    b(wait_off)
"""


def _compile_power_calls():
    namespace = {}
    try:
        exec(_ASM_SOURCE, namespace)
    except (SyntaxError, AttributeError, ImportError):
        raise RuntimeError("Firmware needs machine.system_off() or Thumb inline assembly")
    return namespace


def _verified_s140():
    # S140 is located above the 0x1000-byte MBR. These documented image
    # metadata fields also reject erased flash or a different BLE stack.
    mem = machine.mem32
    info_size = mem[0x3000] & 255
    softdevice_size = mem[0x3008] & 0xFFFFFFFF
    variant = mem[0x3010] & 0xFFFFFFFF
    version = mem[0x3014] & 0xFFFFFFFF
    return (info_size >= 0x18 and info_size <= 0x80 and variant == 140 and
            0x10000 <= softdevice_size <= 0x40000 and
            version // 1000000 in (6, 7))


def system_off():
    """Enter System OFF; normal hardware execution never returns.

    A board build exposing machine.system_off() can supply its own native
    implementation. Otherwise this module requires MicroPython Thumb inline
    assembly. SVC instructions are used only with ubluepy and a verified S140
    image; stock bluetooth/NimBLE firmware uses the hardware register path.
    """
    native = getattr(machine, "system_off", None)
    if native is not None:
        native()
        raise RuntimeError("Native SYSTEMOFF unexpectedly returned")

    if machine.mem32[0x10000100] != 0x52840:
        raise RuntimeError("Direct SYSTEMOFF requires the nRF52840 target")

    try:
        import ubluepy
    except ImportError:
        _compile_power_calls()["_raw_system_off"]()
        raise RuntimeError("Hardware SYSTEMOFF unexpectedly returned")

    if not _verified_s140():
        raise RuntimeError("Cannot verify S140 v6/v7; firmware needs machine.system_off()")
    import uctypes
    calls = _compile_power_calls()
    enabled = bytearray(4)
    status = calls["_sd_is_enabled"](uctypes.addressof(enabled))
    if status:
        raise OSError("SoftDevice state query failed: %d" % status)
    if enabled[0]:
        status = calls["_sd_system_off"]()
        raise OSError("SoftDevice SYSTEMOFF unexpectedly returned: %d" % status)
    calls["_raw_system_off"]()
    raise RuntimeError("Hardware SYSTEMOFF unexpectedly returned")
