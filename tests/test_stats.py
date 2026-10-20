from mychat import stats


def test_trend_labels():
    assert stats.trend(30, 10)["label"] == "升温"
    assert stats.trend(5, 20)["label"] == "降温"
    assert stats.trend(10, 10)["label"] == "平稳"
    assert stats.trend(3, 2)["pct"] is None


def test_seconds_formatting():
    assert stats._fmt_seconds(None) == "—"
    assert stats._fmt_seconds(30) == "30 秒"
    assert stats._fmt_seconds(120) == "2 分钟"
    assert stats._fmt_seconds(7200) == "2.0 小时"


def test_clean_collapses_whitespace_and_truncates():
    assert stats._clean("  a\n\n b  ", 10) == "a b"
    assert stats._clean("x" * 20, 5) == "xxxxx"
