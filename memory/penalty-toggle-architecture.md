---
name: penalty-toggle-architecture
description: Hard/soft ceza bileşenleri scenario.toggles/weights ile aç-kapa edilebilir
metadata:
  type: reference
---

Değerlendirme `app/evaluation/eval_core.py` → `evaluate_constraints` içinde. Her hard/soft bileşen `scenario["toggles"].get(code, True)` ile kontrol edilir (varsayılan açık), her soft terim `scenario["weights"][w_key]` ile ağırlıklanır.

Soft terimler (kod → ağırlık anahtarı):
- `SOFT_MOLD_CHANGE_MINIMIZE` → `w_mold_change` (toplam kalıp değişimi sayısı)
- `SOFT_NIGHT_MOLD_CHANGE` → `w_night_mold_change` (gece segmentinde kalıp değişimi)
- `SOFT_INVENTORY_LOW` → `w_inventory` (pozitif kapanış stoğu, qty)
- `SOFT_MACHINE_COUNT_LOW` → `w_machine_count` (kullanılan makine sayısı)

İlgili hard: `HARD_NO_NIGHT_MOLD_SETUP` (gece setup yasağı). Bu kod örnek girdinin toggles listesinde YOK → varsayılan True (aktif).

Optimize akışı: `app/optimization/optimizer.py` → `optimize_frame(frame, payload)`. Senaryo (weights/toggles) frame'in `scenarioConfig`'inden gelir; payload weights/toggles override ETMEZ. Ablation/varyant için her senaryoyu ayrı `scenarioConfig` ile `load_problem_frame` edip çağırmak gerekir. `evaluate_state(state, problemData, scenarioConfig)` saf fonksiyondur; in-process çağrılabilir. Kullanım örneği: [[ablation-experiment]].
