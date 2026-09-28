"""Core implementation for enumerating network interfaces.

Design decisions
----------------

* **Standard library only.** The brief forbids third-party dependencies.
  Python's ``socket`` module exposes ``ioctl`` on Linux via ``fcntl.ioctl``
  and the ``SIOCGIFCONF`` / ``SIOCGIFNETMASK`` / ``SIOCGIFFLAGS`` constants
  in ``socket``.  On non-Linux platforms there is no portable ioctl-based
  path that works without C extensions, so we fall back to a pure-Python
  heuristic using ``socket.getaddrinfo`` against the hostname — this still
  produces useful address/netmask data for loopback and primary interfaces.

* **One structure, one list.** ``enumerate_interfaces`` always returns a
  list of ``InterfaceInfo`` objects, sorted by interface name then address
  family.  Callers do not need to branch on platform.

* **Flags as an IntEnum.** Interface flags are bitfields; an ``IntEnum``
  lets callers write ``InterfaceFlag.UP in info.flags`` while still
  allowing bitwise operations on raw values when needed.

* **Netmask may be ``None``.** The heuristic fallback cannot always derive
  a netmask (e.g. for IPv6 link-local addresses).  Rather than invent a
  placeholder, we return ``None`` and document it.
"""

from __future__ import annotations

import socket
import struct
import sys
from dataclasses import dataclass, field
from enum import IntFlag
from typing import List, Optional


class InterfaceFlag(IntFlag):
    """Subset of interface flags exposed portably.

    We only model the flags that are meaningful across the platforms we
    support and that callers are likely to act on.  Unknown bits are
    preserved via the integer value of the flag set; callers can inspect
    ``int(info.flags)`` for the raw value if they need a platform-specific
    bit.
    """

    UP = 0x1
    BROADCAST = 0x2
    LOOPBACK = 0x8
    POINTOPOINT = 0x10
    RUNNING = 0x40
    MULTICAST = 0x1000


@dataclass(frozen=True)
class InterfaceInfo:
    """A single address bound to a single interface.

    An interface with both IPv4 and IPv6 addresses yields two
    ``InterfaceInfo`` instances sharing the same ``name``.
    """

    name: str
    address: str
    netmask: Optional[str]
    flags: InterfaceFlag
    family: int = socket.AF_INET
    """Address family integer (``socket.AF_INET`` or ``socket.AF_INET6``)."""


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _linux_enumerate() -> List[InterfaceInfo]:
    """Linux-specific enumeration via ``ioctl(SIOCGIFCONF)``.

    We pack a fixed-size buffer of 4096 bytes for the ``ifconf`` structure.
    On a typical host this is far more than enough; if it ever overflows we
    raise ``RuntimeError`` rather than silently truncating, because a
    truncated interface list is a subtle bug that callers cannot detect.
    """
    import fcntl  # Linux-only import; not available on Windows.

    SIOCGIFCONF = 0x8912
    SIOCGIFFLAGS = 0x8913
    SIOCGIFNETMASK = 0x891D

    # ``ifreq`` is 40 bytes: 16-byte name + 24-byte union.
    IFREQ_SIZE = 40
    MAX_INTERFACES = 1024
    BUF_SIZE = IFREQ_SIZE * MAX_INTERFACES

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # ifconf structure: 16-bit length + padding + pointer.
        # We use the packed form ``i40s`` via struct.
        ifconf = bytearray(struct.pack("iP", BUF_SIZE, 0))
        # ``ioctl`` expects a mutable buffer for the ifconf payload.
        ifconf += bytearray(BUF_SIZE)
        fcntl.ioctl(sock.fileno(), SIOCGIFCONF, ifconf)
        # Unpack the actual length written back.
        actual_len = struct.unpack("i", bytes(ifconf[:4]))[0]
        if actual_len >= BUF_SIZE:
            raise RuntimeError(
                "interface buffer overflow: %d bytes needed" % actual_len
            )
        raw = ifconf[8 : 8 + actual_len]

        results: List[InterfaceInfo] = []
        for offset in range(0, len(raw), IFREQ_SIZE):
            chunk = raw[offset : offset + IFREQ_SIZE]
            if len(chunk) < IFREQ_SIZE:
                break
            name_bytes = chunk[:16]
            name = name_bytes.split(b"\x00", 1)[0].decode("ascii", "replace")
            # The union after the name holds a ``sockaddr_in`` for
            # SIOCGIFCONF responses: 2-byte family, 2-byte port, 4-byte
            # addr, 8 bytes padding.
            family = struct.unpack("H", chunk[16:18])[0]
            if family == socket.AF_INET:
                addr = socket.inet_ntop(socket.AF_INET, chunk[20:24])
            elif family == socket.AF_INET6:
                # AF_INET6 sockaddr is 24 bytes; only the ioctl on Linux
                # with a STREAM socket returns these, and it is uncommon.
                # We handle it defensively.
                addr = socket.inet_ntop(socket.AF_INET6, chunk[24:40])
            else:
                continue

            # Fetch flags.
            flags_req = bytearray(40)
            flags_req[:16] = name.encode("ascii")[:16]
            try:
                flags_resp = fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, flags_req)
                raw_flags = struct.unpack("16sH", bytes(flags_resp[:18]))[1]
            except OSError:
                raw_flags = 0

            # Fetch netmask.
            netmask: Optional[str] = None
            try:
                nm_req = bytearray(40)
                nm_req[:16] = name.encode("ascii")[:16]
                nm_resp = fcntl.ioctl(sock.fileno(), SIOCGIFNETMASK, nm_req)
                nm_family = struct.unpack("H", bytes(nm_resp[16:18]))[0]
                if nm_family == socket.AF_INET:
                    netmask = socket.inet_ntop(socket.AF_INET, bytes(nm_resp[20:24]))
                elif nm_family == socket.AF_INET6:
                    netmask = socket.inet_ntop(socket.AF_INET6, bytes(nm_resp[24:40]))
            except OSError:
                netmask = None

            results.append(
                InterfaceInfo(
                    name=name,
                    address=addr,
                    netmask=netmask,
                    flags=InterfaceFlag(raw_flags & 0x1FFF),
                    family=family,
                )
            )

        return results
    finally:
        sock.close()


