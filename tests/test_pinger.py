from signalscout.monitor import Pinger


def make():
    return Pinger("192.0.2.1", start=False)


def test_replies_and_losses():
    p = make()
    p.feed("PING 192.0.2.1 (192.0.2.1) 56(84) bytes of data.\n", 100.0)
    p.feed("64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=3.10 ms\n", 100.0)
    p.feed("no answer yet for icmp_seq=2\n", 100.5)
    p.feed("64 bytes from 192.0.2.1: icmp_seq=3 ttl=64 time=4.00 ms\n", 100.75)
    p.feed("64 bytes from 192.0.2.1: icmp_seq=3 ttl=64 time=4.00 ms (DUP!)\n", 100.76)
    assert sorted(r for _, r in p.results) == [3.1, 4.0]
    p.feed("64 bytes from 192.0.2.1: icmp_seq=4 ttl=64 time=2.00 ms\n", 104.0)   # seq 2 has now timed out
    assert sorted(p.results, key=lambda x: x[0])[1][1] is None
    assert p.window(99, 105).count(None) == 1 and len(p.window(99, 105)) == 4
    assert p.error is None


def test_skipped_sequence_numbers_count_as_sent():
    p = make()
    p.feed("64 bytes from x: icmp_seq=1 ttl=64 time=1.0 ms\n", 10.0)
    p.feed("64 bytes from x: icmp_seq=4 ttl=64 time=1.0 ms\n", 11.0)
    p.feed("64 bytes from x: icmp_seq=5 ttl=64 time=1.0 ms\n", 20.0)
    assert [r for _, r in p.results].count(None) == 2


def test_errors_are_reported():
    p = make()
    p.feed("From 192.0.2.254 icmp_seq=1 Destination Host Unreachable\n", 5.0)
    assert "Unreachable" in p.error
    p.feed("ping: sendmsg: Network is unreachable\n", 5.1)
    assert "Network is unreachable" in p.error


def test_buckets():
    p = make()
    for i, (t, line) in enumerate([(10.1, "time=2.0"), (10.4, "time=4.0"), (11.2, "time=6.0")], start=1):
        p.feed(f"64 bytes from x: icmp_seq={i} ttl=64 {line} ms\n", t)
    b = p.buckets(0)
    assert [x["t"] for x in b] == [10, 11] and b[0]["rtt"] == 3.0 and b[0]["n"] == 2
