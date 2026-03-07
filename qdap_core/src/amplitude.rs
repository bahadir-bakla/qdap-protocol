// qdap_core/src/amplitude.rs

/// L2 normalizasyon — ||v||₂ = 1
/// AmplitudeEncoder için kritik yol.
pub fn l2_normalize(amplitudes: &[f64]) -> Vec<f64> {
    if amplitudes.is_empty() {
        return vec![];
    }

    // L2 norm hesapla: sqrt(sum(x^2))
    let norm: f64 = amplitudes
        .iter()
        .map(|x| x * x)
        .sum::<f64>()
        .sqrt();

    if norm < 1e-10 {
        // Sıfır vektör — uniform dağılım döndür
        let uniform = 1.0 / (amplitudes.len() as f64).sqrt();
        return vec![uniform; amplitudes.len()];
    }

    amplitudes.iter().map(|x| x / norm).collect()
}

/// Deadline'lardan amplitude ağırlıkları hesapla.
/// Küçük deadline → yüksek ağırlık (öncelikli)
/// 
/// Formül: weight_i = 1 / deadline_i, sonra L2 normalize et
pub fn deadline_to_weights(deadlines_ms: &[f64]) -> Vec<f64> {
    if deadlines_ms.is_empty() {
        return vec![];
    }

    // Inverse deadline → ham ağırlıklar
    // min deadline = 1.0 (en öncelikli)
    let min_deadline = deadlines_ms
        .iter()
        .cloned()
        .fold(f64::INFINITY, f64::min)
        .max(0.001);   // Sıfır deadline koruması

    let raw_weights: Vec<f64> = deadlines_ms
        .iter()
        .map(|d| min_deadline / d.max(0.001))
        .collect();

    l2_normalize(&raw_weights)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_l2_norm_is_one() {
        let v = vec![3.0, 4.0];
        let n = l2_normalize(&v);
        let norm: f64 = n.iter().map(|x| x * x).sum::<f64>().sqrt();
        assert!((norm - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_deadline_priority() {
        // 2ms deadline → 500ms deadline'dan yüksek ağırlık
        let deadlines = vec![2.0, 500.0];
        let weights   = deadline_to_weights(&deadlines);
        assert!(weights[0] > weights[1]);
    }
}
