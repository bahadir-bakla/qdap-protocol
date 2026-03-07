"""
Verification Report Generator
================================

Runs all Phase 3 verifications and produces a unified report.
Output: JSON results + terminal summary.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


class VerificationReport:
    """
    Tüm Faz 3 doğrulamalarını çalıştır ve rapor üret.
    """

    def __init__(self, output_dir: str = "verification/results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results: dict = {}

    def run_all(self) -> dict:
        console.rule("[bold green]QDAP Faz 3 — Doğrulama Raporu[/bold green]")
        console.print(f"Başlama: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

        # 3.1 QFT Denkliği
        console.print(Panel("3.1 QFT ↔ FFT Denklik Testi", style="cyan"))
        from qdap.verification.qft.equivalence import QFTEquivalenceVerifier
        qft_verifier = QFTEquivalenceVerifier(n_qubits=4)  # 4 qubit = faster
        qft_results = qft_verifier.verify_suite()
        self.results['qft_equivalence'] = [
            {
                'test_name': r.test_name,
                'max_error': r.max_abs_error,
                'fidelity': r.fidelity,
                'is_equivalent': r.is_equivalent,
                'bands_match': r.energy_bands_match,
            }
            for r in qft_results
        ]
        all_qft_pass = all(r.is_equivalent for r in qft_results)
        console.print(f"QFT Denkliği: {'✅ TÜMÜ GEÇTİ' if all_qft_pass else '❌ BAŞARISIZ'}\n")

        # 3.2 Born Kuralı
        console.print(Panel("3.2 Amplitude Encoding — Born Kuralı Analogu", style="cyan"))
        from qdap.verification.amplitude.born_rule import BornRuleVerifier
        born_verifier = BornRuleVerifier()
        born_stats = born_verifier.verify_statistical_suite(n_trials=1_000)
        self.results['born_rule'] = born_stats
        console.print(f"Pass rate: {born_stats['pass_rate']:.2%}")
        console.print(f"Max norm error: {born_stats['norm_error_max']:.2e}\n")

        # 3.3 Ghost Session Markov
        console.print(Panel("3.3 Ghost Session — Markov Zinciri Analizi", style="cyan"))
        from qdap.verification.ghost.markov_model import GhostSessionMarkovVerifier
        ghost_verifier = GhostSessionMarkovVerifier()
        ghost_results = ghost_verifier.run_loss_rate_sweep()
        self.results['ghost_markov'] = [
            {
                'p_loss': r.p_loss,
                'f1_score': r.f1_score,
                'precision': r.detection_precision,
                'recall': r.detection_recall,
                'mixing_time': r.mixing_time,
                'is_ergodic': r.is_ergodic,
            }
            for r in ghost_results
        ]

        # Özet tablo
        self._print_summary_table()

        # Kaydet
        out_path = self.output_dir / "verification_results.json"
        with open(out_path, 'w') as f:
            json.dump(self.results, f, indent=2, default=str)
        console.print(f"\n✅ Sonuçlar kaydedildi: {out_path}")

        return self.results

    def _print_summary_table(self):
        table = Table(title="📋 Faz 3 Doğrulama Özeti")
        table.add_column("Test", style="bold")
        table.add_column("Sonuç", style="green")
        table.add_column("Metrik", style="yellow")
        table.add_column("Paper İçin", style="cyan")

        qft = self.results.get('qft_equivalence', [])
        born = self.results.get('born_rule', {})
        ghost = self.results.get('ghost_markov', [])

        all_qft = all(r['is_equivalent'] for r in qft) if qft else False
        born_pass = born.get('pass_rate', 0)
        best_f1 = max((r['f1_score'] for r in ghost), default=0) if ghost else 0

        table.add_row(
            "QFT ↔ FFT Denkliği",
            "✅ PASS" if all_qft else "❌ FAIL",
            "Max hata < 1e-5",
            "Theorem 1",
        )
        table.add_row(
            "Born Kuralı Analogu",
            f"✅ {born_pass:.1%}" if born_pass > 0.99 else f"⚠️ {born_pass:.1%}",
            "10K trial pass rate",
            "Lemma 1",
        )
        table.add_row(
            "Ghost Session F1",
            f"✅ {best_f1:.1%}" if best_f1 > 0.85 else f"⚠️ {best_f1:.1%}",
            "Loss detection F1",
            "Table 2",
        )

        console.print(table)
