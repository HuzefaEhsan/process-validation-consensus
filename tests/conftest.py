"""Put src/ on sys.path so the tests import the modules exactly as the pipeline does
(flat imports such as `import validation`), without modifying the audited modules."""
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
