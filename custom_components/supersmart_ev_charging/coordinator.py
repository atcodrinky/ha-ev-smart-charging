"""Coordinator for SuperSmart EV Charging – logica replicata dalle automazioni YAML Silla Prism."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_CONTRACT_POWER_W,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_VEHICLE_SOC_ENTITY,
    CONF_VEHICLE_CHARGE_LIMIT_ENTITY,
    CONF_VEHICLE_CONNECTED_ENTITY,
    CONF_WALLBOX_STATE_ENTITY,
    CONF_WALLBOX_POWER_ENTITY,
    CONF_WALLBOX_VOLTAGE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_TOTAL_POWER_ENTITY,
    CONF_PV_POWER_ENTITY,
    CONF_TARIFF_ENTITY,
    CONF_TARIFF_OFFPEAK_VALUE,
    CONF_TARIFF_ENABLED,
    CONF_MQTT_ENABLED,
    CONF_MQTT_TOPIC_SET_CURRENT,
    CONF_MQTT_TOPIC_SET_MODE,
    CONF_MQTT_TOPIC_AUTHORIZE,
    CONF_MQTT_TOPIC_REVOKE,
    CONF_MQTT_PAYLOAD_MODE_SOLAR,
    CONF_MQTT_PAYLOAD_MODE_NORMAL,
    CONF_MQTT_PAYLOAD_MODE_PAUSE,
    CONF_BUTTON_AUTHORIZE_ENTITY,
    CONF_BUTTON_REVOKE_ENTITY,
    DEFAULT_CONTRACT_POWER_W,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_ALLOWED_IMPORT_W,
    DEFAULT_MIN_CHARGE_CURRENT_A,
    DEFAULT_MAX_CHARGE_CURRENT_A,
    DEFAULT_NIGHT_POWER_LIMIT_W,
    DEFAULT_USER_SOC_TARGET,
    DEFAULT_VEHICLE_SOC_TARGET,
    DEFAULT_SAFETY_MARGIN_W,
    DEFAULT_TARIFF_OFFPEAK_VALUE,
    DEFAULT_PV_START_CURRENT_A,
    DEFAULT_PV_STOP_CURRENT_A,
    DEFAULT_FV_HYGIENE_CURRENT_A,
    DEFAULT_PV_STOP_CONFIRM_CYCLES,
    DEFAULT_PV_START_CONFIRM_CYCLES,
    WB_STATE_CHARGING,
    WB_STATE_IDLE,
    WB_STATES_READY,
    CHARGING_MODE_IDLE,
    CHARGING_MODE_PV_SURPLUS,
    CHARGING_MODE_NIGHT,
    CHARGING_MODE_FORCE,
    CHARGING_MODE_MASTER_STOP,
)

_LOGGER = logging.getLogger(__name__)


class SuperSmartEvChargingCoordinator(DataUpdateCoordinator):
    """
    SuperSmart EV Charging coordinator.

    Replica ESATTA delle automazioni YAML per Silla Prism + Skoda Enyaq/Elroq:

    PRIORITÀ (dalla più alta):
      1. MASTER STOP  → revoca auth, blocca tutto
      2. FORCE CHARGE → mode 2 (normal) + modula entro contratto, stop a limite_auto
      3. SURPLUS FV   → mode 1 (solar), start ≥7A / stop <5.5A per 60s
      4. NOTTE F3     → mode 2 (normal), solo se FV < 7A e SOC < limite_utente
      5. STOP SOC     → stop se SOC ≥ limite_auto (assoluto) o ≥ limite_utente in F3 notte
      6. IGIENE FV    → spegne solar_controller_active se FV < 7A per 60s senza caricare

    AUTORIZZAZIONE: via button.press su entità HA (button.silla_prism_autorizza/revoca),
    NON tramite topic MQTT separati – timestamp tracciati in variabili interne.

    MQTT:
      - set_mode:          prism/1/command/set_mode  (payload "1","2","3")
      - set_current_limit: prism/1/command/set_current_limit  (payload "6.0", "7.5", …)

    POTENZA ISTANTANEA (sensor.potenza_istantanea):
      Il sensore è un template: rete_power + fotovoltaico_power
      Se CONF_TOTAL_POWER_ENTITY è configurato, viene letto direttamente.
      Altrimenti viene derivato internamente con la stessa formula:
        potenza_istantanea = grid_w + pv_w
      Entrambi i sensori (rete_power e fotovoltaico_power) devono quindi
      essere sempre configurati come CONF_GRID_POWER_ENTITY e CONF_PV_POWER_ENTITY.

    POTENZA CASA (per la modulazione contratto):
      potenza_casa = potenza_istantanea - wallbox_potenza
      margine_w    = max(limite_w - potenza_casa, 0)
      amp          = margine_w / v_grid
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.entry = entry
        d = entry.data

        # ── Feature flags
        self._mqtt_enabled: bool   = d.get(CONF_MQTT_ENABLED, True)
        self._tariff_enabled: bool = d.get(CONF_TARIFF_ENABLED, True)

        # ── MQTT topics & payloads (solo set_mode e set_current_limit per Silla Prism)
        self._topic_set_current = d.get(CONF_MQTT_TOPIC_SET_CURRENT, "prism/1/command/set_current_limit")
        self._topic_set_mode    = d.get(CONF_MQTT_TOPIC_SET_MODE,    "prism/1/command/set_mode")
        # Per wallbox generici senza button entities HA
        self._topic_authorize   = d.get(CONF_MQTT_TOPIC_AUTHORIZE,   "wallbox/command/authorize")
        self._topic_revoke      = d.get(CONF_MQTT_TOPIC_REVOKE,      "wallbox/command/revoke")
        self._payload_solar     = d.get(CONF_MQTT_PAYLOAD_MODE_SOLAR,   "1")
        self._payload_normal    = d.get(CONF_MQTT_PAYLOAD_MODE_NORMAL,  "2")
        self._payload_pause     = d.get(CONF_MQTT_PAYLOAD_MODE_PAUSE,   "3")

        # ── Entità button Silla Prism (autorizza/revoca via button.press)
        # Se configurate, vengono usate AL POSTO dei topic MQTT dedicati
        self._button_authorize_entity = d.get(CONF_BUTTON_AUTHORIZE_ENTITY, "")
        self._button_revoke_entity    = d.get(CONF_BUTTON_REVOKE_ENTITY,    "")

        # ── Entity IDs
        self._soc_entity             = d.get(CONF_VEHICLE_SOC_ENTITY,          "")
        self._charge_limit_entity    = d.get(CONF_VEHICLE_CHARGE_LIMIT_ENTITY, "")
        self._connected_entity       = d.get(CONF_VEHICLE_CONNECTED_ENTITY,    "")
        self._wallbox_state_entity   = d.get(CONF_WALLBOX_STATE_ENTITY,        "")  # sensor.silla_prism_stato_wallbox
        self._wallbox_power_entity   = d.get(CONF_WALLBOX_POWER_ENTITY,        "")
        self._wallbox_voltage_entity = d.get(CONF_WALLBOX_VOLTAGE_ENTITY,      "")
        self._grid_entity            = d.get(CONF_GRID_POWER_ENTITY,           "")  # sensor.rete_power
        self._pv_entity              = d.get(CONF_PV_POWER_ENTITY,             "")  # sensor.fotovoltaico_power
        # sensor.potenza_istantanea = rete_power + fotovoltaico_power (template HA).
        # Se l'entità è configurata viene letta direttamente; altrimenti viene
        # calcolata con la stessa formula per non richiedere una config entry aggiuntiva.
        self._total_power_entity     = d.get(CONF_TOTAL_POWER_ENTITY,          "")  # opzionale
        self._tariff_entity          = d.get(CONF_TARIFF_ENTITY,               "")  # sensor.pun_fascia_corrente
        self._tariff_offpeak         = d.get(CONF_TARIFF_OFFPEAK_VALUE, DEFAULT_TARIFF_OFFPEAK_VALUE)

        # ── Power / capacity
        self._contract_power_w: float     = d.get(CONF_CONTRACT_POWER_W,     DEFAULT_CONTRACT_POWER_W)
        self._battery_capacity_kwh: float = d.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)

        # ── Stato controllabile (esposto come entità HA)
        self.master_stop: bool             = False   # input_boolean.ev_master_stop
        self.force_charge: bool            = False   # input_boolean.forza_ricarica
        self.solar_controller_active: bool = False   # input_boolean.ev_solar_controller_active
        self.night_charging_enabled: bool  = True    # abilita logica notturna F3

        # Limiti SOC esposti come number entities
        self.user_soc_target: float    = float(d.get("initial_user_soc_target",    DEFAULT_USER_SOC_TARGET))
        self.vehicle_soc_target: float = float(d.get("initial_vehicle_soc_target", DEFAULT_VEHICLE_SOC_TARGET))

        # Limiti potenza esposti come number entities
        self.allowed_import_w: float    = DEFAULT_ALLOWED_IMPORT_W   # input_number.limite_import_permesso
        self.night_power_limit_w: float = DEFAULT_NIGHT_POWER_LIMIT_W  # input_number.ev_limite_notturno_w

        # ── Contatori isteresi FV (replicano i trigger template con for:)
        # FV stop: trigger YAML for:60s → 2 cicli da 30 s
        self._pv_below_stop_cycles: int  = 0
        # FV start: trigger YAML for:30s → 1 ciclo da 30 s
        self._pv_above_start_cycles: int = 0
        # Igiene controller: for:60s → 2 cicli da 30 s
        self._fv_hygiene_cycles: int     = 0

        # ── Stato derivato / calcolato
        self.charging_mode: str              = CHARGING_MODE_IDLE
        self.amp_fv_surplus: float           = 0.0   # corrente FV calcolata (A)
        self.wallbox_current_target_a: float = 0.0
        self.last_limit_sent_a: float        = 0.0
        self.last_authorization_ts: datetime | None = None
        self.last_revoke_ts: datetime | None        = None

    # ── DataUpdateCoordinator ──────────────────────────────────────────────────
    async def _async_update_data(self) -> dict[str, Any]:
        """Legge i sensori e calcola tutto lo stato derivato."""
        data: dict[str, Any] = {}
        try:
            # Wallbox
            wb_state   = self._get_state(self._wallbox_state_entity, default=WB_STATE_IDLE)
            wb_w       = abs(self._get_float(self._wallbox_power_entity))
            v_raw      = self._get_float(self._wallbox_voltage_entity, default=0.0)
            voltage    = float(max(min(v_raw if v_raw > 0 else 230.0, 260.0), 180.0))

            data["wallbox_state"]   = wb_state
            data["wallbox_power_w"] = wb_w
            data["wallbox_voltage_v"] = voltage

            # Rete (+ = import dalla rete, - = export verso rete)
            grid_w = self._get_float(self._grid_entity)
            data["grid_power_w"] = grid_w

            # FV produzione
            pv_w = self._get_float(self._pv_entity)
            data["pv_power_w"] = pv_w

            # Potenza istantanea totale (casa + wallbox).
            # Formula template HA: rete_power + fotovoltaico_power
            # Se il sensore template è configurato viene letto direttamente,
            # altrimenti viene derivato con la stessa identica formula.
            if self._total_power_entity:
                total_w = self._get_float(self._total_power_entity)
            else:
                total_w = grid_w + pv_w   # replica: rete_power + fotovoltaico_power
            house_w = max(0.0, total_w - wb_w)
            data["total_power_w"] = total_w
            data["house_power_w"] = house_w

            # Corrente FV disponibile (formula YAML):
            # amp_fv = (-rete_power + offset_w) / v_grid
            # Positivo = surplus FV > import permesso  →  possiamo caricare
            amp_fv = (-grid_w + self.allowed_import_w) / voltage
            self.amp_fv_surplus = amp_fv
            data["amp_fv_surplus"] = amp_fv

            # Veicolo
            soc_raw   = self._get_state(self._soc_entity)
            soc_valid = soc_raw not in ("unknown", "unavailable", "none", "", None)
            soc       = float(soc_raw) if soc_valid else 0.0
            data["vehicle_soc"]       = soc
            data["vehicle_soc_valid"] = soc_valid

            # Connessione veicolo:
            # Se configurata un'entità dedicata (binary_sensor o sensor), la usa.
            # Altrimenti la deriva dallo stato wallbox: qualsiasi stato != idle = connesso.
            # (Replica logica YAML: il veicolo è connesso se la wallbox non è idle)
            if self._connected_entity:
                vehicle_connected = self._get_bool(self._connected_entity, default=False)
            else:
                vehicle_connected = wb_state != WB_STATE_IDLE
            data["vehicle_connected"] = vehicle_connected

            # Fascia tariffaria
            if self._tariff_enabled and self._tariff_entity:
                tariff = self._get_state(self._tariff_entity, default="")
                is_offpeak = tariff == self._tariff_offpeak
            else:
                tariff, is_offpeak = "", False
            data["tariff_value"] = tariff
            data["is_offpeak"]   = is_offpeak

            # Sole sotto orizzonte (sun.sun)
            sun_state         = self._get_state("sun.sun", default="above_horizon")
            sun_below_horizon = sun_state == "below_horizon"
            data["sun_below_horizon"] = sun_below_horizon

            # Target SOC attivo
            target_soc = self.vehicle_soc_target if self.force_charge else self.user_soc_target
            data["target_soc_active"] = target_soc

            # Corrente target wallbox (calcolata da amp_fv per display)
            self.wallbox_current_target_a = max(0.0, amp_fv)
            data["wallbox_current_target_a"] = self.wallbox_current_target_a

            # Stima tempo rimanente
            remaining_kwh = max(0.0, (target_soc - soc) / 100.0 * self._battery_capacity_kwh)
            wallbox_kw    = wb_w / 1000.0
            remaining_min = (remaining_kwh / wallbox_kw * 60) if wallbox_kw > 0.1 else None
            data["remaining_minutes"] = remaining_min
            data["charge_end_time"]   = (
                datetime.now() + timedelta(minutes=remaining_min)
                if remaining_min is not None else None
            )

            # Diagnostica contatori
            data["pv_below_stop_cycles"]  = self._pv_below_stop_cycles
            data["pv_above_start_cycles"] = self._pv_above_start_cycles
            data["fv_hygiene_cycles"]     = self._fv_hygiene_cycles

            data["charging_mode"]           = self.charging_mode
            data["master_stop"]             = self.master_stop
            data["force_charge"]            = self.force_charge
            data["solar_controller_active"] = self.solar_controller_active

        except Exception as err:
            raise UpdateFailed(f"SuperSmart EV Charging – errore lettura dati: {err}") from err

        return data

    # ── Loop principale di decisione ───────────────────────────────────────────
    async def async_update_charging_logic(self, _now: datetime | None = None) -> None:
        """
        Valuta le condizioni di ricarica ogni 30 s e invia comandi MQTT.

        Replica la logica delle automazioni YAML nell'ordine esatto di priorità:
          1. MASTER STOP
          2. FORZA RICARICA (Gestione Carichi)
          3. SOC ≥ limite_auto → STOP assoluto (Gestione SOC)
          4. SURPLUS FV ≥ 7A  → mode solar (Surplus FV)
          5. F3 + notte + SOC < limite_utente → mode normal (Gestione Fascia)
          6. F3 notte + SOC ≥ limite_utente + FV assente → STOP (Gestione SOC F3)
          7. IGIENE controller FV
        """
        await self.async_refresh()
        data = self.data
        if not data:
            return

        wb_state          = data.get("wallbox_state", WB_STATE_IDLE)
        vehicle_connected = data.get("vehicle_connected", False)
        vehicle_soc       = data.get("vehicle_soc", 0.0)
        soc_valid         = data.get("vehicle_soc_valid", False)
        is_offpeak        = data.get("is_offpeak", False)           # fascia F3
        sun_below         = data.get("sun_below_horizon", False)
        amp_fv            = data.get("amp_fv_surplus", 0.0)
        voltage           = data.get("wallbox_voltage_v", 230.0)

        # ── 0. Veicolo scollegato → reset tutto ───────────────────────────────
        if not vehicle_connected:
            if self.charging_mode != CHARGING_MODE_IDLE:
                _LOGGER.info("[SuperSmart] Veicolo scollegato (wallbox idle) – reset")
                self.solar_controller_active = False
                self.force_charge            = False
                self.charging_mode           = CHARGING_MODE_IDLE
                self._reset_pv_counters()
                self._fv_hygiene_cycles = 0
                self.async_update_listeners()
            return

        # SOC non valido → non fare nulla (come "{{ not soc_valid }} → sequence: []")
        if not soc_valid:
            _LOGGER.debug("[SuperSmart] SOC non valido – skip ciclo")
            return

        # ── 1. MASTER STOP – priorità assoluta ────────────────────────────────
        if self.master_stop:
            if self.charging_mode != CHARGING_MODE_MASTER_STOP:
                _LOGGER.warning("[SuperSmart] Master Stop attivo – revoca autorizzazione")
                self.solar_controller_active = False
                self.force_charge            = False
                await self._set_mode(self._payload_pause)
                await self._delay(2)
                await self._revoke()
                self.charging_mode = CHARGING_MODE_MASTER_STOP
                self._reset_pv_counters()
                self.async_update_listeners()
            return

        # ── 2. GESTIONE SOC – stop assoluto se SOC ≥ limite_auto ──────────────
        # Replica: "soc >= limite_auto and stato != 'pause'" → revoca + FV OFF + FORZA OFF
        if vehicle_soc >= self.vehicle_soc_target and wb_state != "pause":
            if self.charging_mode != CHARGING_MODE_IDLE:
                _LOGGER.info(
                    "[SuperSmart] SOC %.0f%% ≥ limite_auto %.0f%% – stop assoluto",
                    vehicle_soc, self.vehicle_soc_target,
                )
                if self.force_charge:
                    self.force_charge = False
                await self._set_mode(self._payload_pause)
                await self._delay(1)
                await self._revoke()
                self.solar_controller_active = False
                self.charging_mode           = CHARGING_MODE_IDLE
                self._reset_pv_counters()
                self._fv_hygiene_cycles = 0
                self.async_update_listeners()
            return

        # ── 3. FORZA RICARICA (Gestione Carichi - FORZA ON) ───────────────────
        if self.force_charge:
            self._reset_pv_counters()
            self._fv_hygiene_cycles = 0

            amp_contratto = self._contratto_balanced_current(data, use_night_limit=False)

            if self.charging_mode != CHARGING_MODE_FORCE:
                # Avvio FORZA: waiting/pause + SOC < limite_auto + amp ≥ 7A
                if wb_state in WB_STATES_READY and amp_contratto >= 7:
                    _LOGGER.info("[SuperSmart] FORZA attivata – avvio ricarica mode 2")
                    await self._send_limit(6.0)
                    await self._set_mode(self._payload_normal)
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_FORCE
                    self.async_update_listeners()
            else:
                # Modulazione FORZA in corso: charging + diff ≥ 0.5A
                if wb_state == WB_STATE_CHARGING and amp_contratto >= 6:
                    await self._send_limit_if_changed(amp_contratto)
                # Stop soft FORZA: amp < 5.5A per 60 s (gestito da contatore esterno)
                # → viene gestito nel blocco igiene/stop-soft sotto
            self.async_update_listeners()
            return

        # ── 4. SURPLUS FV – lavora in tutte le fasce ──────────────────────────
        # skip_in_f3: se F3 e SOC < limite_utente e amp_fv < 7 → lascia spazio alla logica notturna
        skip_in_f3 = is_offpeak and (vehicle_soc < self.user_soc_target) and (amp_fv < DEFAULT_PV_START_CURRENT_A)

        if not skip_in_f3:
            # ── 4a. Stop FV: amp_fv < 5.5 per 60 s mentre carica ─────────────
            if self.solar_controller_active and wb_state == WB_STATE_CHARGING and amp_fv < DEFAULT_PV_STOP_CURRENT_A:
                self._pv_below_stop_cycles += 1
                self._pv_above_start_cycles = 0
                _LOGGER.debug(
                    "[SuperSmart] FV sotto soglia %.1fA < %.1fA – ciclo %d/%d",
                    amp_fv, DEFAULT_PV_STOP_CURRENT_A,
                    self._pv_below_stop_cycles, DEFAULT_PV_STOP_CONFIRM_CYCLES,
                )
                if self._pv_below_stop_cycles >= DEFAULT_PV_STOP_CONFIRM_CYCLES:
                    _LOGGER.info("[SuperSmart] FV calato – stop carica + revoca auth")
                    await self._set_mode(self._payload_pause)
                    await self._revoke()
                    self.solar_controller_active = False
                    self.charging_mode           = CHARGING_MODE_IDLE
                    self._reset_pv_counters()
                    self._fv_hygiene_cycles = 0
                    self.async_update_listeners()
                return

            # ── 4b. Avvio FV: amp_fv ≥ 7 per 30 s + waiting/pause ────────────
            if amp_fv >= DEFAULT_PV_START_CURRENT_A and wb_state in WB_STATES_READY:
                self._pv_above_start_cycles += 1
                self._pv_below_stop_cycles   = 0
                self._fv_hygiene_cycles      = 0
                _LOGGER.debug(
                    "[SuperSmart] FV surplus %.1fA ≥ 7A – ciclo %d/%d",
                    amp_fv, self._pv_above_start_cycles, DEFAULT_PV_START_CONFIRM_CYCLES,
                )
                if self._pv_above_start_cycles >= DEFAULT_PV_START_CONFIRM_CYCLES:
                    _LOGGER.info("[SuperSmart] FV stabile – avvio ricarica solare mode 1")
                    self.solar_controller_active = True
                    await self._set_mode(self._payload_solar)
                    await self._send_limit(6.0)
                    await self._delay(2)
                    await self._authorize()
                    self.charging_mode = CHARGING_MODE_PV_SURPLUS
                    self._reset_pv_counters()
                    self._fv_hygiene_cycles = 0
                    self.async_update_listeners()
                return

            # ── 4c. Modulazione FV: charging + solar_active + diff ≥ 0.5 ─────
            if self.solar_controller_active and wb_state == WB_STATE_CHARGING:
                self._pv_below_stop_cycles   = 0
                self._pv_above_start_cycles  = 0
                self._fv_hygiene_cycles      = 0
                wb_w = data.get("wallbox_power_w", 0.0)
                amp_now     = wb_w / voltage
                amp_new_raw = amp_now + amp_fv - (wb_w / voltage)  # = amp_fv (semplificato)
                # Formula YAML: amp_new_raw = amp_now + ((-rete + offset) / v)
                # che è equivalente ad amp_fv quando amp_fv = (-rete + offset) / v
                amp_limit   = min(max(amp_fv, 0.0), DEFAULT_MAX_CHARGE_CURRENT_A)
                amp_limit   = round(amp_limit, 1)
                if wb_w > 500 and amp_limit >= 6:
                    await self._send_limit_if_changed(amp_limit)
                self.async_update_listeners()
                return

        # ── 5. GESTIONE FASCIA F3 NOTTE – avvio notturno ──────────────────────
        # Condizioni YAML: F3 + sole sotto orizzonte + solar_active=OFF + SOC < limite_utente
        #                  + amp (contratto) ≥ 7A + amp_fv < 6A + stato in [waiting, pause]
        if (self._tariff_enabled
                and is_offpeak
                and sun_below
                and not self.solar_controller_active
                and vehicle_soc < self.user_soc_target
                and wb_state in WB_STATES_READY):

            amp_contratto = self._contratto_balanced_current(data, use_night_limit=True)
            if amp_contratto >= 7 and amp_fv < 6:
                _LOGGER.info(
                    "[SuperSmart] F3 notte – avvio ricarica notturna mode 2 (SOC %.0f%% < %.0f%%)",
                    vehicle_soc, self.user_soc_target,
                )
                self.solar_controller_active = False
                await self._send_limit(6.0)
                await self._set_mode(self._payload_normal)
                await self._delay(1)
                await self._authorize()
                self.charging_mode = CHARGING_MODE_NIGHT
                self._reset_pv_counters()
                self._fv_hygiene_cycles = 0
                self.async_update_listeners()
                return

        # ── 6. GESTIONE CARICHI NOTTURNA – modulazione in F3 ──────────────────
        # Condizioni: F3 + notte + solar_active=OFF + charging + SOC < limite_utente
        if (self._tariff_enabled
                and is_offpeak
                and sun_below
                and not self.solar_controller_active
                and wb_state == WB_STATE_CHARGING
                and vehicle_soc < self.user_soc_target):

            amp_contratto = self._contratto_balanced_current(data, use_night_limit=True)

            if amp_contratto >= 6:
                await self._send_limit_if_changed(amp_contratto)
                self.async_update_listeners()
                return
            else:
                # amp < 5.5 → stop soft (equivale al trigger low_margin_60s)
                self._pv_below_stop_cycles += 1
                if self._pv_below_stop_cycles >= DEFAULT_PV_STOP_CONFIRM_CYCLES:
                    _LOGGER.info("[SuperSmart] F3: margine potenza esaurito – stop ricarica")
                    await self._send_limit(6.0)
                    await self._delay_seconds(20)
                    # Rileggi stato dopo delay
                    wb_state_now = self._get_state(self._wallbox_state_entity, WB_STATE_IDLE)
                    v_now        = self._safe_voltage()
                    if wb_state_now == WB_STATE_CHARGING:
                        amp_contratto_now = self._contratto_balanced_current_now(v_now, use_night_limit=True)
                        if amp_contratto_now < DEFAULT_PV_STOP_CURRENT_A:
                            await self._set_mode(self._payload_pause)
                            await self._revoke()
                            self.charging_mode  = CHARGING_MODE_IDLE
                            self._reset_pv_counters()
                            self._fv_hygiene_cycles = 0
                self.async_update_listeners()
                return

        # ── 7. GESTIONE SOC F3 – stop se SOC ≥ limite_utente in notte senza FV ─
        if (self._tariff_enabled
                and is_offpeak
                and sun_below
                and not self.force_charge
                and vehicle_soc >= self.user_soc_target
                and amp_fv < 6
                and wb_state == WB_STATE_CHARGING):
            _LOGGER.info(
                "[SuperSmart] F3: SOC %.0f%% ≥ limite_utente %.0f%% – stop ricarica notturna",
                vehicle_soc, self.user_soc_target,
            )
            await self._set_mode(self._payload_pause)
            await self._delay(1)
            await self._revoke()
            self.solar_controller_active = False
            self.charging_mode           = CHARGING_MODE_IDLE
            self._reset_pv_counters()
            self._fv_hygiene_cycles      = 0
            self.async_update_listeners()
            return

        # ── 8. USCITA INTELLIGENTE DA FORZA (chiamata quando force_charge → OFF)
        # Gestita in ForceChargeSwitch.async_turn_off() → _handle_force_exit()

        # ── 9. IGIENE CONTROLLER FV – spegne solar_active se inutile ──────────
        # Condizioni YAML: (not forza) and solar_active and stato != 'charging' and amp_fv < 7
        if (not self.force_charge
                and self.solar_controller_active
                and wb_state != WB_STATE_CHARGING
                and amp_fv < DEFAULT_FV_HYGIENE_CURRENT_A):
            self._fv_hygiene_cycles += 1
            _LOGGER.debug(
                "[SuperSmart] Igiene FV: controller attivo inutilmente – ciclo %d/2",
                self._fv_hygiene_cycles,
            )
            if self._fv_hygiene_cycles >= 2:  # for:60s → 2 cicli da 30 s
                _LOGGER.info("[SuperSmart] Igiene FV – spegne solar_controller_active")
                self.solar_controller_active = False
                self._fv_hygiene_cycles      = 0
                if self.charging_mode == CHARGING_MODE_PV_SURPLUS:
                    self.charging_mode = CHARGING_MODE_IDLE
                self.async_update_listeners()
        else:
            self._fv_hygiene_cycles = 0

        self.async_update_listeners()

    # ── Uscita intelligente da FORZA ──────────────────────────────────────────
    async def _handle_force_exit(self) -> None:
        """
        Chiamata quando FORZA viene disattivata.
        Replica EV - Uscita intelligente da FORZA:
          - continua_fv (amp_fv ≥ 7) → attiva solar_controller
          - continua_notturna (F3 + SOC < limite_utente) → lascia andare Gestione Fascia
          - altrimenti → stop carica
        """
        wb_state = self._get_state(self._wallbox_state_entity, WB_STATE_IDLE)
        if wb_state != WB_STATE_CHARGING:
            return
        if self.master_stop:
            return

        soc_raw  = self._get_state(self._soc_entity)
        soc_valid = soc_raw not in ("unknown", "unavailable", "none", "", None)
        if not soc_valid:
            return

        soc            = float(soc_raw)
        v_raw          = self._get_float(self._wallbox_voltage_entity, 0.0)
        voltage        = float(max(min(v_raw if v_raw > 0 else 230.0, 260.0), 180.0))
        grid_w         = self._get_float(self._grid_entity)
        amp_fv         = (-grid_w + self.allowed_import_w) / voltage
        tariff        = self._get_state(self._tariff_entity, "") if self._tariff_enabled else ""
        is_offpeak    = tariff == self._tariff_offpeak
        sun_state     = self._get_state("sun.sun", "above_horizon")
        sun_below     = sun_state == "below_horizon"

        continua_notturna = is_offpeak and (soc < self.user_soc_target)
        continua_fv       = amp_fv >= DEFAULT_PV_START_CURRENT_A

        if continua_fv:
            _LOGGER.info("[SuperSmart] Uscita FORZA: surplus FV disponibile – attiva solar controller")
            self.solar_controller_active = True
            self.charging_mode           = CHARGING_MODE_PV_SURPLUS
        elif continua_notturna:
            _LOGGER.info("[SuperSmart] Uscita FORZA: F3 notturna – continua ricarica grid")
            self.charging_mode = CHARGING_MODE_NIGHT
        else:
            _LOGGER.info("[SuperSmart] Uscita FORZA: nessuna condizione – stop ricarica")
            await self._set_mode(self._payload_pause)
            await self._revoke()
            self.solar_controller_active = False
            self.charging_mode           = CHARGING_MODE_IDLE

        self._reset_pv_counters()
        self.async_update_listeners()

    # ── Helpers calcolo corrente ───────────────────────────────────────────────
    def _contratto_balanced_current(self, data: dict[str, Any], use_night_limit: bool) -> float:
        """
        Corrente massima che non supera il limite di potenza.
        Replica la formula YAML di Gestione Fascia e Gestione Carichi:
          margine_w = max(limite_w - potenza_casa, 0)
          amp = margine_w / v_grid
        Potenza casa = potenza_tot - potenza_wallbox (NON usa grid+pv come il coordinatore originale).
        """
        voltage     = data.get("wallbox_voltage_v", 230.0)
        house_w     = data.get("house_power_w", 0.0)
        # limite_w: notturno (ev_limite_notturno_w) o contratto completo
        if use_night_limit:
            limite_w = self.night_power_limit_w
        else:
            limite_w = self._contract_power_w
        margine_w = max(limite_w - house_w, 0.0)
        amp = margine_w / voltage if voltage > 0 else 0.0
        return round(min(amp, DEFAULT_MAX_CHARGE_CURRENT_A), 1)

    def _contratto_balanced_current_now(self, voltage: float, use_night_limit: bool) -> float:
        """
        Versione sincrona per ricalcolo intra-ciclo (dopo delay in stop-soft).
        Usa la stessa formula di _contratto_balanced_current:
          potenza_istantanea = total_power_entity  oppure  grid_w + pv_w
          potenza_casa       = potenza_istantanea - wallbox_potenza
        """
        wallbox_w = abs(self._get_float(self._wallbox_power_entity))
        if self._total_power_entity:
            total_w = self._get_float(self._total_power_entity)
        else:
            grid_w = self._get_float(self._grid_entity)
            pv_w   = self._get_float(self._pv_entity)
            total_w = grid_w + pv_w
        house_w   = max(0.0, total_w - wallbox_w)
        limite_w  = self.night_power_limit_w if use_night_limit else self._contract_power_w
        margine_w = max(limite_w - house_w, 0.0)
        amp = margine_w / voltage if voltage > 0 else 0.0
        return round(min(amp, DEFAULT_MAX_CHARGE_CURRENT_A), 1)

    def _safe_voltage(self) -> float:
        v_raw = self._get_float(self._wallbox_voltage_entity, 0.0)
        return float(max(min(v_raw if v_raw > 0 else 230.0, 260.0), 180.0))

    def _reset_pv_counters(self) -> None:
        self._pv_below_stop_cycles  = 0
        self._pv_above_start_cycles = 0

    # ── Comandi MQTT ──────────────────────────────────────────────────────────
    async def _authorize(self) -> None:
        """
        Autorizza ricarica.
        Se configurata un'entità button HA (Silla Prism), usa button.press.
        Altrimenti pubblica su topic MQTT generico.
        """
        self.last_authorization_ts = datetime.now()
        if self._button_authorize_entity:
            await self._press_button(self._button_authorize_entity)
            _LOGGER.debug("[SuperSmart] Authorize → button.press %s", self._button_authorize_entity)
        elif self._mqtt_enabled:
            await mqtt.async_publish(self.hass, self._topic_authorize, "1", qos=1)
            _LOGGER.debug("[SuperSmart] Authorize → MQTT %s", self._topic_authorize)

    async def _revoke(self) -> None:
        """
        Revoca autorizzazione.
        Se configurata un'entità button HA (Silla Prism), usa button.press.
        Altrimenti pubblica su topic MQTT generico.
        """
        self.last_revoke_ts = datetime.now()
        if self._button_revoke_entity:
            await self._press_button(self._button_revoke_entity)
            _LOGGER.debug("[SuperSmart] Revoke → button.press %s", self._button_revoke_entity)
        elif self._mqtt_enabled:
            await mqtt.async_publish(self.hass, self._topic_revoke, "1", qos=1)
            _LOGGER.debug("[SuperSmart] Revoke → MQTT %s", self._topic_revoke)

    async def _press_button(self, entity_id: str) -> None:
        """Preme un'entità button in HA (replica action: button.press)."""
        await self.hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )

    async def _send_limit(self, current_a: float) -> None:
        """Invia limite corrente via MQTT. Salva last_limit_sent_a."""
        # La YAML usa float con 1 decimale: "6.0", "7.5" ecc. (NON int clamped)
        clamped = round(
            min(DEFAULT_MAX_CHARGE_CURRENT_A, max(DEFAULT_MIN_CHARGE_CURRENT_A, current_a)),
            1,
        )
        if self._mqtt_enabled:
            await mqtt.async_publish(
                self.hass, self._topic_set_current, f"{clamped:.1f}", qos=1
            )
        self.last_limit_sent_a = clamped
        _LOGGER.debug("[SuperSmart] Corrente %.1fA → %s", clamped, self._topic_set_current)

    async def _send_limit_if_changed(self, current_a: float) -> None:
        """Invia limite solo se cambiato ≥ 0.5A (anti-spam YAML: diff ≥ 0.5)."""
        if abs(current_a - self.last_limit_sent_a) >= 0.5:
            await self._send_limit(current_a)

    async def _set_mode(self, payload: str) -> None:
        if not self._mqtt_enabled or not self._topic_set_mode:
            return
        await mqtt.async_publish(self.hass, self._topic_set_mode, payload, qos=1)
        _LOGGER.debug("[SuperSmart] Mode '%s' → %s", payload, self._topic_set_mode)

    async def _delay(self, seconds: int) -> None:
        """Delay asincrono (replica i 'delay:' nelle sequenze YAML)."""
        import asyncio
        await asyncio.sleep(seconds)

    async def _delay_seconds(self, seconds: int) -> None:
        import asyncio
        await asyncio.sleep(seconds)

    # ── Wrapper pubblici (usati da services e switch) ─────────────────────────
    async def authorize_charging(self) -> None:
        await self._authorize()

    async def revoke_charging(self) -> None:
        await self._revoke()

    async def set_current_limit(self, current_a: float) -> None:
        await self._send_limit(current_a)

    # ── Helpers stato ──────────────────────────────────────────────────────────
    def _get_state(self, entity_id: str, default: str = "unknown") -> str:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        return state.state if state else default

    def _get_float(self, entity_id: str, default: float = 0.0) -> float:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable", ""):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _get_bool(self, entity_id: str, default: bool = False) -> bool:
        if not entity_id:
            return default
        state = self.hass.states.get(entity_id)
        return state.state in ("on", "true", "connected", "yes", "1") if state else default

    @staticmethod
    def _w_to_a(watts: float, voltage: float) -> float:
        return (watts / voltage) if voltage > 0 else 0.0
