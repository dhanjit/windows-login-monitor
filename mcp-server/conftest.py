"""Make the mcp-server directory importable in tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
