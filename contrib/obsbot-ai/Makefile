# Builds the optional helper against OBSBOT's SDK, which is not part of this project.
#
#   SDK_INCLUDE  folder that holds dev/devs.hpp   (default: ./include)
#   SDK_LIB      folder that holds libdev.so      (default: /usr/lib)
SDK_INCLUDE ?= include
SDK_LIB ?= /usr/lib
PREFIX ?= $(HOME)/.local

obsbot-ai: obsbot-ai.cpp
	$(CXX) -std=c++17 -O2 -I$(SDK_INCLUDE) $< -o $@ -L$(SDK_LIB) -ldev -lpthread -Wl,-rpath,$(SDK_LIB)

install: obsbot-ai
	install -Dm755 obsbot-ai $(PREFIX)/bin/obsbot-ai

clean:
	rm -f obsbot-ai

.PHONY: install clean
