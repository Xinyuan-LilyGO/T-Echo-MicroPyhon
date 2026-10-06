"""Single, double and long press recognizer."""

import time


class Button:
    SINGLE_CLICK = 1
    DOUBLE_CLICK = 2
    LONG_PRESS = 3

    def __init__(self, pin):
        self.pin = pin
        self.high_triggered_count = 0
        self.low_triggered_count = 0
        self.paragraph_triggered_count = 0
        self._active = False
        self._previous = -1
        self._deadline = 0
        self._next_sample = 0

    def reset(self):
        self._active = False
        self._previous = -1
        self.high_triggered_count = 0
        self.low_triggered_count = 0
        self.paragraph_triggered_count = 0

    def poll_gesture(self, window_ms=1000, sample_ms=50):
        """Poll the Arduino example's 50 ms, one-second gesture scanner."""
        now = time.ticks_ms()
        if not self._active:
            if self.pin.value():
                return None
            print("Press button to trigger start")
            self._active = True
            self._previous = -1
            self.high_triggered_count = 0
            self.low_triggered_count = 0
            self.paragraph_triggered_count = 0
            self._deadline = time.ticks_add(now, window_ms)
            self._next_sample = now

        if time.ticks_diff(now, self._next_sample) >= 0:
            current = self.pin.value()
            if current != self._previous:
                self.paragraph_triggered_count += 1
                self._previous = current
            if current:
                self.high_triggered_count += 1
            else:
                self.low_triggered_count += 1
            self._next_sample = time.ticks_add(now, sample_ms)

        if time.ticks_diff(now, self._deadline) < 0:
            return None

        print("end")
        print("high_triggered_count: %d" % self.high_triggered_count)
        print("low_triggered_count: %d" % self.low_triggered_count)
        print("paragraph_triggered_count: %d" % self.paragraph_triggered_count)
        count = self.paragraph_triggered_count
        self._active = False
        if count == 1:
            return self.LONG_PRESS
        if count == 4:
            return self.DOUBLE_CLICK
        if count == 2:
            return self.SINGLE_CLICK
        return None

    def wait_gesture(self, window_ms=1000):
        if not self._active and self.pin.value():
            return None
        while True:
            gesture = self.poll_gesture(window_ms)
            if gesture is not None or not self._active:
                return gesture
            time.sleep_ms(1)
