import sys, io

# Save real stderr/stdout before tools.py corrupts them
class FakeBuffer(io.StringIO):
    """A StringIO subclass that provides the full io.TextIOBase interface."""
    def fileno(self): return 2

# Suppress output during import to avoid tools.py I/O corruption
class SuppressOutput:
    def __init__(self):
        self.buffer = FakeBuffer()
        self.encoding = 'utf-8'
    def write(self, s): return len(s) if s else 0
    def flush(self): pass
    def fileno(self): return 2
    def readable(self): return False
    def writable(self): return True
    def seekable(self): return False
    def isatty(self): return False

saved_stderr = sys.stderr
saved_stdout = sys.stdout
sys.stderr = SuppressOutput()
sys.stdout = SuppressOutput()

results = {}

# Test each module
modules = ['config', 'search_engine', 'ui_styles', 'ui_components']
for m in modules:
    try:
        __import__(m)
        results[m] = 'OK'
    except Exception as e:
        results[m] = 'FAIL: ' + str(e)

modes = ['modes.qa', 'modes.quiz', 'modes.compare', 'modes.case', 'modes.agent']
for m in modes:
    try:
        __import__(m)
        results[m] = 'OK'
    except Exception as e:
        results[m] = 'FAIL: ' + str(e)

# Restore
sys.stderr = saved_stderr
sys.stdout = saved_stdout

for k, v in results.items():
    print(k + ': ' + v)
