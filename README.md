# SuperSmar EV Charging per Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> Integrazione HACS generica per la ricarica intelligente di veicoli elettrici.  
> Compatibile con qualsiasi EV, qualsiasi wallbox MQTT e qualsiasi impianto fotovoltaico.

---

## Funzionalità

| Funzione | Descrizione |
|---|---|
| ☀️ **Surplus FV** | Regolazione dinamica della corrente in base all'eccedenza fotovoltaica |
| 🌙 **Fascia off-peak** | Ricarica durante la fascia tariffaria economica (es. F3 italiana, Tibber, Omie) |
| ⚡ **Load Balancing** | Prevenzione automatica dello sforamento della potenza contrattuale |
| 🔋 **Dual SOC Target** | Target SOC separato per uso quotidiano e per ricarica solare/completa |
| 🚀 **Forza Ricarica** | Override immediato indipendente da fascia o disponibilità solare |
| 🛑 **Master Stop** | Blocco globale di tutte le ricariche con un solo switch |
| 📡 **MQTT generico** | Topic e payload MQTT completamente configurabili per qualsiasi wallbox |
| 🔌 **Wallbox opzionale** | Funziona anche senza wallbox MQTT (solo monitoraggio) |

---

## Compatibilità

**Veicoli**: qualsiasi EV con integrazione Home Assistant che esponga:
- un sensore SOC in percentuale
- un binary sensor per il cavo collegato

Esempi testati: Skoda Elroq, Volkswagen ID.4, Renault Zoe, Tesla (via unofficial integration), BMW iX

**Wallbox**: qualsiasi wallbox controllabile via MQTT. Esempi:
- Silla Prism (configurazione di default)
- Easee (con MQTT bridge)
- go-e Charger
- Alfen Eve
- Qualsiasi wallbox con firmware OpenWB o simili

**Fotovoltaico / misuratori**: qualsiasi inverter o smart meter che esponga la potenza in Watt come sensore HA (SolarEdge, Fronius, Huawei SUN2000, Shelly EM, Eastron SDM, ecc.)

