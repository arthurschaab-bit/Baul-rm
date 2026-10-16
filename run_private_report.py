"""Run the public calculation core with a private local report profile."""
import argparse
import sys
import os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
from baul_rm import report_core

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',type=Path,required=True,help='Private local Python profile outside this checkout')
    parser.add_argument('--meteo-mode',choices=['ausweisen','ausschliessen'],default='ausweisen',help='Preferred variant; both variants are always required')
    args=parser.parse_args()
    os.environ['BAUL_RM_METEO_MODE']=args.meteo_mode
    result=report_core.load_private_profile(args.profile,Path(__file__).resolve().parent).run(report_core)
    from baul_rm.meteo_analysis import require_both_variants
    if result is None:
        raise ValueError('Profile must return its private output directory; both weather variants are required')
    require_both_variants(result)
