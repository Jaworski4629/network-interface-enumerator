"""Tests for network_interface_enumerator.core.

These tests are deliberately deterministic and avoid asserting on any
value that depends on the host's actual network configuration beyond
structural invariants (types, sorting, uniqueness, flag membership).
"""

import socket
import unittest

from network_interface_enumerator import (
    InterfaceFlag,
    InterfaceInfo,
    enumerate_interfaces,
)


class TestInterfaceInfoDataclass(unittest.TestCase):
    def test_frozen_and_hashable(self):
        info = InterfaceInfo(
            name="eth0",
            address="10.0.0.1",
            netmask="255.255.255.0",
            flags=InterfaceFlag.UP,
        )
        # frozen dataclass must be hashable.
        hash(info)
        with self.assertRaises(Exception):
            info.name = "eth1"  # type: ignore[misc]

    def test_default_family_is_inet(self):
        info = InterfaceInfo(
            name="x", address="1.2.3.4", netmask=None, flags=InterfaceFlag.UP
        )
        self.assertEqual(info.family, socket.AF_INET)


class TestInterfaceFlag(unittest.TestCase):
    def test_bitwise_combination(self):
        combo = InterfaceFlag.UP | InterfaceFlag.RUNNING
        self.assertIn(InterfaceFlag.UP, combo)
        self.assertIn(InterfaceFlag.RUNNING, combo)
        self.assertNotIn(InterfaceFlag.LOOPBACK, combo)

    def test_int_value(self):
        self.assertEqual(int(InterfaceFlag.UP), 0x1)
        self.assertEqual(int(InterfaceFlag.LOOPBACK), 0x8)


class TestEnumerateInterfaces(unittest.TestCase):
    def test_returns_list(self):
        result = enumerate_interfaces()
        self.assertIsInstance(result, list)

    def test_entries_are_interface_info(self):
        result = enumerate_interfaces()
        for entry in result:
            self.assertIsInstance(entry, InterfaceInfo)

    def test_non_empty(self):
        # At minimum the loopback fallback guarantees an entry.
        result = enumerate_interfaces()
        self.assertGreater(len(result), 0)

    def test_sorted_by_name_then_family(self):
        result = enumerate_interfaces()
        keys = [(e.name, e.family) for e in result]
        self.assertEqual(keys, sorted(keys))

    def test_unique_by_name_address_family(self):
        result = enumerate_interfaces()
        keys = [(e.name, e.address, e.family) for e in result]
        self.assertEqual(len(keys), len(set(keys)))

    def test_address_is_string(self):
        result = enumerate_interfaces()
        for entry in result:
            self.assertIsInstance(entry.address, str)
            self.assertGreater(len(entry.address), 0)

    def test_netmask_is_str_or_none(self):
        result = enumerate_interfaces()
        for entry in result:
            self.assertTrue(entry.netmask is None or isinstance(entry.netmask, str))

    def test_flags_are_interface_flag(self):
        result = enumerate_interfaces()
        for entry in result:
            self.assertIsInstance(entry.flags, InterfaceFlag)

    def test_contains_loopback(self):
        result = enumerate_interfaces()
        loopbacks = [
            e for e in result
            if InterfaceFlag.LOOPBACK in e.flags
        ]
        self.assertGreater(len(loopbacks), 0)
        for lb in loopbacks:
            self.assertTrue(
                lb.address.startswith("127.") or lb.address == "::1",
                f"loopback address unexpected: {lb.address}",
            )

    def test_family_matches_address(self):
        result = enumerate_interfaces()
        for entry in result:
            if entry.family == socket.AF_INET:
                # IPv4 dotted quad.
                parts = entry.address.split(".")
                self.assertEqual(len(parts), 4)
                for p in parts:
                    self.assertTrue(0 <= int(p) <= 255)
            elif entry.family == socket.AF_INET6:
                self.assertIn(":", entry.address)
            else:
                self.fail(f"unexpected family {entry.family}")


if __name__ == "__main__":
    unittest.main()