**Fasce tariffarie** (opzionale):
- [PUN Sensor](https://github.com/virtualdj/pun_sensor) per le fasce F1/F2/F3 italiane
- [Tibber](https://www.home-assistant.io/integrations/tibber/) per prezzi spot dinamici
- Qualsiasi sensore che restituisca un valore stringa configurabile

---

## Installazione via HACS

1. In HACS → **Integrazioni** → menu ⋮ → **Repository personalizzati**
2. Inserire l'URL di questo repository, categoria **Integrazione**
3. Cliccare **Installa**
4. Riavviare Home Assistant
5. **Impostazioni → Dispositivi e servizi → Aggiungi integrazione** → cercare **EV Smart Charging**

---

## Configurazione guidata (3 step)

### Step 1 – Parametri generali
| Campo | Descrizione | Default |
|---|---|---|
| Potenza contratto | Limite contrattuale in Watt | 5700 W |
| Capacità batteria | kWh del pacco batteria del veicolo | 60 kWh |
| Fascia off-peak | Abilita la ricarica notturna/economica | ✅ |
| MQTT | Abilita il controllo del wallbox via MQTT | ✅ |

### Step 2 – Selezione entità
Seleziona le entità già presenti in HA per ciascun ruolo:

| Campo | Obbligatorio | Esempio |
|---|---|---|
| Sensore SOC veicolo | ✅ | `sensor.elroq_percentuale_batteria` |
| Binary sensor cavo | ✅ | `binary_sensor.ev_cavo_collegato` |
| Limite carica veicolo | ⬜ | `number.elroq_limite_di_carica` |
| Potenza rete | ✅ | `sensor.rete_power` |
| Produzione FV | ✅ | `sensor.fotovoltaico_power` |
| Potenza wallbox | ⬜ | `sensor.wallbox_potenza` |
| Tensione wallbox | ⬜ | `sensor.wallbox_tensione` |
| Fascia tariffaria | ⬜ | `sensor.pun_fascia_corrente` |
| Valore fascia off-peak | ⬜ | `F3` |

### Step 3 – Configurazione MQTT
| Campo | Default (Silla Prism) |
|---|---|
| Topic autorizza | `wallbox/command/authorize` |
| Topic revoca | `wallbox/command/revoke` |
| Topic imposta corrente | `wallbox/command/set_current_limit` |
| Topic imposta modalità | `wallbox/command/set_mode` |
| Payload solare | `1` |
| Payload normale | `2` |
| Payload pausa | `3` |

---

## Entità create dall'integrazione

### 📊 Sensori
| Entità | Descrizione |
|---|---|
| `sensor.charging_mode` | Modalità di ricarica corrente |
| `sensor.pv_surplus` | Eccedenza fotovoltaica disponibile (W) |
| `sensor.target_soc` | SOC target attivo |
| `sensor.charging_time_remaining` | Tempo rimanente stimato |
| `sensor.charge_end_time` | Timestamp fine ricarica stimata |
| `sensor.wallbox_current_target` | Corrente target teorica da FV (A) |

### 🔘 Switch
| Entità | Descrizione |
|---|---|
| `switch.master_stop` | Blocco globale di tutte le ricariche |
| `switch.force_charge` | Forza ricarica immediata |
| `switch.solar_controller_active` | Controllo solare attivo |
| `switch.night_off_peak_charging` | Abilita/disabilita ricarica off-peak |

### 🔢 Number (regolabili dalla UI o da automazioni)
| Entità | Range | Default |
|---|---|---|
| `number.user_soc_target` | 10–100% | 50% |
| `number.vehicle_soc_target` | 20–100% | 80% |
| `number.contract_power_limit` | 1500–22000 W | 5700 W |
| `number.allowed_grid_import` | 0–3000 W | 200 W |
| `number.night_charging_power_limit` | 1000–22000 W | 3000 W |

### 🔽 Select
| Entità | Opzioni |
|---|---|
| `select.charging_mode` | `idle`, `pv_surplus`, `night`, `force`, `master_stop` |

---

## Logica di decisione

```
Veicolo collegato?
        │ NO → IDLE
        ▼ SÌ
Master Stop attivo? → SÌ → Revoca autorizzazione → STOP
        │ NO
        ▼
Forza Ricarica attiva? → SÌ → Ricarica fino a Vehicle SOC Target (con load balancing)
        │ NO
        ▼
Fascia off-peak + Night Charging abilitato? → SÌ → Ricarica fino a User SOC Target
        │ NO
        ▼
Surplus FV ≥ corrente minima (6A)? → SÌ → Ricarica solare fino a Vehicle SOC Target
        │ NO
        ▼
IDLE (in attesa)
```

---

## Servizi disponibili

```yaml
# Autorizza ricarica manualmente
service: ev_smart_charging.authorize_charging

# Revoca autorizzazione manualmente
service: ev_smart_charging.revoke_charging

# Imposta limite di corrente manualmente
service: ev_smart_charging.set_charge_limit
data:
  current_a: 10   # valore tra 6 e 16
```

---

## Esempio di card Lovelace

```yaml
type: entities
title: EV Smart Charging
entities:
  - entity: select.charging_mode
  - entity: sensor.pv_surplus
  - entity: sensor.charging_time_remaining
  - entity: number.user_soc_target
  - entity: number.vehicle_soc_target
  - entity: switch.master_stop
  - entity: switch.force_charge
  - entity: switch.night_off_peak_charging
  - entity: number.allowed_grid_import
  - entity: number.contract_power_limit
```

---

## Struttura file

```
custom_components/ev_smart_charging/
├── __init__.py          # Setup integrazione e servizi HA
├── coordinator.py       # Logica smart charging + comandi MQTT
├── config_flow.py       # Configurazione guidata in 3 step
├── const.py             # Costanti e valori di default
├── sensor.py            # Sensori derivati
├── switch.py            # Switch (Master Stop, Forza, Solare, Notte)
├── number.py            # Parametri regolabili
├── select.py            # Selezione modalità di ricarica
├── manifest.json        # Metadati integrazione HACS
├── strings.json         # Testi UI italiano
└── translations/
    ├── it.json          # Traduzione italiana
    └── en.json          # Traduzione inglese
```

---

## Crediti

Basato sulla logica di automazione del progetto originale  
[ha-skoda-elroq-smart-charging](https://github.com/atcodrinky/ha-skoda-elroq-smart-charging) di [@atcodrinky](https://github.com/atcodrinky).

---

## Licenza

MIT License – vedi file [LICENSE](LICENSE)
