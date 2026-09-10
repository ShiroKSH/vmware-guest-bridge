"""Local VMware guest operations and RFB console access."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vmware-guest-bridge")
except PackageNotFoundError:
    __version__ = "0+uninstalled"
