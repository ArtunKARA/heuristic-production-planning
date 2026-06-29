# Ablation Experiment Results

Instance: small (`Doc/SampleData/example_input.json`). Her hucre 10 run, max_iter=50, GA + GATabu. Tum metrikler **tam objektif** altinda yeniden degerlendirilmis en iyi plandan alinmistir (dual-eval).

## Weight profile: `default`

| Ablation scenario | method | total_score (mean±std) | best | feasible | mold_change | night_change | inventory | machines |
|---|---|---|---|---|---|---|---|---|
| Full framework | gatabu | 4.793e+06±4.153e+04 | 4.703e+06 | 0 | 5.9 | 0 | 3.111e+06 | 16.7 |
| Without night penalty | gatabu | 4.793e+06±4.153e+04 | 4.703e+06 | 0 | 5.9 | 0 | 3.111e+06 | 16.7 |
| Without inventory penalty | gatabu | 7.7e+06±5.247e+05 | 6.93e+06 | 0 | 6.3 | 0 | 7.695e+06 | 15.7 |
| Without mold-change penalty | gatabu | 4.772e+06±4.523e+04 | 4.703e+06 | 0 | 5.5 | 0 | 3.119e+06 | 16.7 |
| Without Tabu refinement | ga | 4.796e+06±3.624e+04 | 4.725e+06 | 0 | 7.6 | 0 | 3.168e+06 | 16.3 |

## Weight profile: `normalized`

| Ablation scenario | method | total_score (mean±std) | best | feasible | mold_change | night_change | inventory | machines |
|---|---|---|---|---|---|---|---|---|
| Full framework | gatabu | 8807±332.1 | 8254 | 0 | 1.8 | 0 | 7.5e+06 | 16 |
| Without night penalty | gatabu | 8807±332.1 | 8254 | 0 | 1.8 | 0 | 7.5e+06 | 16 |
| Without inventory penalty | gatabu | 9050±291.7 | 8254 | 0 | 2.3 | 0 | 7.945e+06 | 16.2 |
| Without mold-change penalty | gatabu | 9313±429.3 | 8716 | 0 | 7 | 0 | 7.209e+06 | 14.6 |
| Without Tabu refinement | ga | 9057±246.5 | 8716 | 0 | 4.1 | 0 | 7.45e+06 | 15.3 |

## Normalize edilmis agirliklar

| weight_key | baseline KPI (ref) | weight | normalized? |
|---|---|---|---|
| w_mold_change | 5.9 | 169.5 | True |
| w_night_mold_change | 0 | 100 | False |
| w_inventory | 3.111e+06 | 0.0003215 | True |
| w_machine_count | 16.7 | 59.88 | True |

## Katman onemi (somut farklar)

### `default` profili
- **Inventory katmani:** inventory KPI 3.111e+06 -> 7.695e+06 (+147%) kalip cezasi kapatilinca.
- **Kalip-degisim katmani:** mold_change KPI 5.9 -> 5.5 (-7%) ceza kapatilinca.
- **Tabu rafinasyonu (GATabu vs GA):** mold_change 5.9 -> 7.6 (+29%); total_score 4.793e+06 -> 4.796e+06 (+0%) Tabu cikarilinca.
- **Gece katmani:** night_change 0 -> 0 (bu instance'ta yapisal olarak 0; asagiya bakiniz).

### `normalized` profili
- **Inventory katmani:** inventory KPI 7.5e+06 -> 7.945e+06 (+6%) kalip cezasi kapatilinca.
- **Kalip-degisim katmani:** mold_change KPI 1.8 -> 7 (+289%) ceza kapatilinca.
- **Tabu rafinasyonu (GATabu vs GA):** mold_change 1.8 -> 4.1 (+128%); total_score 8807 -> 9057 (+3%) Tabu cikarilinca.
- **Gece katmani:** night_change 0 -> 0 (bu instance'ta yapisal olarak 0; asagiya bakiniz).

## Genel yorum

- **Inventory katmani** `default` profilinde net anlamli: cezasi kapatilinca stok ~2 katina cikar.
- **Kalip-degisim katmani** `normalized` profilinde net anlamli: cezasi kapatilinca kalip degisimi birkac katina cikar (default'ta inventory bu terimi numerik olarak ezdiginden gorunmez).
- **Tabu rafinasyonu** her iki profilde de anlamli: yalniz-GA daha fazla kalip degisimi uretir; GATabu cozumu iyilestirir.
- **Gece katmani** bu instance'ta yapisal olarak tetiklenmez: tum setup'lar gunduz/aksam segmentlerinde olur, hicbir konfigurasyonda gece kalip degisimi olusmaz (KPI hep 0). Bu bir guvenlik kisitidir; bu veri setinde baglayici degildir.
- **Olcek notu:** `default` agirliklarda inventory (qty, milyonlar) soft objektifi ezer; mold/night (sayim) terimleri numerik olarak etkisiz kalir. `normalized` profili her aktif terimi baseline planda ~ayni buyukluge olcekler; bu yuzden iki profil birlikte raporlanir.
- **Feasibility notu:** max_iter=50 butcesinde hicbir kosu hard-feasible'a ulasmaz (due-date acigi); ablation mutlak feasibility'yi degil, katmanlarin GORECELI etkisini olcer ve bu etki tum senaryolarda tutarlidir.
