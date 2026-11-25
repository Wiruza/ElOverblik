# Eloverblik (Power Monitor) for Home Assistant

Brings your hourly electricity readings from [Eloverblik.dk](https://eloverblik.dk) straight into Home
Assistant. No computer has to stay on: Home Assistant fetches the readings itself every two hours.

It's the Home Assistant version of the Power Monitor desktop app, built as a fork of
[homeassistant-eloverblik](https://github.com/JonasPed/homeassistant-eloverblik) by JonasPed (Apache-2.0;
see `NOTICE` for what changed).

## What you get

For each meter you add, there's a device named after its address with these sensors:

| Entity | What it is |
|---|---|
| `sensor.eloverblik_<last 6 digits>_latest_day` | kWh on the latest full day (attribute `hourly_kwh` has its 24 hours) |
| `sensor.eloverblik_<…>_last_7_days` | kWh for the 7 days up to the latest full day |
| `sensor.eloverblik_<…>_this_month` | kWh this month so far |
| `sensor.eloverblik_<…>_last_month` | kWh for the whole previous month |
| `sensor.eloverblik_<…>_this_year` | kWh this year so far |
| `sensor.eloverblik_<…>_latest_hour` | kWh in the newest hour |
| `sensor.eloverblik_<…>_data_until` | When the newest reading ends |

The hourly history also goes into the statistic **"Electricity used – <your address>"**
(`eloverblik:consumption_<meter number>`), which the Energy dashboard uses. Each hour is placed at the time it
was used, even though Eloverblik delivers readings a day or two late.

The entity ids and the statistic are the same as the Power Monitor desktop app uses, so dashboards and the
Energy dashboard you set up with the app keep working.

## Install

### With HACS (recommended)

1. Put this folder in a GitHub repository of your own (for example your fork of homeassistant-eloverblik:
   replace its contents with the contents of this folder).
2. In Home Assistant open HACS → ⋮ (top right) → **Custom repositories**, paste your repository's address,
   choose type **Integration** and press **Add**.
3. Find **Eloverblik (Power Monitor)** in HACS, press **Download**, then restart Home Assistant.

### By hand

1. Copy the folder `custom_components/eloverblik` into the `custom_components` folder in your Home Assistant
   configuration folder (create `custom_components` if it isn't there). The *Samba share* or
   *Studio Code Server* add-on makes this easy.
2. Restart Home Assistant: Settings → ⋮ → Restart Home Assistant.

If you had the original Eloverblik integration installed, this replaces it (same name). Your existing
entries keep working.

## Set up

1. Settings → Devices & services → **Add integration** → search for **Eloverblik**.
2. Paste your Eloverblik key (eloverblik.dk → log in with MitID → menu with your name → *Datadeling* →
   *Opret token*).
3. Pick your meter and how much history to download (1 year is a good start). Press Submit.
4. Energy dashboard: Settings → Dashboards → Energy → Electricity grid → **Add consumption** → choose
   "Electricity used – <your address>".

When the key expires (after up to a year), Home Assistant shows a **Reconfigure** notice: paste a new key there.

## Moving over from the Power Monitor desktop app

1. In the desktop app, untick *Update automatically* and turn off the *Daily update* on its Home Assistant page,
   so the two don't both send.
2. Install this integration and restart Home Assistant (the restart also clears the sensors the app sent).
3. Add the integration as above. It continues the same hourly history, so the Energy dashboard needs no changes.

## Good to know

* Eloverblik allows only two logins a minute and is sometimes slow or busy; the integration then simply tries
  again at the next update and keeps the readings it already has.
* Readings are kept in Home Assistant's storage, so a restart doesn't download everything again.
* Removing the integration leaves the hourly history in place. Delete it under Developer tools → Statistics if
  you don't want it.
