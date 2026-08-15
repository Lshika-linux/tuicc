from tuicc.netinfo import _parse_ip_brief_output


def test_parse_ip_brief_output_extracts_address_strips_cidr():
    # Real captured output, this session's own machine (2026-08-15).
    output = "wlan0            UP             192.168.0.2/24 \n"

    assert _parse_ip_brief_output(output) == "192.168.0.2"


def test_parse_ip_brief_output_none_when_no_ipv4_assigned():
    # Interface exists (UP/DOWN column present) but has no third
    # column at all — still negotiating DHCP, or genuinely addressless.
    output = "wlan0            DOWN           \n"

    assert _parse_ip_brief_output(output) is None


def test_parse_ip_brief_output_none_on_empty_output():
    assert _parse_ip_brief_output("") is None
