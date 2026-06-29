from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")


def download_finbert_model(
    model_name: str = "ProsusAI/finbert",
    output_dir: Path = Path("models") / "ProsusAI" / "finbert",
) -> Path:
    from transformers import BertForSequenceClassification, BertTokenizer

    output_dir.mkdir(parents=True, exist_ok=True)
    tokenizer = BertTokenizer.from_pretrained(model_name)
    model = BertForSequenceClassification.from_pretrained(model_name)
    tokenizer.save_pretrained(output_dir)
    model.save_pretrained(output_dir)
    return output_dir.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Download FinBERT locally for offline/news sentiment fallback use.")
    parser.add_argument("--model", default="ProsusAI/finbert", help="Hugging Face model id. Default: ProsusAI/finbert")
    parser.add_argument(
        "--output-dir",
        default=str(Path("models") / "ProsusAI" / "finbert"),
        help="Local output directory. Default: models/ProsusAI/finbert",
    )
    args = parser.parse_args()
    path = download_finbert_model(args.model, Path(args.output_dir))
    print(f"Downloaded {args.model} to {path}")
    print(f"Set FINBERT_LOCAL_MODEL_PATH={path}")


if __name__ == "__main__":
    main()
