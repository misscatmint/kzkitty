all:

clean:
	find . -not -path '*/.git/*' \
		\( -name '*,cover' -o -name __pycache__ \) -prune \
		-exec rm -rf '{}' ';'
	rm -rf .mypy_cache .ruff_cache
	rm -f .coverage

check: test

lint:
	ruff check
	ty check
	pyrefly check
	zuban check
	mypy .
	basedpyright

test:

.PHONY: all clean check lint test
