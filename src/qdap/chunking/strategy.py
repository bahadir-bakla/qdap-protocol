"""
QFT-Guided Chunk Strategy
============================

Maps QFT energy bands to optimal chunk sizes.
High frequency → small chunks (latency critical).
Low frequency → large chunks (throughput critical).
"""

from enum import IntEnum


class ChunkStrategy(IntEnum):
    MICRO  = 4   * 1024        #   4KB — burst IoT, realtime
    SMALL  = 16  * 1024        #  16KB — small RPC
    MEDIUM = 64  * 1024        #  64KB — general purpose
    LARGE  = 256 * 1024        # 256KB — bulk transfer
    JUMBO  = 1   * 1024 * 1024 #   1MB — large file transfer

    @classmethod
    def from_energy_bands(
        cls, low: float, mid: float, high: float, payload_size: int,
        has_spectrum_data: bool = True,
    ) -> 'ChunkStrategy':
        """Select optimal chunk size from QFT energy bands."""
        # No spectrum data → use payload-size-aware fallback
        if not has_spectrum_data:
            return cls._payload_size_default(payload_size)

        if high > 0.50:
            return cls.MICRO
        if high > 0.35:
            return cls.SMALL
        if low > 0.70:
            if payload_size > 10 * 1024 * 1024:
                return cls.JUMBO
            if payload_size > 1 * 1024 * 1024:
                return cls.LARGE
            return cls.MEDIUM
        if low > 0.50:
            return cls.LARGE
        if payload_size < 64 * 1024:
            return cls.SMALL
        if payload_size < 1 * 1024 * 1024:
            return cls.MEDIUM
        return cls.LARGE

    @classmethod
    def _payload_size_default(cls, payload_size: int) -> 'ChunkStrategy':
        """
        Payload-size-aware fallback when no spectrum data.
        Inspired by RFC 7540 frame sizing guidelines.
        """
        if payload_size < 32 * 1024:
            return cls.SMALL
        if payload_size < 512 * 1024:
            return cls.MEDIUM
        if payload_size < 10 * 1024 * 1024:
            return cls.LARGE
        return cls.JUMBO   # >= 10MB → always JUMBO

    def describe(self) -> str:
        names = {
            self.MICRO:  "MICRO (4KB) — burst/IoT",
            self.SMALL:  "SMALL (16KB) — RPC",
            self.MEDIUM: "MEDIUM (64KB) — general",
            self.LARGE:  "LARGE (256KB) — bulk",
            self.JUMBO:  "JUMBO (1MB) — large file",
        }
        return names.get(self, "UNKNOWN")

