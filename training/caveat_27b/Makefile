.PHONY: validate lint source-check install-cap64

validate:
	python3 -m caveat_27b.cli validate-config --campaign configs/campaign.yaml

lint:
	ruff check src

source-check:
	./scripts/freeze_source.sh --check

install-cap64:
	./scripts/install_cap64_helper.sh
