from signalscout import wifi

SCAN = r"""*:84\:F1\:47\:C9\:B0\:2D:IITJ_WLAN:52:5260 MHz:260 Mbit/s:20 MHz:68:WPA1 WPA2 802.1X
 :AE\:3B\:76\:E4\:6E\:7D:SRP:36:5180 MHz:270 Mbit/s:80 MHz:59:WPA2
 :84\:F1\:47\:CA\:59\:A2:IITJ_WLAN:6:2437 MHz:260 Mbit/s:20 MHz:64:WPA1 WPA2 802.1X
 :12\:34\:56\:78\:9A\:BC:a\:b\\c:11:2462 MHz:54 Mbit/s:20 MHz:20:
"""

PROC = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
wlp0s20f3: 0000   58.  -52.  -256        0      0      0      3  24534        7
"""

IWCONFIG = """wlp0s20f3  IEEE 802.11  ESSID:"IITJ_WLAN"
          Mode:Managed  Frequency:5.26 GHz  Access Point: 84:F1:47:C9:B0:2D
          Bit Rate=286.7 Mb/s   Tx-Power=8 dBm
          Power Management:on
          Link Quality=58/70  Signal level=-52 dBm
          Tx excessive retries:2  Invalid misc:24534   Missed beacon:0
"""


def test_split_terse_handles_escapes():
    assert wifi.split_terse(r"a\:b:c\\d:") == ["a:b", "c\\d", ""]


def test_parse_scan():
    aps = wifi.parse_scan(SCAN, wifi.SCAN_FIELDS)
    assert [a["signal_pct"] for a in aps] == [68, 64, 59, 20]          # sorted strongest first
    me = aps[0]
    assert me["in_use"] and me["bssid"] == "84:F1:47:C9:B0:2D" and me["band"] == "5"
    assert me["span"] == [5250, 5270] and me["signal_dbm"] == -59.2
    srp = next(a for a in aps if a["ssid"] == "SRP")
    assert srp["width"] == 80 and srp["span"] == [5170, 5250]
    odd = aps[-1]
    assert odd["ssid"] == "a:b\\c" and odd["security"] == "open"


def test_parse_scan_without_bandwidth_column():
    fields = [f for f in wifi.SCAN_FIELDS if f != "BANDWIDTH"]
    aps = wifi.parse_scan(" :AA\\:BB\\:CC\\:DD\\:EE\\:FF:x:1:2412 MHz:54 Mbit/s:50:WPA2\n", fields)
    assert aps[0]["width"] is None and aps[0]["span"] == [2402, 2422]


def test_channel_span_bonding():
    assert wifi.channel_span(5260, 52, 80) == (5250, 5330)    # block 52-64, centre 58
    assert wifi.channel_span(5745, 149, 80) == (5735, 5815)   # block 149-161, centre 155
    assert wifi.channel_span(5200, 40, 40) == (5170, 5210)    # pair 36/40, centre 38
    assert wifi.channel_span(5260, 52, 160) == (5170, 5330)   # block 36-64, centre 50
    assert wifi.channel_span(5975, 5, 80) == (5945, 6025)     # 6 GHz block 1-13, centre 7
    assert wifi.channel_span(2412, 1, 40) == (2402, 2442)     # HT40+ assumed for low channels


def test_parse_proc_wireless():
    s = wifi.parse_proc_wireless(PROC, "wlp0s20f3")
    assert s == {"quality": 58.0, "signal_dbm": -52.0, "noise_dbm": None,
                 "discard_retry": 3, "missed_beacon": 7}
    assert wifi.parse_proc_wireless(PROC, "wlan1") is None


def test_parse_iwconfig():
    s = wifi.parse_iwconfig(IWCONFIG)
    assert s == {"bssid": "84:F1:47:C9:B0:2D", "bitrate_mbps": 286.7, "tx_power_dbm": 8,
                 "tx_excessive_retries": 2, "power_save": True}
    assert wifi.parse_iwconfig("Bit Rate=1.2 Gb/s")["bitrate_mbps"] == 1200
