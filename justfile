build:
	@echo "Starting Build Process..." ;
	@rm -rf ./dist/* ;
	@python -m build > /dev/null 2>&1
	@PACKAGE=$(ls ./dist/*.whl 2>/dev/null | tail -n 1 || echo "") ; \
	echo "Built package: $PACKAGE"; \
	pip install --force "$PACKAGE" > /dev/null 2>&1; \
	echo "Installed package: $PACKAGE"

format:
	@echo "Formatting code" ;
	@black . 2>&1 | grep -E "reformatted|left unchanged"
	@pylint .
