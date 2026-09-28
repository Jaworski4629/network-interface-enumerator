"""Network Interface Enumerator.

A small, dependency-free library for listing local network interfaces,
their addresses, netmasks, and flags in a portable structure.
"""

from .core import (
    InterfaceInfo,
    InterfaceFlag,
    enumerate_interfaces,
)

__all__ = [
    "InterfaceInfo",
    "InterfaceFlag",
    "enumerate_interfaces",
]

__version__ = "1.0.0"
