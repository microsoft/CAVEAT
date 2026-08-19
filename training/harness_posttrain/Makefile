.PHONY: validate test lint source-check install-cap64

validate:
	python3 -m harness_posttrain.cli validate-config --campaign configs/campaign.yaml

test:
	pytest

lint:
	ruff check src tests

source-check:
	./scripts/freeze_source.sh --check

install-cap64:
	./scripts/install_cap64_helper.sh
