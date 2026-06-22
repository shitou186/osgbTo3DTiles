PYTHON   := python3
VENV     := venv
PIP      := $(VENV)/bin/pip
PYTEST   := $(VENV)/bin/python -m pytest
PYTHONPATH := .

INPUT    ?=
OUTPUT   ?=

.PHONY: help setup clean test run

help:
	@echo "用法:"
	@echo "  make setup          创建虚拟环境并安装依赖"
	@echo "  make clean          清除 __pycache__ 和 .pytest_cache"
	@echo "  make test           运行测试"
	@echo "  make run INPUT=<dir> OUTPUT=<dir>  执行转换"
	@echo ""
	@echo "示例:"
	@echo "  make setup"
	@echo "  make test"
	@echo "  make run INPUT=/data/osgb OUTPUT=/data/3dtiles"

setup: $(VENV)/bin/activate

$(VENV)/bin/activate: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -q -r requirements.txt
	$(PIP) install -q pytest
	@touch $(VENV)/bin/activate

clean:
	rm -rf osgb2tiles/__pycache__ tests/__pycache__ .pytest_cache

test: setup clean
	PYTHONPATH=$(PYTHONPATH) $(PYTEST) tests/ -v

run: setup clean
	@test -n "$(INPUT)"  || (echo "错误: 请指定 INPUT=<osgb目录>" && exit 1)
	@test -n "$(OUTPUT)" || (echo "错误: 请指定 OUTPUT=<输出目录>" && exit 1)
	PYTHONPATH=$(PYTHONPATH) $(VENV)/bin/python -m osgb2tiles -i $(INPUT) -o $(OUTPUT)
