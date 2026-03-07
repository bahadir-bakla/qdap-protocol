"""
TCP Socket Tuning — QDAP-Optimized Socket Configuration
=========================================================

Platform-aware socket options for minimizing latency
and maximizing throughput on TCP connections.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass


@dataclass
class TCPTuningConfig:
    """
    QDAP için optimize edilmiş TCP socket ayarları.
    Her ayarın benchmark'a etkisi not edilmiştir.
    """

    # Nagle algoritması — QDAP kendi batching'ini yapıyor,
    # Nagle bize latency kaybettirir
    tcp_nodelay: bool = True           # Etki: p99 latency ↓ ~20%

    # Send/recv buffer — QFrame max boyutuna göre ayarla
    # Default: 87380 bytes → Biz: 4MB (büyük QFrame'ler için)
    send_buffer_size: int = 4 * 1024 * 1024   # Etki: throughput ↑
    recv_buffer_size: int = 4 * 1024 * 1024

    # Keepalive — Ghost Session'ın üstüne transport-level sağlık
    keepalive_enabled: bool = True
    keepalive_idle:    int = 30   # 30s sessizlikte keepalive gönder
    keepalive_interval: int = 5  # 5s aralıkla tekrar
    keepalive_count:   int = 3   # 3 başarısız → bağlantı kes

    # TCP_CORK — burst gönderimde paketleri birleştir
    # Sadece Linux'ta çalışır, Mac'te ignore edilir
    use_cork: bool = False

    # SO_REUSEADDR + SO_REUSEPORT — hızlı restart için
    reuse_addr: bool = True
    reuse_port: bool = True


def apply_tuning(sock: socket.socket, config: TCPTuningConfig) -> None:
    """
    Socket'e tüm optimizasyonları uygula.
    Mac ve Linux uyumlu — desteklenmeyen opsiyonlar sessizce skip edilir.
    """
    if config.tcp_nodelay:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, config.send_buffer_size)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, config.recv_buffer_size)
    except OSError:
        pass  # Some systems may reject large buffer sizes

    if config.reuse_addr:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    if config.reuse_port:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (OSError, AttributeError):
            pass  # Not available on all platforms

    if config.keepalive_enabled:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Platform-specific keepalive parametreleri
        if hasattr(socket, 'TCP_KEEPIDLE'):   # Linux
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,
                                config.keepalive_idle)
            except OSError:
                pass
        if hasattr(socket, 'TCP_KEEPINTVL'):  # Linux + Mac
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL,
                                config.keepalive_interval)
            except OSError:
                pass
        if hasattr(socket, 'TCP_KEEPCNT'):    # Linux + Mac
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT,
                                config.keepalive_count)
            except OSError:
                pass

    if config.use_cork and hasattr(socket, 'TCP_CORK'):  # Linux only
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_CORK, 1)
        except OSError:
            pass
