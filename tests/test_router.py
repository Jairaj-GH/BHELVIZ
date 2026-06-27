from BHELVIZ_FULL.backend.NLP.router import route_question


def test_route_document():
    r = route_question("What is the leave policy for employees?")
    assert r["intent"] in ("document", "hybrid")


def test_route_structured():
    r = route_question("Show absent employees today")
    assert r["intent"] in ("structured", "hybrid")


def test_route_hybrid():
    r = route_question("Which absent employees violated the leave policy?")
    assert r["intent"] == "hybrid"
