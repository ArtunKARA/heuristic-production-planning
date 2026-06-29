---
name: ablation-experiment
description: Reviewer 1 ablation deneyi — runner, tasarım kararları ve ölçek-dominansı bulgusu
metadata:
  type: project
---

Reviewer 1 açık bir ablation istedi ("framework katmanları gerçekten anlamlı mı?"). Bunun için `Doc/ablation_runner.py` yazıldı (in-process; sunucu gerektirmez).

Tasarım:
- Senaryolar resimdeki tabloyla birebir: full / no_night / no_inventory / no_mold_change / no_tabu. İlk dördü `scenario.toggles` üzerinden bir cezayı kapatır; `no_tabu` ise `gatabu` yerine `ga` (algoritma değişikliği).
- **Dual-eval:** ablated objektifle optimize → en iyi plan → TAM objektifle yeniden değerlendir. Kapatılan katmanın KPI'si ancak böyle ölçülebilir.
- Yöntemler: ga + gatabu. Bütçe: scenario×method başına 10 run, max_iter=50 (~63s/run, toplam ~1.75 saat).
- İki weight profili: `default` (girdideki ağırlıklar) + `normalized`.

Kritik bulgular (sayısal):
- **Ölçek dominansı:** `default` ağırlıklarda inventory KPI'si qty cinsinden (~milyonlar), mold/night ise sayım (tek haneli). w_inventory=1 × ~3.5M, soft objektifi ezer; mold (×10) ve night (×100) numerik olarak etkisiz kalır → o cezaları kapatmak planı değiştirmez. `normalized` profili her terimi baseline planda ~1000 katkıya ölçekler (night gibi baseline≈0 olan terim "yapısal etkisiz" sayılıp normalize edilmez).
- **Gece katmanı yapısal olarak tetiklenmiyor:** Tüm planlarda 73 lot'un tamamı DAY/EVE segmentinde; sıfır gece setup'ı. `time_shift_hours=0` ve bucket-shift gün-içi saat kaydırmadığı için optimizer 00:00–08:00 penceresini hiç keşfetmez. Hiçbir ağırlık bu ablation'ı bu instance'ta anlamlı kılamaz — raporda dürüstçe belirtildi.
- `no_night` senaryosu hem `SOFT_NIGHT_MOLD_CHANGE` hem `HARD_NO_NIGHT_MOLD_SETUP` kapatır (hard kısıt soft ablation'ı maskeliyordu).

Çıktılar `Doc/ablation_outputs/` altında: `csv/ablation_runs.csv` (ham, artımlı), `csv/ablation_summary.csv`, `ablation_table.md` (makaleye hazır), `plots/*.png`. İlgili kod: penalty toggle/weight mantığı [[penalty-toggle-architecture]].

**SONUÇ (100/100 tamamlandı):**
- Inventory katmanı (default profil): cezası kapatılınca stok 3.11M→7.69M (**+147%**) — net anlamlı.
- Kalıp-değişim katmanı (normalized profil): cezası kapatılınca mold 1.8→7.0 (**+289%**) — net anlamlı (default'ta inventory ezdiği için görünmez).
- Tabu rafinasyonu (GATabu vs GA): mold normalized'da 1.8→4.1 (+128%), default'ta 5.9→7.6 (+29%) — anlamlı.
- Gece katmanı: yapısal olarak 0 (tüm setup'lar gündüz/akşam; gece penceresi keşfedilmez) — bu instance'ta etkisiz, dürüstçe raporlandı.
- max_iter=50'de hiçbir koşu hard-feasible değil (due-date açığı); ablation göreli etkiyi ölçer, tutarlı.

Uzun koşu çalıştırma dersi: [[long-runs-foreground-chunks]].
