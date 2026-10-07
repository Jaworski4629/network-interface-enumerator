# Network Interface Enumerator

Lists local network interfaces — name, address, netmask, flags — in a single portable list of `InterfaceInfo` objects. Standard library only, no C extensions, no third-party packages.

## Usage

```python
from network_interface_enumerator import enumerate_interfaces, InterfaceInfo, InterfaceFlag

for iface in enumerate_interfaces():
    print(iface.name, iface.address, iface.netmask, iface.flags)
    if InterfaceFlag.UP in iface.flags and InterfaceFlag.LOOPBACK not in iface.flags:
        print("  -> usable non-loopback interface")
```

`enumerate_interfaces()` returns a `list[InterfaceInfo]` sorted by `(name, family)`. Each entry has `name: str`, `address: str`, `netmask: str | None`, `flags: InterfaceFlag`, and `family: int` (a `socket.AF_*` constant).

## Why this exists

The problem: you need to know what network interfaces a host has, with addresses and netmasks, without pulling in `netifaces` or `psutil`. On Linux the standard library gives you just enough — `fcntl.ioctl` with `SIOCGIFCONF`, `SIOCGIFNETMASK`, and `SIOCGIFFLAGS` — to do this accurately. On other platforms there is no portable pure-Python path to real interface names, so the library falls back to a heuristic that resolves the local hostname and synthesises names (`"lo"` for loopback, `"default"` for everything else). The trade-off is explicit: accurate data on Linux, best-effort structural data elsewhere.

## Edge cases you will hit

- **Non-Linux platforms return synthetic interface names.** If you need real `en0`/`eth0` names on macOS or Windows, this library will not give them to you — use a platform-specific binding instead.
- **`netmask` can be `None`.** The fallback path cannot derive netmasks for non-loopback addresses. Check for `None` before parsing.
- **IPv6 netmasks via ioctl are best-effort.** The Linux `SIOCGIFNETMASK` path handles IPv6, but some kernels return the wrong family; in that case `netmask` is `None`.
- **Buffer overflow raises `RuntimeError`.** If a host has more than 1024 interfaces, the ioctl buffer is exceeded and the call fails loudly rather than returning a truncated list.

## Exports

- `enumerate_interfaces() -> list[InterfaceInfo]`
- `InterfaceInfo` (frozen dataclass: `name`, `address`, `netmask`, `flags`, `family`)
- `InterfaceFlag` (`IntFlag`: `UP`, `BROADCAST`, `LOOPBACK`, `POINTOPOINT`, `RUNNING`, `MULTICAST`)

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

## Limitations

Values are coerced to floats, so very large integers lose precision. If you need
exact integer aggregates over a window, this is the wrong tool.

