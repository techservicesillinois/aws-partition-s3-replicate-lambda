import os
from os.path import dirname, join
import sys

BASE = dirname(dirname(__file__))
sys.path.insert(0, join(BASE, 'build'))

for name in ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SECURITY_TOKEN', 'AWS_SESSION_TOKEN']:
    os.environ[name] = 'testing'
