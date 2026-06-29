---
name: long-runs-foreground-chunks
description: Bu ortamda arka plan süreçleri ölüyor; uzun koşuları foreground parçalarla çalıştır
metadata:
  type: feedback
---

Bu makinede `run_in_background: true` ile başlatılan uzun python koşuları güvenilir değil — süreç ~15-20 dk sonra (makine uykusu veya oturum yaşam döngüsü) sessizce ölüyor; tamamlanma bildirimi gelmiyor. Ablation deneyinde arka plan koşusu 3 kez (16/100, 28/100, 0 ilerleme) öldü.

**Why:** Sandbox/oturum, detached arka plan süreçlerini canlı tutmuyor.

**How to apply:** Saatler süren işleri (a) **resume** destekli yap (her run'ı CSV'ye artımlı yaz, mevcut satırları atla) ve (b) **foreground parçalarla** çalıştır — her Bash çağrısı 10 dk timeout'a kadar koşar, süreç çağrı içinde bittiği için kill edilmez. `Doc/ablation_runner.py` bunu `--max-new-runs N` arg'ı ile destekler (~7 run ≈ 8.5 dk, güvenli). Parçaları arka arkaya çağırıp bitir. İlgili iş: [[ablation-experiment]].
