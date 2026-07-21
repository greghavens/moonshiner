.PHONY: test incident

test:
	bash tests/test_mountrecover.sh

incident:
	@result_dir=$$(mktemp -d); \
	trap 'rm -rf "$$result_dir"' EXIT; \
	./mountrecover incident /srv/archive "$$result_dir"; \
	cat "$$result_dir/report.env"
