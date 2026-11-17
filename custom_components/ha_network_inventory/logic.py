"""Pure logic (no Home Assistant imports): IP ranges and status evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network

STATUS_OK = "ok"
STATUS_DHCP = "dhcp"
STATUS_NO_LABEL = "no_label"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_WRONG_SEGMENT = "wrong_segment"
STATUS_OUTSIDE = "outside"
STATUS_UNKNOWN = "unknown"

STATUSES = [
    STATUS_OK,
    STATUS_DHCP,
    STATUS_NO_LABEL,
    STATUS_AMBIGUOUS,
    STATUS_WRONG_SEGMENT,
    STATUS_OUTSIDE,
    STATUS_UNKNOWN,
]

# Statuses that need attention.
PROBLEM_STATUSES = [
    STATUS_DHCP,
    STATUS_NO_LABEL,
    STATUS_AMBIGUOUS,
    STATUS_WRONG_SEGMENT,
    STATUS_OUTSIDE,
]


@dataclass(frozen=True)
class IpRange:
    """An inclusive range of IPv4 addresses."""

    start: IPv4Address
    end: IPv4Address

    @classmethod
    def parse(cls, text: str) -> IpRange:
        """Parse '192.168.189.0/24', '192.168.188.1-192.168.188.49',
        '192.168.188.1-49' or a single address. Raises ValueError."""
        text = (text or "").strip().replace(" ", "")
        if not text:
            raise ValueError("empty range")
        if "/" in text:
            net = ip_network(text, strict=False)
            if not isinstance(net, IPv4Network):
                raise ValueError("only IPv4 is supported")
            return cls(net.network_address, net.broadcast_address)
        if "-" in text:
            first, last = text.split("-", 1)
            start = ip_address(first)
            if "." not in last:
                # Shorthand: 192.168.188.1-49
                last = ".".join(first.split(".")[:3] + [last])
            end = ip_address(last)
        else:
            start = end = ip_address(text)
        if not isinstance(start, IPv4Address) or not isinstance(end, IPv4Address):
            raise ValueError("only IPv4 is supported")
        if start > end:
            raise ValueError("start after end")
        return cls(start, end)

    def contains(self, ip: IPv4Address) -> bool:
        return self.start <= ip <= self.end

    def overlaps(self, other: IpRange) -> bool:
        return self.start <= other.end and other.start <= self.end

    def within(self, other: IpRange) -> bool:
        return other.start <= self.start and self.end <= other.end

    @property
    def size(self) -> int:
        return int(self.end) - int(self.start) + 1

    def __str__(self) -> str:
        # Show as CIDR when it is exactly one network.
        if self.start == self.end:
            return str(self.start)
        try:
            prefix = 32 - (self.size - 1).bit_length()
            net = IPv4Network(f"{self.start}/{prefix}")
            if net.network_address == self.start and net.broadcast_address == self.end:
                return str(net)
        except ValueError:
            pass
        return f"{self.start} – {self.end}"


@dataclass(frozen=True)
class Segment:
    """An IP segment bound to a label."""

    label_id: str
    range: IpRange


def parse_ip(value: object) -> IPv4Address | None:
    """Return an IPv4 address or None."""
    if not value:
        return None
    try:
        ip = ip_address(str(value).strip())
    except ValueError:
        return None
    return ip if isinstance(ip, IPv4Address) else None


def evaluate(
    ip: IPv4Address | None,
    labels: set[str],
    segments: list[Segment],
    dhcp: IpRange | None,
) -> tuple[str, tuple[str, ...]]:
    """Return (status, label_ids of matching segments)."""
    segment_ids = tuple(c.label_id for c in segments if c.label_id in labels)
    if ip is None:
        return STATUS_UNKNOWN, segment_ids
    if dhcp is not None and dhcp.contains(ip):
        return STATUS_DHCP, segment_ids
    if not segment_ids:
        return STATUS_NO_LABEL, segment_ids
    if len(segment_ids) > 1:
        return STATUS_AMBIGUOUS, segment_ids
    own = next(c for c in segments if c.label_id == segment_ids[0])
    if own.range.contains(ip):
        return STATUS_OK, segment_ids
    if any(c.range.contains(ip) for c in segments):
        return STATUS_WRONG_SEGMENT, segment_ids
    return STATUS_OUTSIDE, segment_ids


def validate_layout(
    network: IpRange, segments: list[Segment], dhcp: IpRange | None
) -> str | None:
    """Return an error key if the layout is inconsistent, else None."""
    ranges = [c.range for c in segments] + ([dhcp] if dhcp else [])
    for rng in ranges:
        if not rng.within(network):
            return "outside_network"
    for i, a in enumerate(ranges):
        for b in ranges[i + 1 :]:
            if a.overlaps(b):
                return "overlap"
    return None
