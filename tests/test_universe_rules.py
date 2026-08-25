from a_stock.universe import classify_board, classify_security_status


def test_real_representative_codes_classify_by_code_and_market() -> None:
    assert classify_board("600519", 1) == ("sh_main", "SH", True)
    assert classify_board("000001", 0) == ("sz_main", "SZ", True)
    assert classify_board("300750", 0) == ("chinext", "SZ", True)
    assert classify_board("688981", 1) == ("star_market", "SH", True)
    assert classify_board("920000", 0) == ("bse", "BJ", True)


def test_market_field_mismatch_is_detected() -> None:
    assert classify_board("600519", 0) == ("sh_main", "SH", False)
    assert classify_board("000001", 1) == ("sz_main", "SZ", False)


def test_security_name_status_rules() -> None:
    assert classify_security_status("ST聆达") == "st"
    assert classify_security_status("*ST美丽") == "star_st"
    assert classify_security_status("退市庭B") == "delisting"
    assert classify_security_status("贵州茅台") == "normal"
