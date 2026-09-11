.PHONY: test check

test:
	python3 -m unittest discover -s tests -v

check: test
	PYTHONPYCACHEPREFIX=.pycache python3 -m compileall -q firmware_manager tests
	python3 -m firmware_manager doctor
	git diff --check
