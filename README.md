# wifispot

A Linux Wi-Fi mapping and decision assistant. It shows the access points around you, monitors
your connection live, and walks you through a short survey of candidate spots to tell you
whether moving actually helps. It works on a laptop alone. An Android phone can optionally
be paired by QR code to add location to the measurements.

The phone improves **where** a measurement was taken, never the Wi-Fi measurement itself:
all Wi-Fi data comes from the laptop. Phone positioning is good outdoors and often too coarse
indoors; wifispot checks the reported accuracy and falls back gracefully.

## Requirements

- Linux with NetworkManager (`nmcli`), `ip`, and `ping` (iputils)
- Optional: `iwconfig` (link rate, transmit power, retries) and `openssl` (needed for phone pairing)
- Python 3.10+ and [uv](https://docs.astral.sh/uv/)

## Run

```sh
uv run wifispot            # opens http://127.0.0.1:8765 in your browser
uv run wifispot --help     # --iface, --port, --phone-port, --window, --internet-host, --data-dir
uv run pytest              # tests
```

## Using it

1. **Now**: live signal, router and internet latency, packet loss, link rate, traffic and radio details.
2. **Networks**: every visible access point drawn across its channel width per band, with which
   ones share or overlap your channel.
3. **Find a better spot**:
   1. Press *Measure here* where you are. This is the baseline.
   2. Carry the laptop to 2–4 candidate spots. At each, set it down and measure (12 s, keep it still).
   3. Read the recommendation, then press *I'm here now* at the chosen spot. The Now tab then
      compares live data collected since you arrived with what the survey measured there.

**Pairing a phone (optional).** Click *Pair phone*, scan the QR code with a phone on the same
Wi-Fi, accept the certificate warning (the laptop signs its own certificate; browsers only allow
location on HTTPS pages), and tap *Share location*. Carry the phone with the laptop. The phone can
also start measurements and shows direction to the recommended spot when accuracy allows. If the
page never loads on the phone, the network is probably isolating clients (common on campus and
guest Wi-Fi); everything else still works.

## How decisions are made

Each measurement window records signal (1 Hz), pings to the router (4/s), and link rate, then
runs a scan so off-channel scanning cannot disturb the latency samples. A spot's score (0–100)
weights signal 30 %, packet loss 25 %, latency 20 %, link rate 15 %, and latency spikes 10 %.

wifispot only says **Move** when the best spot beats the starting spot by at least 5 points
(about 5 dB of signal or 1 % loss) *and* a block bootstrap of the window's samples puts that
ahead with ≥ 90 % probability. Otherwise it says **Stay**. The size threshold exists because
the bootstrap only sees noise inside a 12-second window, and real conditions drift more than that.
Confidence drops for stale measurements (> 15 min), movement detected by the phone, few samples,
or only one alternative spot measured. Ties between good spots are reported as ties.

## What the maps claim

- **Spot graph** (always available): spots in visit order. You can drag them onto a blank canvas
  to place them roughly; those positions are labelled as yours, not measured.
- **Geographic** (with a phone): needs at least 2 spots with phone accuracy of ±20 m or better.
  Accuracy circles are drawn; if they are large compared with the spacing between spots, the map says
  so. Accuracy is the median of the fixes, not shrunk by averaging, because consecutive fixes share
  most of their error.
- **Interpolated signal**: only offered with 4+ placed spots (and, for geographic, spacing at least 3× the
  position uncertainty). It fades out with distance from the nearest measurement; everything else stays
  hatched as unknown.

## Data and network exposure

- The laptop UI listens on `127.0.0.1` only and rejects foreign `Host` headers and non-JSON posts.
- The phone server starts only when you pair, listens on the Wi-Fi address, and requires the random
  token in the QR code. *Unpair* revokes it.
- Surveys are saved as JSON under `~/.local/share/wifispot/surveys/`. Nothing leaves the machine except
  pings to your router and to `1.1.1.1` once per second (`--internet-host ""` disables that).

## Limitations

- Signal in the Networks view is converted from NetworkManager's percentage and is approximate;
  the live signal comes from `/proc/net/wireless` in dBm.
- The phone flow was tested with an emulated phone in headless Chrome, not yet on a physical Android device.
- Only the connected link is measured. The tool cannot measure throughput to the internet or
  connections you are not associated with.
