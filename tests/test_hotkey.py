"""Hotkey state machine tests with simulated key events (no real keyboard input is sent)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yap import hotkey  # noqa: E402

DOWN, UP = hotkey.WM_KEYDOWN, hotkey.WM_KEYUP
LCTRL, LWIN, D, SHIFT, LALT = 0xA2, 0x5B, 0x44, 0xA0, 0xA4


def make(spec="ctrl+win"):
    events, masks = [], []
    hotkey.tap_key = lambda vk: masks.append(vk)  # don't inject real keys in tests
    hk = hotkey.HoldHotkey(spec, lambda: events.append("start"), lambda: events.append("stop"),
                           lambda: events.append("cancel"))
    hk._physically_down = lambda vk: vk in hk.down  # pretend the OS agrees with what we saw
    def send(msg, vk, extra=0):
        hk._filter(msg, SimpleNamespace(vkCode=vk, dwExtraInfo=extra))
    return hk, send, events, masks


def test_basic_hold_and_release():
    hk, send, ev, masks = make()
    send(DOWN, LCTRL); send(DOWN, LWIN); send(DOWN, LWIN)  # includes auto-repeat
    assert ev == ["start"] and masks == [hotkey.VK_MASK]
    send(UP, LWIN); send(UP, LCTRL)
    assert ev == ["start", "stop"]


def test_either_order_and_release_ctrl_first():
    hk, send, ev, _ = make()
    send(DOWN, LWIN); send(DOWN, LCTRL); send(UP, LCTRL); send(UP, LWIN)
    assert ev == ["start", "stop"]


def test_other_key_cancels_and_needs_full_release():
    hk, send, ev, _ = make()
    send(DOWN, LCTRL); send(DOWN, LWIN); send(DOWN, D)   # Ctrl+Win+D = new desktop
    assert ev == ["start", "cancel"]
    send(UP, D); send(DOWN, D); send(UP, D)
    assert ev == ["start", "cancel"]  # no restart while still holding Ctrl+Win
    send(UP, LWIN); send(UP, LCTRL)
    send(DOWN, LCTRL); send(DOWN, LWIN)
    assert ev == ["start", "cancel", "start"]


def test_extra_modifier_already_held_does_not_start():
    hk, send, ev, _ = make()
    send(DOWN, SHIFT); send(DOWN, LCTRL); send(DOWN, LWIN)
    assert ev == []


def test_own_injected_keys_ignored():
    hk, send, ev, _ = make()
    send(DOWN, LCTRL); send(DOWN, LWIN)
    send(DOWN, hotkey.VK_MASK, hotkey.YAP_TAG); send(UP, hotkey.VK_MASK, hotkey.YAP_TAG)
    send(UP, LWIN)
    assert ev == ["start", "stop"]


def test_stale_key_is_forgotten():
    hk, send, ev, _ = make()
    hk._physically_down = lambda vk: False  # OS says nothing else is held
    hk.down = {LWIN}                         # we missed Win's key-up earlier
    send(DOWN, LCTRL)
    assert ev == [] and hk.down == {LCTRL}


def test_single_key_and_alt_combo():
    hk, send, ev, masks = make("f9")
    send(DOWN, 0x78); send(UP, 0x78)
    assert ev == ["start", "stop"] and masks == []
    hk, send, ev, masks = make("ctrl+alt")
    send(DOWN, LCTRL); send(DOWN, LALT)
    assert ev == ["start"] and masks == [hotkey.VK_MASK]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
