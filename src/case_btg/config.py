from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / "Input"
OUTPUT_DIR = ROOT / "Output"
GOLDEN_PATH = ROOT / "golden_records" / "golden records.csv"
PROMPTS_DIR = ROOT / "prompts"
CACHE_DIR = ROOT / "cache" / "llm"

# Página com menos caracteres extraíveis que isso é tratada como imagem (scan).
MIN_TEXT_CHARS_PER_PAGE = 50
SCAN_RENDER_DPI = 200
