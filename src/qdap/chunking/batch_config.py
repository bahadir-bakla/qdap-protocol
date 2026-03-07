"""
Batch Configuration
=====================

Determines how many chunks to group into a single QFrame.
Target: ~2MB per batch for optimal throughput/overhead balance.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BatchConfig:
    SMALL_BATCH: int = 2
    DEFAULT_BATCH: int = 8
    LARGE_BATCH: int = 16
    JUMBO_BATCH: int = 32

    @classmethod
    def for_payload(cls, payload_size: int, chunk_size: int) -> int:
        """Select optimal batch size based on payload and chunk size."""
        cfg = cls()
        total_chunks = (payload_size + chunk_size - 1) // chunk_size

        if total_chunks <= 4:
            return 1

        target_batch_bytes = 2 * 1024 * 1024  # 2MB target
        batch = max(1, target_batch_bytes // chunk_size)

        if batch <= 2:
            return cfg.SMALL_BATCH
        if batch <= 8:
            return cfg.DEFAULT_BATCH
        if batch <= 16:
            return cfg.LARGE_BATCH
        return cfg.JUMBO_BATCH
