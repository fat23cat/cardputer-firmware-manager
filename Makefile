.DEFAULT_GOAL := help
APP ?= hub codex
WORKSPACE ?= $(abspath ..)
SD ?= /Volumes/CARDPUTER
APP_FLAGS = $(foreach app,$(APP),--app $(app))
MANAGER = python3 -m firmware_manager

.PHONY: help build flash stage release doctor doctor-sd
help:
	@printf '%s\n' 'make build [APP="hub codex brucecompact"] - build without SD' 'make flash [APP=hub] [SD=/Volumes/CARDPUTER] - build and stage on SD' 'make stage - stage existing builds on SD' 'make release - download and stage releases' 'make doctor / doctor-sd - validate layout / mounted SD' 'make check - manager tests and checks'

build:
	$(MANAGER) doctor
	$(MANAGER) build $(APP_FLAGS) --workspace "$(WORKSPACE)"

flash:
	$(MANAGER) doctor --sd "$(SD)"
	$(MANAGER) local $(APP_FLAGS) --workspace "$(WORKSPACE)" --sd "$(SD)" --build
	$(MANAGER) doctor --sd "$(SD)"

stage:
	$(MANAGER) doctor --sd "$(SD)"
	$(MANAGER) local $(APP_FLAGS) --workspace "$(WORKSPACE)" --sd "$(SD)"
	$(MANAGER) doctor --sd "$(SD)"

release:
	$(MANAGER) doctor --sd "$(SD)"
	$(MANAGER) release $(APP_FLAGS) --sd "$(SD)"
	$(MANAGER) doctor --sd "$(SD)"

doctor:
	$(MANAGER) doctor

doctor-sd:
	$(MANAGER) doctor --sd "$(SD)"

.PHONY: test check

test:
	python3 -m unittest discover -s tests -v

check: test
	PYTHONPYCACHEPREFIX=.pycache python3 -m compileall -q firmware_manager tests
	python3 -m firmware_manager doctor
	git diff --check
