.PHONY: build up down logs test shell

BUILD_ARGS :=

build:
	docker-compose build --no-cache

up:
	docker-compose up --build api nlp prometheus

down:
	docker-compose down --volumes --remove-orphans

logs:
	docker-compose logs -f

test:
	# Run backend tests inside the api container (installs requirements in image)
	docker-compose run --rm api bash -lc "pip install -r requirements.txt && pytest -q || true"

shell:
	docker-compose run --rm api bash
