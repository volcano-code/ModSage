from modsage.catalog import classify, search_terms


def test_classification_matches_english_words_not_fragments():
    assert classify("Fixture Buildings", ["mod"]) == "other"
    assert classify("Fixture Units", ["mod"]) == "units"
    assert classify("Fixture UI Improvements", ["mod"]) == "ui"


def test_immortal_empires_does_not_expand_to_empire_faction():
    terms = search_terms("Immortal Empires")
    assert "帝国" not in terms