def _fallback_enumerate() -> List[InterfaceInfo]:
    """Heuristic enumeration for non-Linux platforms.

    We resolve the local hostname via ``socket.getaddrinfo`` and treat
    each returned address as belonging to a single synthetic interface
    named ``"lo"`` for loopback addresses and ``"default"`` otherwise.
    This is imprecise — we cannot map addresses to real interface names
    without platform-specific C calls — but it is deterministic and avoids
    any dependency on ctypes or compiled extensions.

    The netmask is derived from the address class for IPv4 loopback only;
    for everything else it is ``None`` because we genuinely do not know it.
    """
    results: List[InterfaceInfo] = []
    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        infos = []

    # Always include the loopback explicitly so callers have at least one
    # entry even when hostname resolution fails (e.g. in a container with
    # no /etc/hosts entry).
    loopback_entries = [
        ("lo", "127.0.0.1", "255.0.0.0", socket.AF_INET, InterfaceFlag.UP | InterfaceFlag.LOOPBACK | InterfaceFlag.RUNNING),
        ("lo", "::1", None, socket.AF_INET6, InterfaceFlag.UP | InterfaceFlag.LOOPBACK | InterfaceFlag.RUNNING),
    ]
    for name, addr, mask, fam, flags in loopback_entries:
        results.append(
            InterfaceInfo(name=name, address=addr, netmask=mask, flags=flags, family=fam)
        )

    seen = {("lo", "127.0.0.1"), ("lo", "::1")}
    for family, _stype, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        if family == socket.AF_INET:
            addr = sockaddr[0]
        else:
            addr = sockaddr[0]
        key = ("default", addr)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            InterfaceInfo(
                name="default",
                address=addr,
                netmask=None,
                flags=InterfaceFlag.UP | InterfaceFlag.RUNNING,
                family=family,
            )
        )

    return results


def enumerate_interfaces() -> List[InterfaceInfo]:
    """List local network interfaces and their configured addresses.

    Returns
    -------
    list[InterfaceInfo]
        Sorted by ``(name, family)``.  An interface with multiple
        addresses appears once per address.

    Notes
    -----
    On Linux this uses ``ioctl`` directly for accurate interface names,
    addresses, netmasks, and flags.  On other platforms a heuristic based
    on ``socket.getaddrinfo`` is used; interface names are synthetic and
    netmasks are ``None`` except for loopback.
    """
    if _is_linux():
        try:
            results = _linux_enumerate()
        except OSError:
            # ioctl may fail inside restricted sandboxes without
            # /proc/net; fall back gracefully.
            results = _fallback_enumerate()
    else:
        results = _fallback_enumerate()

    # Ensure at least the loopback entries are present: on Linux the ioctl
    # path may not include the loopback interface in restricted
    # environments, and on non-Linux platforms hostname resolution may fail.
    has_loopback = any(
        InterfaceFlag.LOOPBACK in info.flags for info in results
    )
    if not has_loopback:
        results = _fallback_enumerate() + results

    # De-duplicate: the ioctl path can return the same (name, addr, family)
    # twice when an interface has aliases.
    seen = set()
    unique: List[InterfaceInfo] = []
    for info in results:
        key = (info.name, info.address, info.family)
        if key in seen:
            continue
        seen.add(key)
        unique.append(info)

    unique.sort(key=lambda i: (i.name, i.family))
    return unique
