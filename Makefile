.PHONY: help backend liquidsoap build-frontend test-ui test-frontend test-liq test-functional

help:
	@echo "Available targets:"
	@echo "  backend           Run Flask backend (ui/app.py)"
	@echo "  liquidsoap        Run Liquidsoap with radio.liq"
	@echo "  build-frontend    Build React app and copy to ui/"
	@echo "  test-ui           Run Python UI tests"
	@echo "  test-frontend     Run frontend tests (Jest/RTL)"
	@echo "  test-liq          Validate Liquidsoap config"
	@echo "  test-functional   Run functional smoke tests"

backend:
	python ui/app.py

liquidsoap:
	liquidsoap radio.liq

build-frontend:
	cd radio-frontend && npm ci && npm run build && cp -R build/* ../ui/

test-ui:
	python ui/tests/run_tests.py

test-frontend:
	cd radio-frontend && npm test -- --watchAll=false

test-liq:
	python test_radio_liq.py

test-functional:
	bash test_radio_functional.sh
