"""Run the public calculation core with a private local report profile."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from baul_rm import report_core

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True,help='Private local Python profile outside this checkout')
    args=parser.parse_args()
    report_core.load_private_profile(args.profile,Path(__file__).resolve().parent).run(report_core)
