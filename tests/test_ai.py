from mychat import ai


def test_json_from_plain_and_fenced():
    assert ai._json_from('{"summary": "ok", "scores": {}}')["summary"] == "ok"
    assert ai._json_from('```json\n{"read": "hi", "replies": []}\n```')["read"] == "hi"


def test_json_from_prefers_expected_top_level_keys():
    text = 'noise {"scores": {"affection": 10}} tail {"summary": "s", "scores": {"affection": 80}}'
    result = ai._json_from(text, ("summary", "scores"))
    assert result["summary"] == "s"
    assert result["scores"]["affection"] == 80


def test_norm_perception_clamps_scores_and_drops_empty_signals():
    raw = {
        "summary": "  一段判断  ",
        "scores": {"friendliness": 120, "affection": -4, "aversion": "x"},
        "signals": [{"type": "positive", "label": "主动", "quote": "在吗"}, {"quote": ""}],
        "relations": [{"type": "朋友", "confidence": 200, "reason": "闲聊"}],
        "tags": ["朋友", "朋友", "  ", "直率"],
    }
    result = ai._norm_perception(raw, {"name": "A"}, 12)
    assert result["summary"] == "一段判断"
    assert result["scores"]["friendliness"] == 100
    assert result["scores"]["affection"] == 0
    assert result["scores"]["aversion"] == 50
    assert len(result["signals"]) == 1
    assert result["relations"][0]["confidence"] == 100
    assert result["tags"] == ["朋友", "直率"]


def test_normalize_scope_falls_back_to_default():
    assert ai.normalize_scope("week") == "week"
    assert ai.normalize_scope("nope") == ai.DEFAULT_SCOPE
    assert ai.normalize_scope(None) == ai.DEFAULT_SCOPE


def test_query_terms_skips_stopwords_and_dedupes():
    terms = ai._query_terms("我们第一次聊天是什么时候？")
    assert "我们" not in terms
    assert len(terms) == len(set(terms))
    assert terms
