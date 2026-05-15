"""
V2X Channel Model — WINNER+ B1 (Urban V2V)
Based on: ETSI TR 102 861, 3GPP TR 36.885, ITU-R M.2135
"""
import numpy as np

CARRIER_GHZ = 5.9     # DSRC/C-V2X frequency
TX_POWER_DBM = 23.0   # Typical V2X Tx power (200mW)
ANTENNA_GAIN_DB = 3.0
NOISE_FIGURE_DB = 9.0
BANDWIDTH_HZ = 10e6   # 10 MHz DSRC channel

# Thermal noise: kTB in dBm
THERMAL_NOISE_DBM = -174 + 10 * np.log10(BANDWIDTH_HZ) + 30  # ≈ -101 dBm


def path_loss_v2v(d_m: float, los: bool, fc_ghz: float = CARRIER_GHZ) -> float:
    """
    V2V path loss model — antenna heights ~1.5m.
    LOS:  Two-ray ground reflection (IEEE 802.11p PHY, validated in Gozalvez 2012).
           PL = 40*log10(d) - 20*log10(h_tx*h_rx) = 40*log10(d) - 7.0
           Better than WINNER+ for same-road vehicles at 5.9 GHz.
    NLOS: WINNER+ B1 NLOS (buildings block, Nakagami-1 fading).
           PL = 36.7*log10(d) + 22.7 + 26*log10(fc)
    """
    d = max(d_m, 1.0)
    if los:
        return 40.0 * np.log10(d) - 7.04  # two-ray, h_t=h_r=1.5m → 20*log10(2.25)=7.04
    else:
        return 36.7 * np.log10(d) + 22.7 + 26 * np.log10(fc_ghz)


# Keep old name as alias for backwards compat
def path_loss_winner_b1(d_m: float, los: bool, fc_ghz: float = CARRIER_GHZ) -> float:
    return path_loss_v2v(d_m, los, fc_ghz)


def shadowing_db(los: bool, rng: np.random.Generator) -> float:
    """Log-normal shadowing: sigma=4dB LOS, sigma=8dB NLOS"""
    return rng.normal(0, 4.0 if los else 8.0)


def nakagami_fading_db(los: bool, rng: np.random.Generator) -> float:
    """Nakagami-m fading. m=2 LOS, m=1 (Rayleigh) NLOS."""
    m = 2.0 if los else 1.0
    power = rng.gamma(shape=m, scale=1.0 / m)
    return 10 * np.log10(max(power, 1e-12))


def compute_snr_db(distance_m: float, los: bool, rng: np.random.Generator,
                   tx_power_dbm: float = TX_POWER_DBM,
                   interference_dbm: float = -110.0) -> float:
    """Instantaneous received SNR in dB, including fading."""
    pl = path_loss_winner_b1(distance_m, los)
    sh = shadowing_db(los, rng)
    fd = nakagami_fading_db(los, rng)
    rx_power = tx_power_dbm + ANTENNA_GAIN_DB - pl + sh + fd
    noise = THERMAL_NOISE_DBM + NOISE_FIGURE_DB
    noise_plus_ifx = 10 * np.log10(10 ** (noise / 10) + 10 ** (interference_dbm / 10))
    return rx_power - noise_plus_ifx


def is_los(pos1: np.ndarray, pos2: np.ndarray, buildings: list) -> bool:
    """Check LOS via AABB segment intersection."""
    for (bx, by, bw, bh) in buildings:
        if _segment_intersects_box(pos1, pos2, bx, by, bw, bh):
            return False
    return True


def _segment_intersects_box(p1, p2, bx, by, bw, bh) -> bool:
    t_min, t_max = 0.0, 1.0
    for d, p, lo, hi in [
        (p2[0] - p1[0], p1[0], bx, bx + bw),
        (p2[1] - p1[1], p1[1], by, by + bh),
    ]:
        if abs(d) < 1e-9:
            if p < lo or p > hi:
                return False
        else:
            t1, t2 = (lo - p) / d, (hi - p) / d
            if t1 > t2:
                t1, t2 = t2, t1
            t_min, t_max = max(t_min, t1), min(t_max, t2)
            if t_min > t_max:
                return False
    return True


def compute_cbr(n_vehicles: int, bsm_rate_hz: float = 10.0,
                bsm_bytes: int = 400, data_rate_mbps: float = 6.0) -> float:
    """Channel Busy Ratio — fraction of channel occupied by BSMs."""
    bsm_duration_s = (bsm_bytes * 8) / (data_rate_mbps * 1e6)
    return min(n_vehicles * bsm_rate_hz * bsm_duration_s, 1.0)


def snr_to_per(snr_db: float, protocol: str) -> float:
    """
    SNR -> Packet Error Rate using sigmoid approximation.
    Parameters derived from published BER curves for each PHY.
    DSRC:    BPSK-1/2 at 6Mbps  (SNR0~6dB)
    802.11bd: LDPC +3dB gain     (SNR0~3dB)
    C-V2X:   LTE turbo coding    (SNR0~4dB)
    """
    params = {
        "dsrc":    (6.0, 0.80),
        "80211bd": (3.0, 0.90),
        "cv2x":    (4.0, 0.85),
        "udp":     (6.5, 0.75),
        "mqtt":    (6.5, 0.75),
        "qdap":    (6.0, 0.80),  # same PHY; adaptive FEC handled separately
    }
    snr0, k = params.get(protocol, (6.0, 0.80))
    return 1.0 / (1.0 + np.exp(k * (snr_db - snr0)))
