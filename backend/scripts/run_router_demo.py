from BHELVIZ_FULL.backend.NLP.router import route_question

cases = [
    "What is the leave policy for employees?",
    "Show absent employees today",
    "Which absent employees violated the leave policy?",
]

for c in cases:
    print(c, '->', route_question(c))
