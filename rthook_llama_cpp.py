# PyInstaller runtime hook: fix llama_cpp DLL path.
# llama_cpp resolves its lib/ directory using __file__ at import time, which
# bakes in the build machine's path. Setting LLAMA_CPP_LIB_PATH overrides this.
import os
import sys

if hasattr(sys, '_MEIPASS'):
    os.environ['LLAMA_CPP_LIB_PATH'] = os.path.join(sys._MEIPASS, 'llama_cpp', 'lib')
